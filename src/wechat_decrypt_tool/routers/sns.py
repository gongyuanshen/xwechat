from bisect import bisect_left, bisect_right
from ..account_workers import account_to_thread
from functools import lru_cache
from pathlib import Path
import base64
import hashlib
import json
import re
import httpx
import html # 修复&amp;转义的问题！！！
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Optional
from urllib.parse import urlparse

from starlette.background import BackgroundTask

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, FileResponse

from ..account_identity import resolve_account_self_username
from ..chat_helpers import _load_contact_rows, _pick_display_name, _resolve_account_dir
from ..logging_config import get_logger
from ..data_source import normalize_data_source
from ..snapshot_registry import resolve_account_database_dir
from ..media_helpers import _read_and_maybe_decrypt_media, _resolve_account_wxid_dir
from ..path_fix import PathFixRoute
from ..perf_trace import create_perf_trace
from .. import sns_media as _sns_media

try:
    import zstandard as zstd  # type: ignore
except Exception:
    zstd = None

logger = get_logger(__name__)

router = APIRouter(route_class=PathFixRoute)

_SNS_VIDEO_KEY_RE = re.compile(r'<enc\s+key="(\d+)"', flags=re.IGNORECASE)
_MP_BIZ_RE = re.compile(r"__biz=([A-Za-z0-9_=+-]+)")
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_SNS_APP_NAME_RE = re.compile(r"<appname[^>]*>([\s\S]*?)</appname>", flags=re.IGNORECASE)
_SNS_XML_CDATA_BLOCK_RE = re.compile(r"<!\[CDATA\[[\s\S]*?\]\]>", flags=re.IGNORECASE)
_SNS_XML_BARE_AMP_RE = re.compile(r"&(?!(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)")
_SNS_XML_INVALID_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


# Key: (account_dir.name, sorted(usernames), keyword) -> (expires_at_ts, force_sqlite)
_SNS_MEDIA_SLOW_LOOKUP_LIMITER = threading.BoundedSemaphore(4)
_SNS_INDEX_SCHEDULE_LOCK = threading.Lock()
_SNS_INDEX_SCHEDULED: set[str] = set()










def _parse_csv_list(raw: Optional[str]) -> list[str]:
    if raw is None:
        return []
    s = str(raw or "").strip()
    if not s:
        return []
    # Best-effort: allow comma-separated list in one query param.
    return [p.strip() for p in s.split(",") if p.strip()]


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0





def _to_unsigned_i64_str(v: Any) -> str:
    """Return unsigned decimal string for a signed/unsigned 64-bit integer-ish value.

    Moments `tid/id` is often an unsigned u64 stored as signed i64 (negative) in sqlite/WCDB.
    Frontend cache-key formulas expect the *unsigned* decimal string.
    """
    try:
        x = int(v)
    except Exception:
        return str(v or "").strip()
    return str(x & 0xFFFFFFFFFFFFFFFF)











def _extract_mp_biz_from_url(url: str) -> str:
    """Extract `__biz` from mp.weixin.qq.com URLs (best-effort)."""
    u = html.unescape(str(url or "")).replace("&amp;", "&").strip()
    if not u:
        return ""
    m = _MP_BIZ_RE.search(u)
    if not m:
        return ""
    return str(m.group(1) or "").strip()


@lru_cache(maxsize=16)
def _build_biz_to_official_index(contact_db_path: str, mtime_ns: int, size: int) -> dict[str, dict[str, Any]]:
    """Build mapping: __biz -> { username, serviceType } from contact.db.biz_info."""
    out: dict[str, dict[str, Any]] = {}
    if not contact_db_path:
        return out

    conn = sqlite3.connect(str(contact_db_path))
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                "SELECT username, brand_info, external_info, home_url FROM biz_info"
            ).fetchall()
        except Exception:
            rows = []

        for r in rows:
            try:
                uname = str(r["username"] or "").strip()
            except Exception:
                uname = ""
            if not uname:
                continue

            try:
                brand_info = str(r["brand_info"] or "")
            except Exception:
                brand_info = ""
            try:
                external_info = str(r["external_info"] or "")
            except Exception:
                external_info = ""
            try:
                home_url = str(r["home_url"] or "")
            except Exception:
                home_url = ""

            service_type: Optional[int] = None
            if external_info:
                try:
                    j = json.loads(external_info)
                    st = j.get("ServiceType")
                    if st is not None:
                        service_type = int(st)
                except Exception:
                    service_type = None

            blob = " ".join([brand_info, external_info, home_url])
            for biz in _MP_BIZ_RE.findall(blob):
                b = str(biz or "").strip()
                if not b:
                    continue
                prev = out.get(b)
                if prev is None:
                    out[b] = {"username": uname, "serviceType": service_type}
                else:
                    if prev.get("serviceType") is None and service_type is not None:
                        prev["serviceType"] = service_type
    finally:
        conn.close()

    return out


def _get_biz_to_official_index(contact_db_path: Path) -> dict[str, dict[str, Any]]:
    if not contact_db_path.exists():
        return {}
    st = contact_db_path.stat()
    mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
    return _build_biz_to_official_index(str(contact_db_path), mtime_ns, int(st.st_size))


def _extract_sns_video_key(raw_xml: Any) -> str:
    """Extract Isaac64 video key from raw XML, e.g. `<enc key="1578806206" ...>`."""
    text = _decode_sns_text_blob(raw_xml)
    m = _SNS_VIDEO_KEY_RE.search(text or "")
    return str(m.group(1) or "").strip() if m else ""


def _looks_like_xml_text(s: str) -> bool:
    if not s:
        return False
    t = str(s).lstrip()
    if t.startswith('"') and t.endswith('"'):
        t = t.strip('"').lstrip()
    return t.startswith("<")


def _sanitize_wechat_xml_for_et(xml_text: str) -> str:
    """Best-effort sanitize for ElementTree parsing.

    WeChat Moments "XML" is sometimes not well-formed XML (commonly: raw `&` inside URLs),
    which breaks `xml.etree.ElementTree.fromstring`. We keep CDATA blocks intact and:
    - strip invalid control chars
    - escape bare `&` outside CDATA blocks
    """

    s = str(xml_text or "")
    if not s:
        return ""

    s = _SNS_XML_INVALID_CHARS_RE.sub("", s)

    parts: list[str] = []
    last = 0
    for m in _SNS_XML_CDATA_BLOCK_RE.finditer(s):
        head = s[last : m.start()]
        if head:
            parts.append(_SNS_XML_BARE_AMP_RE.sub("&amp;", head))
        parts.append(m.group(0))
        last = m.end()

    tail = s[last:]
    if tail:
        parts.append(_SNS_XML_BARE_AMP_RE.sub("&amp;", tail))

    return "".join(parts)


def _decode_sns_text_blob(value: Any) -> str:
    """Decode text/blob values that may be hex/base64 encoded and/or zstd-compressed.

    Saved WeChat records can represent TEXT/BLOB fields as:
    - plain XML string
    - hex string (often a zstd frame starting with 28b52ffd...)
    - base64 string (same)
    """

    if value is None:
        return ""

    if isinstance(value, memoryview):
        raw = bytes(value)
        if raw and zstd is not None and raw.startswith(_ZSTD_MAGIC):
            try:
                raw = zstd.decompress(raw)
            except Exception:
                pass
        try:
            s = raw.decode("utf-8", errors="ignore")
        except Exception:
            s = ""
        s = html.unescape(str(s or "").strip())
        return s if _looks_like_xml_text(s) else (str(s or "").strip())

    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if raw and zstd is not None and raw.startswith(_ZSTD_MAGIC):
            try:
                raw = zstd.decompress(raw)
            except Exception:
                pass
        try:
            s = raw.decode("utf-8", errors="ignore")
        except Exception:
            s = ""
        s = html.unescape(str(s or "").strip())
        return s if _looks_like_xml_text(s) else (str(s or "").strip())

    try:
        text = str(value or "")
    except Exception:
        return ""

    text = html.unescape(text.strip())
    if not text:
        return ""

    if _looks_like_xml_text(text):
        return text

    def _accept_xml(decoded: str) -> str:
        s2 = html.unescape(str(decoded or "").strip())
        return s2 if _looks_like_xml_text(s2) else ""

    # Hex string (optionally prefixed with 0x)
    t_hex = text[2:] if text.lower().startswith("0x") else text
    if len(t_hex) >= 16 and len(t_hex) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", t_hex):
        try:
            raw = bytes.fromhex(t_hex)
            if raw and zstd is not None and raw.startswith(_ZSTD_MAGIC):
                try:
                    raw = zstd.decompress(raw)
                except Exception:
                    raw = b""
            if raw:
                s2 = _accept_xml(raw.decode("utf-8", errors="ignore"))
                if s2:
                    return s2
        except Exception:
            pass

    # Base64 string
    if len(text) >= 24 and len(text) % 4 == 0 and re.fullmatch(r"[A-Za-z0-9+/=]+", text):
        try:
            raw = base64.b64decode(text)
            if raw and zstd is not None and raw.startswith(_ZSTD_MAGIC):
                try:
                    raw = zstd.decompress(raw)
                except Exception:
                    raw = b""
            if raw:
                s2 = _accept_xml(raw.decode("utf-8", errors="ignore"))
                if s2:
                    return s2
        except Exception:
            pass

    return text




def _build_location_text(node: Optional[ET.Element]) -> str:
    if node is None:
        return ""

    def _get(key: str) -> str:
        return str(node.get(key) or node.findtext(key) or "").strip()

    def _clean(v: str) -> str:
        # Some WeChat XML uses special whitespace (NBSP / thin spaces) inside the location string.
        return (
            str(v or "")
            .replace("\u00a0", " ")
            .replace("\u2006", " ")
            .strip()
        )

    city = _clean(_get("city"))
    poi = _clean(_get("poiName") or _get("poi") or _get("label"))
    address = _clean(_get("address") or _get("poiAddress"))

    # Avoid duplicated city prefix like: "广安市·广安市·xxx".
    if city and poi and poi.startswith(city):
        rest = poi[len(city):].lstrip(" ·")
        if rest:
            poi = rest

    # WeChat UI typically renders `city·poi/address`.
    if city and (poi or address):
        return f"{city}·{poi or address}".strip()

    for cand in (poi, address, city):
        if cand:
            return cand
    return ""


def _parse_timeline_xml(xml_text: str, fallback_username: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "username": fallback_username,
        "createTime": 0,
        "contentDesc": "",
        "location": "",
        "sourceName": "",
        "media": [],
        "likes": [],
        "comments": [],
        "type": 1,  # 默认类型
        "title": "",
        "contentUrl": "",
        "finderFeed": {},
        "finderLive": {},
    }

    xml_str = _decode_sns_text_blob(xml_text)
    if not xml_str:
        return out


    try:
        root = ET.fromstring(_sanitize_wechat_xml_for_et(xml_str))
    except Exception:
        return out

    # External share source label (e.g. QQ音乐 / 哔哩哔哩) is usually stored in `<appInfo><appName>...`.
    try:
        for el in root.iter():
            try:
                tag = str(el.tag or "").lower()
            except Exception:
                continue
            if tag in {"appname", "sourcename"}:
                v = str(el.text or "").strip()
                if v:
                    out["sourceName"] = html.unescape(v).strip()
                    break
            try:
                attrs = el.attrib or {}
            except Exception:
                attrs = {}
            for k, v in attrs.items():
                if str(k or "").lower() in {"appname", "sourcename"}:
                    vv = str(v or "").strip()
                    if vv:
                        out["sourceName"] = html.unescape(vv).strip()
                        break
            if out["sourceName"]:
                break
    except Exception:
        pass

    def _find_text(*paths: str) -> str:
        for p in paths:
            try:
                v = root.findtext(p)
            except Exception:
                v = None
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""
    # &amp转义！！
    def _clean_url(u: str) -> str:
        if not u:
            return ""

        cleaned = html.unescape(u)
        cleaned = cleaned.replace("&amp;", "&")
        return cleaned.strip()

    out["username"] = _find_text(".//TimelineObject/username", ".//TimelineObject/user_name",
                                 ".//username") or fallback_username
    out["createTime"] = _safe_int(_find_text(".//TimelineObject/createTime", ".//createTime"))
    out["contentDesc"] = _find_text(".//TimelineObject/contentDesc", ".//contentDesc")
    out["location"] = _build_location_text(root.find(".//location"))

    # --- 提取内容类型 ---
    post_type = _safe_int(_find_text(".//ContentObject/type", ".//type"))
    out["type"] = post_type

    # --- 如果是公众号文章 (Type 3) ---
    if post_type == 3:
        out["title"] = _find_text(".//ContentObject/title")
        out["contentUrl"] = _clean_url(_find_text(".//ContentObject/contentUrl"))

    # --- 如果是外部分享链接 (Type 5) ---
    if post_type == 5:
        out["title"] = _find_text(
            ".//ContentObject/title",
            ".//ContentObject/linkTitle",
            ".//ContentObject/name",
            ".//ContentObject/desc",
            ".//ContentObject/description",
        )
        out["contentUrl"] = _clean_url(
            _find_text(
                ".//ContentObject/contentUrl",
                ".//ContentObject/linkUrl",
                ".//ContentObject/url",
                ".//ContentObject/jumpUrl",
            )
        )

    # --- 如果是音乐分享/链接卡片 (Type 42) ---
    if post_type == 42:
        # WeChat sometimes stores link/music share metadata under ContentObject fields.
        out["title"] = _find_text(
            ".//ContentObject/title",
            ".//ContentObject/linkTitle",
            ".//ContentObject/name",
            ".//ContentObject/desc",
        )
        out["contentUrl"] = _clean_url(
            _find_text(
                ".//ContentObject/contentUrl",
                ".//ContentObject/linkUrl",
                ".//ContentObject/url",
                ".//ContentObject/jumpUrl",
            )
        )

    # --- 如果是视频号 (Type 28) ---
    if post_type == 28:
        out["title"] = _find_text(".//ContentObject/title")
        out["contentUrl"] = _clean_url(_find_text(".//ContentObject/contentUrl"))
        out["finderFeed"] = {
            "nickname": _find_text(".//finderFeed/nickname"),
            "desc": _find_text(".//finderFeed/desc"),
            "thumbUrl": _clean_url(
                _find_text(".//finderFeed/mediaList/media/thumbUrl", ".//finderFeed/mediaList/media/coverUrl")),
            "url": _clean_url(_find_text(".//finderFeed/mediaList/media/url"))
        }

    if post_type == 34:
        out["finderLive"] = {
            "id": _find_text(".//finderLive/finderLiveID"),
            "username": _find_text(".//finderLive/finderUsername"),
            "objectId": _find_text(".//finderLive/finderObjectID"),
            "nonceId": _find_text(".//finderLive/finderNonceID"),
            "nickname": _find_text(".//finderLive/nickname"),
            "headUrl": _clean_url(_find_text(".//finderLive/headUrl")),
            "desc": _find_text(".//finderLive/desc"),
            "liveStatus": _safe_int(_find_text(".//finderLive/liveStatus")),
            "coverUrl": _clean_url(
                _find_text(".//finderLive/media/coverUrl", ".//finderLive/coverUrl")
            ),
            "width": _safe_int(_find_text(".//finderLive/media/width")),
            "height": _safe_int(_find_text(".//finderLive/media/height")),
        }

    media: list[dict[str, Any]] = []
    try:
        media_nodes = list(root.findall(".//mediaList//media"))
        media_nodes.extend(root.findall(".//finderLive/media"))
        for m in media_nodes:
            mt = _safe_int(m.findtext("type") or m.findtext("mediaType"))

            def _first_media_child(*names: str) -> Optional[ET.Element]:
                for name in names:
                    element = m.find(name)
                    if element is not None:
                        return element
                return None

            url_el = _first_media_child("url", "urlV", "coverUrl")
            thumb_el = _first_media_child("thumb", "thumbV", "thumbUrl", "coverUrl")

            url = _clean_url(url_el.text if url_el is not None else "")
            thumb = _clean_url(thumb_el.text if thumb_el is not None else "")

            url_attrs = dict(url_el.attrib) if url_el is not None and url_el.attrib else {}
            thumb_attrs = dict(thumb_el.attrib) if thumb_el is not None and thumb_el.attrib else {}
            media_id = str(m.findtext("id") or m.findtext("mediaId") or "").strip()
            size_el = m.find("size")
            size = dict(size_el.attrib) if size_el is not None and size_el.attrib else {}
            width = str(m.findtext("width") or "").strip()
            height = str(m.findtext("height") or "").strip()
            if width and not size.get("width"):
                size["width"] = width
            if height and not size.get("height"):
                size["height"] = height

            if not url and not thumb:
                continue

            media.append({
                "type": mt,
                "id": media_id,
                "url": url,
                "thumb": thumb,
                "urlAttrs": url_attrs,
                "thumbAttrs": thumb_attrs,
                "size": size,
            })
    except Exception:
        pass
    out["media"] = media

    # Fallback: some type=42 shares only expose the jump URL via media[0].url.
    if post_type in (5, 42):
        if (not str(out.get("contentUrl") or "").strip()) and media:
            m0 = media[0] if isinstance(media[0], dict) else {}
            u0 = str(m0.get("url") or "").strip()
            if u0:
                out["contentUrl"] = u0

    def _tag_lower(el: Optional[ET.Element]) -> str:
        if el is None:
            return ""
        try:
            return str(el.tag or "").strip().lower()
        except Exception:
            return ""

    def _direct_child_text(node: ET.Element, *names: str) -> str:
        wanted = {str(n or "").strip().lower() for n in names if str(n or "").strip()}
        if not wanted:
            return ""
        try:
            children = list(node)
        except Exception:
            children = []
        for child in children:
            if _tag_lower(child) not in wanted:
                continue
            v = str(child.text or "").strip()
            if v:
                return html.unescape(v).replace("&amp;", "&").strip()
        return ""

    def _has_descendant(node: ET.Element, *names: str) -> bool:
        wanted = {str(n or "").strip().lower() for n in names if str(n or "").strip()}
        if not wanted:
            return False
        try:
            return any(_tag_lower(el) in wanted for el in node.iter())
        except Exception:
            return False

    def _iter_comment_nodes() -> list[ET.Element]:
        nodes: list[ET.Element] = []
        seen: set[int] = set()
        comment_tags = {"comment", "commentuser", "user_comment", "commentitem"}
        for el in root.iter():
            if id(el) in seen:
                continue
            if _tag_lower(el) not in comment_tags:
                continue
            if not (
                _direct_child_text(el, "username", "user_name")
                or _direct_child_text(el, "content")
                or _direct_child_text(el, "nickname", "nickName", "displayName")
                or _has_descendant(el, "imageinfo")
            ):
                continue
            seen.add(id(el))
            nodes.append(el)
        return nodes

    def _parse_comment_images(comment_node: ET.Element) -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []
        try:
            image_nodes = [el for el in comment_node.iter() if _tag_lower(el) == "imageinfo"]
        except Exception:
            image_nodes = []

        for img in image_nodes:
            url = _clean_url(_direct_child_text(img, "url", "cdn_url", "origin_url", "originurl"))
            thumb_url = _clean_url(_direct_child_text(img, "thumb_url", "thumburl", "thumb", "thumb_url_v", "thumburlv"))
            token = _direct_child_text(img, "token")
            key = _direct_child_text(img, "key")
            enc_idx = _direct_child_text(img, "enc_idx", "encidx")
            thumb_token = _direct_child_text(img, "thumb_url_token", "thumb_token", "thumburltoken")
            thumb_key = _direct_child_text(img, "thumb_key", "thumbkey")
            thumb_enc_idx = _direct_child_text(img, "thumb_enc_idx", "thumbencidx")
            media_id = _direct_child_text(img, "media_id", "mediaid", "id")
            md5 = _direct_child_text(img, "md5")
            width = _safe_int(_direct_child_text(img, "width", "w"))
            height = _safe_int(_direct_child_text(img, "height", "h"))
            file_size = _safe_int(_direct_child_text(img, "file_size", "filesize", "total_size", "totalsize"))
            height_percentage = _safe_int(_direct_child_text(img, "height_percentage", "heightpercentage"))
            min_area = _safe_int(_direct_child_text(img, "min_area", "minarea"))

            if not url and not thumb_url:
                continue

            images.append(
                {
                    "type": 2,
                    "id": media_id,
                    "mediaId": media_id,
                    "url": url,
                    "thumb": thumb_url or url,
                    "thumbUrl": thumb_url,
                    "token": token,
                    "key": key,
                    "encIdx": enc_idx,
                    "thumbUrlToken": thumb_token,
                    "thumbKey": thumb_key,
                    "thumbEncIdx": thumb_enc_idx,
                    "md5": md5,
                    "width": width,
                    "height": height,
                    "heightPercentage": height_percentage,
                    "fileSize": file_size,
                    "minArea": min_area,
                    "urlAttrs": {
                        "token": token,
                        "key": key,
                        "enc_idx": enc_idx,
                        "md5": md5,
                    },
                    "thumbAttrs": {
                        "token": thumb_token,
                        "key": thumb_key,
                        "enc_idx": thumb_enc_idx,
                        "md5": md5,
                    },
                    "size": {
                        "width": width,
                        "height": height,
                        "totalSize": file_size,
                    },
                }
            )
        return images

    likes: list[str] = []
    try:
        for u in root.findall(".//likeList//like//username"):
            if u is None or not u.text:
                continue
            v = str(u.text).strip()
            if v:
                likes.append(v)
    except Exception:
        likes = []
    out["likes"] = likes

    comments: list[dict[str, Any]] = []
    try:
        for c in _iter_comment_nodes():
            content = _direct_child_text(c, "content")
            images = _parse_comment_images(c)
            if not content and not images:
                continue
            comments.append(
                {
                    "id": _direct_child_text(c, "cmtid", "commentId", "comment_id", "id"),
                    "username": _direct_child_text(c, "username", "user_name"),
                    "nickname": _direct_child_text(c, "nickName", "nickname", "displayName"),
                    "content": content,
                    "refUsername": _direct_child_text(c, "refUserName", "ref_username", "replyUsername", "reply_user_name"),
                    "refNickname": _direct_child_text(c, "refNickName", "refNickname", "replyNickname", "reply_nickname"),
                    "images": images,
                }
            )
    except Exception:
        comments = []
    out["comments"] = comments

    return out


@lru_cache(maxsize=16)
def _sns_video_roots(wxid_dir_str: str) -> tuple[str, ...]:
    """List all month cache roots that contain `Sns/Video`."""
    wxid_dir = Path(str(wxid_dir_str or "").strip())
    cache_root = wxid_dir / "cache"
    try:
        month_dirs = [p for p in cache_root.iterdir() if p.is_dir()]
    except Exception:
        month_dirs = []

    roots: list[str] = []
    for mdir in month_dirs:
        video_root = mdir / "Sns" / "Video"
        try:
            if video_root.exists() and video_root.is_dir():
                roots.append(str(video_root))
        except Exception:
            continue
    roots.sort()
    return tuple(roots)


def _image_size_from_bytes(data: bytes, media_type: str) -> tuple[int, int]:
    mt = str(media_type or "").lower()
    if mt == "image/png":
        if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
            try:
                w = int.from_bytes(data[16:20], "big")
                h = int.from_bytes(data[20:24], "big")
                return w, h
            except Exception:
                return 0, 0
        return 0, 0

    if mt in {"image/jpeg", "image/jpg"}:
        if len(data) < 4 or data[0:2] != b"\xff\xd8":
            return 0, 0
        i = 2
        n = len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            i += 2
            while marker == 0xFF and i < n:
                marker = data[i]
                i += 1
            if marker in {0xD8, 0xD9}:
                continue
            if i + 2 > n:
                return 0, 0
            seg_len = (data[i] << 8) + data[i + 1]
            i += 2
            if seg_len < 2 or i + seg_len - 2 > n:
                return 0, 0
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                if i + 4 < len(data):
                    try:
                        h = (data[i + 1] << 8) + data[i + 2]
                        w = (data[i + 3] << 8) + data[i + 4]
                        return w, h
                    except Exception:
                        return 0, 0
            i += seg_len - 2
        return 0, 0
    return 0, 0


@lru_cache(maxsize=16)
def _sns_img_roots(wxid_dir_str: str) -> tuple[str, ...]:
    """列出包含 `Sns/Img` 的月份缓存目录。"""
    wxid_dir = Path(str(wxid_dir_str or "").strip())
    cache_root = wxid_dir / "cache"
    try:
        month_dirs = [p for p in cache_root.iterdir() if p.is_dir()]
    except Exception:
        month_dirs = []

    roots: list[str] = []
    for mdir in month_dirs:
        img_root = mdir / "Sns" / "Img"
        try:
            if img_root.exists() and img_root.is_dir():
                roots.append(str(img_root))
        except Exception:
            continue
    roots.sort()
    return tuple(roots)


@lru_cache(maxsize=16)
def _sns_img_time_index(wxid_dir_str: str) -> tuple[list[float], list[str]]:
    """为朋友圈本地图片缓存构建按修改时间排序的索引。"""
    wxid_dir = Path(str(wxid_dir_str or "").strip())
    out: list[tuple[float, str]] = []

    cache_root = wxid_dir / "cache"
    try:
        month_dirs = [p for p in cache_root.iterdir() if p.is_dir()]
    except Exception:
        month_dirs = []

    for mdir in month_dirs:
        img_root = mdir / "Sns" / "Img"
        try:
            if not (img_root.exists() and img_root.is_dir()):
                continue
        except Exception:
            continue
        try:
            for sub in img_root.iterdir():
                if not sub.is_dir():
                    continue
                for f in sub.iterdir():
                    try:
                        if not f.is_file():
                            continue
                        st = f.stat()
                        out.append((float(st.st_mtime), str(f)))
                    except Exception:
                        continue
        except Exception:
            continue

    out.sort(key=lambda x: x[0])
    mtimes = [m for m, _p in out]
    paths = [_p for _m, _p in out]
    return mtimes, paths


def _normalize_hex32(value: Optional[str]) -> str:
    """提取前 32 位十六进制字符，不存在则返回空字符串。"""
    s = str(value or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"[^0-9a-f]", "", s)
    if len(s) < 32:
        return ""
    return s[:32]


def _generate_sns_cache_key(tid: str, media_id: str, media_type: int = 2) -> str:
    if not tid or not media_id:
        return ""
    raw_key = f"{tid}_{media_id}_{media_type}"
    try:
        return hashlib.md5(raw_key.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def _resolve_sns_cached_image_path_by_cache_key(
    *,
    wxid_dir: Path,
    cache_key: str,
    create_time: int,
) -> Optional[str]:
    key32 = _normalize_hex32(cache_key)
    if not key32:
        return None

    sub = key32[:2]
    rest = key32[2:]
    roots = _sns_img_roots(str(wxid_dir))
    if not roots:
        return None

    best: tuple[float, str] | None = None
    for root_str in roots:
        try:
            p = Path(root_str) / sub / rest
            if not (p.exists() and p.is_file()):
                continue
            st = p.stat()
            score = abs(float(st.st_mtime) - float(create_time)) if create_time > 0 else -float(st.st_mtime)
            if best is None or score < best[0]:
                best = (score, str(p))
        except Exception:
            continue
    return best[1] if best else None


def _resolve_sns_cached_image_path_by_md5(
    *,
    wxid_dir: Path,
    md5: str,
    create_time: int,
) -> Optional[str]:
    md5_32 = _normalize_hex32(md5)
    if not md5_32:
        return None

    sub = md5_32[:2]
    rest = md5_32[2:]
    roots = _sns_img_roots(str(wxid_dir))
    if not roots:
        return None

    best: tuple[float, str] | None = None
    for root_str in roots:
        try:
            p = Path(root_str) / sub / rest
            if not (p.exists() and p.is_file()):
                continue
            st = p.stat()
            score = abs(float(st.st_mtime) - float(create_time)) if create_time > 0 else -float(st.st_mtime)
            if best is None or score < best[0]:
                best = (score, str(p))
        except Exception:
            continue
    return best[1] if best else None


@lru_cache(maxsize=4096)
def _resolve_sns_cached_image_path(
    *,
    account_dir_str: str,
    create_time: int,
    width: int,
    height: int,
    idx: int,
    total_size: int = 0,
) -> Optional[str]:
    """根据朋友圈动态和媒体元数据尽力匹配本地图片缓存。"""
    total_size_i = int(total_size or 0)
    must_match_size = width > 0 and height > 0
    if (not must_match_size) and total_size_i <= 0:
        return None

    account_dir = Path(str(account_dir_str or "").strip())
    if not account_dir.exists():
        return None

    wxid_dir = _resolve_account_wxid_dir(account_dir)
    if not wxid_dir:
        return None

    mtimes, paths = _sns_img_time_index(str(wxid_dir))
    if not mtimes:
        return None

    create_time_i = int(create_time or 0)
    if create_time_i > 0:
        window = 72 * 3600
        lo = create_time_i - window
        hi = create_time_i + window
        l = bisect_left(mtimes, lo)
        r = bisect_right(mtimes, hi)
        if l >= r:
            # 发布时间附近没有候选时不能退化成“最近 800 张”全局猜图：
            # 老动态很容易因此命中多年后的同尺寸图片，造成缩略图与原图串图。
            return None
    else:
        l = max(0, len(mtimes) - 800)
        r = len(mtimes)

    candidates: list[tuple[int, float, str]] = []
    for j in range(l, r):
        try:
            path = Path(paths[j])
            size_diff = abs(int(path.stat().st_size) - total_size_i) if total_size_i > 0 else 0
            if create_time_i > 0:
                candidates.append((size_diff, abs(mtimes[j] - float(create_time_i)), paths[j]))
            else:
                candidates.append((size_diff, -mtimes[j], paths[j]))
        except Exception:
            continue
    candidates.sort(key=lambda x: (x[0], x[1], x[2]))

    # 旧版缓存没有稳定 key 时才会进入这里。先利用文件大小和时间收窄范围，
    # 避免一张图在请求线程里解密上千个候选文件。
    if total_size_i > 0:
        tolerance = max(4096, int(total_size_i * 0.05))
        close_candidates = [item for item in candidates if item[0] <= tolerance]
        if close_candidates:
            candidates = close_candidates
    candidates = candidates[:128]

    matched: list[tuple[int, float, str]] = []
    for _stat_size_diff, diff, pstr in candidates:
        try:
            p = Path(pstr)
            payload, media_type = _read_and_maybe_decrypt_media(p, account_dir)
            if not payload or not str(media_type or "").startswith("image/"):
                continue
            if must_match_size:
                w0, h0 = _image_size_from_bytes(payload, str(media_type or ""))
                if (w0, h0) != (width, height):
                    continue
            size_diff = abs(len(payload) - total_size_i) if total_size_i > 0 else 0
            matched.append((int(size_diff), float(diff), pstr))
        except Exception:
            continue

    if not matched:
        return None
    if must_match_size:
        matched.sort(key=lambda x: (x[0], x[1], x[2]))
        if total_size_i > 0:
            return matched[0][2]
        idx0 = max(0, int(idx or 0))
        return matched[idx0][2] if idx0 < len(matched) else None
    if total_size_i > 0:
        matched.sort(key=lambda x: (x[0], x[1], x[2]))
        return matched[0][2]
    return None


def _resolve_sns_cached_image_path_bounded(**kwargs: Any) -> Optional[str]:
    """限制高成本旧缓存匹配的并发，避免磁盘和解密任务互相放大。"""
    with _SNS_MEDIA_SLOW_LOOKUP_LIMITER:
        return _resolve_sns_cached_image_path(**kwargs)


def _resolve_sns_cached_video_path(
    wxid_dir: Path,
    post_id: str,
    media_id: str
) -> Optional[str]:
    """基于逆向出的固定盐值 3，解析朋友圈视频的本地缓存路径"""
    if not post_id or not media_id:
        return None

    raw_key = f"{post_id}_{media_id}_3"  # 暂时硬编码，大概率是对的
    try:
        key32 = hashlib.md5(raw_key.encode("utf-8")).hexdigest()
    except Exception:
        return None

    sub = key32[:2]
    rest = key32[2:]

    roots = _sns_video_roots(str(wxid_dir))
    for root_str in roots:
        try:
            base_path = Path(root_str) / sub / rest
            for ext in [".mp4", ".tmp"]:
                p = base_path.with_suffix(ext)
                if p.exists() and p.is_file():
                    return str(p)
        except Exception:
            continue

    return None

def _get_sns_covers(account_dir: Path, target_wxid: str, limit: int=20) -> list[dict[str, Any]]:
    """无论多古老，强行揪出用户的朋友圈封面历史 (type=7)。

    返回倒序（最新在前）的列表，包含 createTime 便于前端叠加显示。
    """
    wxid = str(target_wxid or "").strip()
    if not wxid:
        return []

    try:
        lim = int(limit or 20)
    except Exception:
        lim = 20
    if lim <= 0:
        lim = 1
    # Keep payload bounded; cover history isn't worth huge queries.
    if lim > 50:
        lim = 50

    wxid_esc = wxid.replace("'", "''")
    cover_sql = (
        "SELECT tid, content FROM SnsTimeLine "
        f"WHERE user_name = '{wxid_esc}' AND content LIKE '%<type>7</type>%' "
        "ORDER BY tid DESC "
        f"LIMIT {lim}"
    )

    rows: list[dict[str, Any]] = []

    # 1) Prefer real-time WCDB if available (reads db_storage/sns/sns.db).

    # 2) Fallback to local decrypted snapshot sns.db.
    if not rows:
        sns_db_path = resolve_account_database_dir(account_dir) / "sns.db"
        if sns_db_path.exists():
            try:
                # 只读模式防止锁死
                conn_sq = sqlite3.connect(f"file:{sns_db_path}?mode=ro", uri=True)
                conn_sq.row_factory = sqlite3.Row
                rows_sq = conn_sq.execute(cover_sql).fetchall()
                conn_sq.close()
                rows = [{"tid": r["tid"], "content": r["content"]} for r in (rows_sq or [])]
            except Exception as e:
                logger.warning("[sns] SQLite cover fetch failed: %s", e)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rr in rows:
        if not isinstance(rr, dict):
            continue
        cover_xml = rr.get("content")
        if not cover_xml:
            continue

        try:
            cover_tid = int(rr.get("tid") or 0)
        except Exception:
            cover_tid = 0

        parsed = _parse_timeline_xml(str(cover_xml or ""), wxid)
        media = parsed.get("media") or []
        if not isinstance(media, list) or not media:
            continue

        cid = _to_unsigned_i64_str(cover_tid or "")
        if cid in seen:
            continue
        seen.add(cid)

        out.append(
            {
                "id": cid,
                "tid": cover_tid,
                "username": wxid,
                "createTime": int(parsed.get("createTime") or 0),
                "media": media,
                "type": 7,
            }
        )
    return out


@router.get("/api/sns/self_info", summary="获取个人信息（wxid和nickname）")
def api_sns_self_info(account: Optional[str] = None, source: str = "auto"):

    account_dir = _resolve_account_dir(account)
    wxid = resolve_account_self_username(account_dir)
    normalize_data_source(source)

    logger.info(f"[self_info] 开始获取账号信息, 预设 wxid: {wxid}")

    nickname = wxid
    result_source = "wxid_dir"


    contact_db_path = resolve_account_database_dir(account_dir) / "contact.db"
    if contact_db_path.exists():
        conn = None
        try:
            db_uri = f"file:{contact_db_path}?mode=ro"
            conn = sqlite3.connect(db_uri, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row

            cursor = conn.execute("PRAGMA table_info(contact)")
            cols = {row["name"].lower() for row in cursor.fetchall()}
            logger.debug(f"[self_info] contact 表现有字段: {cols}")

            target_nick_col = "nick_name" if "nick_name" in cols else ("nickname" if "nickname" in cols else None)

            if target_nick_col:
                sql = f"SELECT remark, {target_nick_col} as nickname_val, alias FROM contact WHERE username = ? LIMIT 1"
                row = conn.execute(sql, (wxid,)).fetchone()


                if row:
                    raw_remark = str(row["remark"] or "").strip() if "remark" in row.keys() else ""
                    raw_nick = str(row["nickname_val"] or "").strip()
                    raw_alias = str(row["alias"] or "").strip() if "alias" in row.keys() else ""

                    if raw_remark:
                        nickname = raw_remark
                        result_source = "contact_db_remark"
                    elif raw_nick:
                        nickname = raw_nick
                        result_source = "contact_db_nickname"
                    elif raw_alias:
                        nickname = raw_alias
                        result_source = "contact_db_alias"

                    logger.info(f"[self_info] 从数据库提取成功: {nickname} (src: {result_source})")
            else:
                logger.warning("[self_info] contact 表中找不到任何昵称相关字段")

        except sqlite3.OperationalError as e:
            logger.error(f"[self_info] 数据库繁忙或锁定: {e}")
        except Exception as e:
            logger.exception(f"[self_info] 查询异常: {e}")
        finally:
            if conn: conn.close()
    else:
        logger.warning(f"[self_info] 找不到 contact.db: {contact_db_path}")

    return {
        "wxid": wxid,
        "nickname": nickname,
        "source": result_source
    }


def _build_sns_snapshot_status(account_dir: Path) -> dict[str, Any]:
    account_dir = Path(account_dir)
    sns_db_path = resolve_account_database_dir(account_dir) / "sns.db"
    candidates = [sns_db_path, Path(f"{sns_db_path}-wal")]
    file_stats: list[tuple[str, int, int]] = []
    for candidate in candidates:
        try:
            stat = candidate.stat()
        except FileNotFoundError:
            continue
        file_stats.append((candidate.name, int(stat.st_mtime_ns), int(stat.st_size)))

    db_available = any(name == "sns.db" for name, _mtime, _size in file_stats)
    if not db_available:
        return {"status": "ok", "available": False, "version": "", "mtimeNs": 0, "size": 0}

    version_payload = json.dumps([str(sns_db_path), file_stats], ensure_ascii=True, separators=(",", ":"))
    version = hashlib.sha256(version_payload.encode("utf-8")).hexdigest()[:24]
    return {
        "status": "ok",
        "available": True,
        "version": version,
        "mtimeNs": max((mtime for _name, mtime, _size in file_stats), default=0),
        "size": sum(size for _name, _mtime, size in file_stats),
    }


@router.get("/api/sns/snapshot/status", summary="获取朋友圈本地快照版本")
def get_sns_snapshot_status(account: Optional[str] = None):
    """Return the local snapshot version."""
    account_dir = _resolve_account_dir(account)
    return _build_sns_snapshot_status(account_dir)














@router.get("/api/sns/timeline", summary="获取朋友圈时间线")
def list_sns_timeline(
    account: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    usernames: Optional[str] = None,
    keyword: Optional[str] = None,
    source: str = "auto",
):
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Invalid limit.")
    if limit > 200:
        limit = 200
    if offset < 0:
        offset = 0

    account_dir = _resolve_account_dir(account)
    contact_db_path = resolve_account_database_dir(account_dir) / "contact.db"

    normalize_data_source(source)

    users = _parse_csv_list(usernames)
    kw = str(keyword or "").strip()

    cover_data = None
    covers_data: list[dict[str, Any]] = []
    if offset == 0:
        target_wxid = users[0] if users else resolve_account_self_username(account_dir)
        covers_data = _get_sns_covers(
            account_dir,
            target_wxid,
            limit=20,

        )
        cover_data = covers_data[0] if covers_data else None

    def _list_from_decrypted_sqlite() -> dict[str, Any]:
        """Legacy path: query the decrypted sns.db under output/databases/{account}.

        Note: This path may contain historical timeline items that are no longer
        visible in WeChat due to privacy settings (e.g. "only last 3 days").
        """
        sns_db_path = resolve_account_database_dir(account_dir) / "sns.db"
        if not sns_db_path.exists():
            raise HTTPException(status_code=404, detail="sns.db not found for this account.")

        filters: list[str] = []
        params: list[Any] = []

        if users:
            placeholders = ",".join(["?"] * len(users))
            filters.append(f"user_name IN ({placeholders})")
            params.extend(users)

        if kw:
            filters.append("content LIKE ?")
            params.append(f"%{kw}%")

        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""

        sql = f"""
            SELECT tid, user_name, content
            FROM SnsTimeLine
            {where_sql}
            ORDER BY tid DESC
            LIMIT ? OFFSET ?
        """
        # Fetch 1 extra row to determine hasMore.
        params_with_page = params + [limit + 1, offset]

        conn2 = sqlite3.connect(f"{sns_db_path.as_uri()}?mode=ro", uri=True)
        conn2.row_factory = sqlite3.Row
        try:
            rows2 = conn2.execute(sql, params_with_page).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning("[sns] query failed: %s", e)
            raise HTTPException(status_code=500, detail=f"sns.db query failed: {e}")
        finally:
            conn2.close()

        has_more2 = len(rows2) > limit
        rows2 = rows2[:limit]

        post_usernames2 = [str(r["user_name"] or "").strip() for r in rows2 if str(r["user_name"] or "").strip()]
        contact_rows2 = _load_contact_rows(contact_db_path, post_usernames2) if contact_db_path.exists() else {}
        biz_index2 = _get_biz_to_official_index(contact_db_path) if contact_db_path.exists() else {}
        official_usernames2: set[str] = set()

        timeline2: list[dict[str, Any]] = []
        for r in rows2:
            try:
                tid2 = r["tid"]
            except Exception:
                tid2 = None
            uname2 = str(r["user_name"] or "").strip()

            content_xml = str(r["content"] or "")
            parsed2 = _parse_timeline_xml(content_xml, uname2)

            # Best-effort: attach ISAAC64 video key for SNS videos/live-photos (WeFlow compatible).
            video_key2 = _extract_sns_video_key(content_xml)
            if video_key2:
                pmedia2 = parsed2.get("media")
                if isinstance(pmedia2, list):
                    for m0 in pmedia2:
                        if not isinstance(m0, dict):
                            continue
                        if "videoKey" not in m0:
                            m0["videoKey"] = video_key2
                        lp = m0.get("livePhoto")
                        if isinstance(lp, dict):
                            if not str(lp.get("key") or "").strip():
                                lp["key"] = video_key2

            display2 = _pick_display_name(contact_rows2.get(uname2), uname2) if uname2 else uname2
            post_type2 = int(parsed2.get("type", 1) or 1)

            official2: dict[str, Any] = {}
            if post_type2 == 3:
                content_url2 = str(parsed2.get("contentUrl") or "")
                biz2 = _extract_mp_biz_from_url(content_url2)
                info2 = biz_index2.get(biz2) if biz2 else None
                off_username2 = str(info2.get("username") or "").strip() if isinstance(info2, dict) else ""
                off_service_type2 = info2.get("serviceType") if isinstance(info2, dict) else None
                official2 = {
                    "biz": biz2,
                    "username": off_username2,
                    "serviceType": off_service_type2,
                    "displayName": "",
                }
                if off_username2:
                    official_usernames2.add(off_username2)

            timeline2.append(
                {
                    "id": _to_unsigned_i64_str(tid2) if tid2 is not None else (str(parsed2.get("createTime") or "") or uname2),
                    "tid": tid2,
                    "username": uname2 or parsed2.get("username") or "",
                    "displayName": display2,
                    "createTime": int(parsed2.get("createTime") or 0),
                    "contentDesc": str(parsed2.get("contentDesc") or ""),
                    "location": str(parsed2.get("location") or ""),
                    "sourceName": str(parsed2.get("sourceName") or ""),
                    "media": parsed2.get("media") or [],
                    "likes": parsed2.get("likes") or [],
                    "comments": parsed2.get("comments") or [],
                    "type": post_type2,
                    "title": parsed2.get("title", ""),
                    "contentUrl": parsed2.get("contentUrl", ""),
                    "finderFeed": parsed2.get("finderFeed", {}),
                    "finderLive": parsed2.get("finderLive", {}),
                    "official": official2,
                }
            )

        if official_usernames2 and contact_db_path.exists():
            official_rows2 = _load_contact_rows(contact_db_path, list(official_usernames2))
            for item in timeline2:
                off2 = item.get("official")
                if not isinstance(off2, dict):
                    continue
                u0_2 = str(off2.get("username") or "").strip()
                if not u0_2:
                    continue
                row2 = official_rows2.get(u0_2)
                if row2 is None:
                    continue
                off2["displayName"] = str(_pick_display_name(row2, u0_2)).strip()

        return {
            "timeline": timeline2,
            "snapshotGeneration": sns_db_path.parent.parents[1].name if sns_db_path.parent != account_dir else "legacy",
            "hasMore": has_more2,
            "limit": limit,
            "offset": offset,
            "source": "sqlite",
            "cover": cover_data,
            "covers": covers_data,
        }

    decrypted = _list_from_decrypted_sqlite()
    decrypted["source"] = "decrypted"
    return decrypted



def _sns_file_version(path: Path) -> tuple[int, int, int, int]:
    """返回主文件和 WAL 的轻量版本，用于联系人统计缓存失效。"""
    values: list[int] = []
    for candidate in (Path(path), Path(f"{path}-wal")):
        try:
            stat = candidate.stat()
            values.extend((int(stat.st_mtime_ns), int(stat.st_size)))
        except Exception:
            values.extend((0, 0))
    return values[0], values[1], values[2], values[3]


def _schedule_sns_user_tid_index(sns_db_path: Path) -> None:
    """在后台补齐联系人时间线索引，不让首次页面查询等待建索引。"""
    from ..snapshot_registry import start_snapshot_thread, legacy_database_write
    key = str(Path(sns_db_path).resolve())
    with _SNS_INDEX_SCHEDULE_LOCK:
        if key in _SNS_INDEX_SCHEDULED:
            return
        _SNS_INDEX_SCHEDULED.add(key)

    def worker() -> None:
        try:
            with legacy_database_write(sns_db_path.parent):
                conn = sqlite3.connect(str(sns_db_path), timeout=2)
                try:
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_sns_timeline_user_tid "
                        "ON SnsTimeLine(user_name, tid DESC)"
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:
            logger.info("[sns] background index creation deferred: %s", exc)
            with _SNS_INDEX_SCHEDULE_LOCK:
                _SNS_INDEX_SCHEDULED.discard(key)

    try:
        start_snapshot_thread(sns_db_path.parent, worker,
                              name=f"sns-index-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:8]}")
    except BaseException:
        with _SNS_INDEX_SCHEDULE_LOCK:
            _SNS_INDEX_SCHEDULED.discard(key)
        raise


@lru_cache(maxsize=16)
def _load_sns_users_cached(
    sns_db_path: str,
    _sns_mtime_ns: int,
    _sns_size: int,
    _sns_wal_mtime_ns: int,
    _sns_wal_size: int,
    contact_db_path: str,
    _contact_mtime_ns: int,
    _contact_size: int,
    _contact_wal_mtime_ns: int,
    _contact_wal_size: int,
) -> tuple[tuple[str, str, int], ...]:
    """按数据库版本缓存完整联系人统计，搜索和 limit 在外层处理。"""
    conn = sqlite3.connect(str(sns_db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
              user_name AS username,
              SUM(
                CASE
                  WHEN content IS NOT NULL AND content != '' AND content NOT LIKE '%<type>7</type>%'
                  THEN 1 ELSE 0
                END
              ) AS postCount,
              COUNT(*) AS totalCount
            FROM SnsTimeLine
            GROUP BY user_name
            ORDER BY postCount DESC, totalCount DESC
            """
        ).fetchall() or []
    finally:
        conn.close()

    usernames = [str(row["username"] or "").strip() for row in rows if row is not None]
    usernames = [username for username in usernames if username]
    contact_path = Path(contact_db_path)
    contact_rows = _load_contact_rows(contact_path, usernames) if contact_path.exists() else {}

    result: list[tuple[str, str, int]] = []
    for row in rows:
        username = str(row["username"] or "").strip()
        if not username:
            continue
        try:
            post_count = int(row["postCount"] or 0)
        except Exception:
            post_count = 0
        if post_count <= 0:
            continue
        display = str(_pick_display_name(contact_rows.get(username), username) or "")
        display = display.replace("\xa0", " ").strip() or username
        result.append((username, display, post_count))
    return tuple(result)


@router.get("/api/sns/users", summary="列出朋友圈联系人（按发圈数统计）")
def list_sns_users(
    account: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 5000,
):
    account_dir = _resolve_account_dir(account)
    database_dir = resolve_account_database_dir(account_dir)
    sns_db_path = database_dir / "sns.db"
    if not sns_db_path.exists():
        raise HTTPException(status_code=404, detail="sns.db not found for this account.")
    # Published generations remain immutable, including read-side CREATE INDEX.
    if database_dir == account_dir:
        _schedule_sns_user_tid_index(sns_db_path)

    contact_db_path = database_dir / "contact.db"

    try:
        lim = int(limit or 5000)
    except Exception:
        lim = 5000
    if lim <= 0:
        lim = 5000
    if lim > 5000:
        lim = 5000

    kw = str(keyword or "").strip().lower()
    sns_version = _sns_file_version(sns_db_path)
    contact_version = _sns_file_version(contact_db_path)
    cached_items = _load_sns_users_cached(
        str(sns_db_path),
        *sns_version,
        str(contact_db_path),
        *contact_version,
    )

    items: list[dict[str, Any]] = []
    for uname, display, post_count in cached_items:
        if kw and kw not in uname.lower() and kw not in display.lower():
            continue
        items.append({"username": uname, "displayName": display, "postCount": post_count})
        if len(items) >= lim:
            break

    return {"items": items, "count": len(items), "limit": lim}


def _is_allowed_sns_media_host(host: str) -> bool:
    return _sns_media.is_allowed_sns_media_host(host)


def _fix_sns_cdn_url(
    url: str,
    *,
    token: str = "",
    is_video: bool = False,
    force_original: bool = False,
) -> str:
    return _sns_media.fix_sns_cdn_url(
        url,
        token=token,
        is_video=is_video,
        force_original=force_original,
    )


_SNS_REMOTE_VIDEO_CACHE_EXTS = [
    ".mp4",
    ".bin",  # legacy/unknown
]


def _sns_remote_video_cache_dir_and_stem(account_dir: Path, *, url: str, key: str) -> tuple[Path, str]:
    digest = hashlib.md5(f"video|{url}|{key}".encode("utf-8", errors="ignore")).hexdigest()
    cache_dir = account_dir / "sns_remote_video_cache" / digest[:2]
    return cache_dir, digest


def _sns_remote_video_cache_existing_path(cache_dir: Path, stem: str) -> Optional[Path]:
    for ext in _SNS_REMOTE_VIDEO_CACHE_EXTS:
        p = cache_dir / f"{stem}{ext}"
        try:
            if p.exists() and p.is_file():
                return p
        except Exception:
            continue
    return None


async def _download_sns_remote_to_file(url: str, dest_path: Path, *, max_bytes: int) -> tuple[str, str]:
    """Download SNS media to file (streaming) from Tencent CDN.

    Returns: (content_type, x_enc)
    """
    u = str(url or "").strip()
    if not u:
        return "", ""

    # Safety: only allow Tencent CDN hosts.
    try:
        p = urlparse(u)
        host = str(p.hostname or "").lower()
        if not _is_allowed_sns_media_host(host):
            raise HTTPException(status_code=400, detail="SNS media host not allowed.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid SNS media URL.")

    base_headers = {
        "User-Agent": "MicroMessenger Client",
        "Accept": "*/*",
        # Do not request compression for video streams.
        "Connection": "keep-alive",
    }

    header_variants = [
        {},
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63090719) XWEB/8351",
            "Referer": "https://servicewechat.com/",
            "Origin": "https://servicewechat.com",
        },
        {"Referer": "https://wx.qq.com/", "Origin": "https://wx.qq.com"},
        {"Referer": "https://mp.weixin.qq.com/", "Origin": "https://mp.weixin.qq.com"},
    ]

    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for extra in header_variants:
            headers = dict(base_headers)
            headers.update(extra)
            try:
                if dest_path.exists():
                    try:
                        dest_path.unlink(missing_ok=True)
                    except Exception:
                        pass

                total = 0
                async with client.stream("GET", u, headers=headers) as resp:
                    resp.raise_for_status()
                    content_type = str(resp.headers.get("Content-Type") or "").strip()
                    x_enc = str(resp.headers.get("x-enc") or "").strip()
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with dest_path.open("wb") as f:
                        async for chunk in resp.aiter_bytes():
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > max_bytes:
                                raise HTTPException(status_code=400, detail="SNS video too large.")
                            f.write(chunk)
                return content_type, x_enc
            except HTTPException:
                raise
            except Exception as e:
                last_err = e
                continue

    raise last_err or RuntimeError("sns remote download failed")


async def _materialize_sns_remote_video(
    *,
    account_dir: Path,
    url: str,
    key: str,
    token: str,
    use_cache: bool,
    diagnostic_id: str = "",
) -> Optional[Path]:
    return await _sns_media.materialize_sns_remote_video(
        account_dir=account_dir,
        url=url,
        key=key,
        token=token,
        use_cache=use_cache,
        diagnostic_id=diagnostic_id,
    )


def _best_effort_unlink(path: str) -> None:
    _sns_media.best_effort_unlink(path)


def _detect_image_mime(data: bytes) -> str:
    return _sns_media.detect_image_mime(data)


_SNS_REMOTE_CACHE_EXTS = [
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".avif",
    ".heic",
    ".heif",
    ".bin",  # legacy/unknown
]


def _mime_to_ext(mt: str) -> str:
    m = str(mt or "").split(";", 1)[0].strip().lower()
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/avif": ".avif",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }.get(m, ".bin")


def _ext_to_mime(ext: str) -> str:
    e = str(ext or "").strip().lower().lstrip(".")
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "bmp": "image/bmp",
        "avif": "image/avif",
        "heic": "image/heic",
        "heif": "image/heif",
    }.get(e, "")


def _sns_remote_cache_dir_and_stem(account_dir: Path, *, url: str, key: str) -> tuple[Path, str]:
    digest = hashlib.md5(f"{url}|{key}".encode("utf-8", errors="ignore")).hexdigest()
    cache_dir = account_dir / "sns_remote_cache" / digest[:2]
    return cache_dir, digest


def _sns_remote_cache_existing_path(cache_dir: Path, stem: str) -> Optional[Path]:
    for ext in _SNS_REMOTE_CACHE_EXTS:
        p = cache_dir / f"{stem}{ext}"
        try:
            if p.exists() and p.is_file():
                return p
        except Exception:
            continue
    return None


def _sniff_image_mime_from_file(path: Path) -> str:
    try:
        with path.open("rb") as f:
            head = f.read(64)
        return _detect_image_mime(head)
    except Exception:
        return ""


async def _download_sns_remote_bytes(url: str) -> tuple[bytes, str, str]:
    """Download SNS media bytes from Tencent CDN with a few safe header variants."""
    u = str(url or "").strip()
    if not u:
        return b"", "", ""

    max_bytes = 25 * 1024 * 1024

    base_headers = {
        "User-Agent": "MicroMessenger Client",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        # Avoid brotli dependency issues; images are already compressed anyway.
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }

    # Some CDN endpoints return a small placeholder image for certain UA/Referer
    # combinations but still respond 200. Try the simplest (base headers only)
    # first to maximize the chance of getting the real media in one request.
    header_variants = [
        {},
        # WeFlow/Electron: MicroMessenger UA + servicewechat.com referer passes some CDN anti-hotlink checks.
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63090719) XWEB/8351",
            "Referer": "https://servicewechat.com/",
            "Origin": "https://servicewechat.com",
        },
        {"Referer": "https://wx.qq.com/", "Origin": "https://wx.qq.com"},
        {"Referer": "https://mp.weixin.qq.com/", "Origin": "https://mp.weixin.qq.com"},
    ]

    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for extra in header_variants:
            headers = dict(base_headers)
            headers.update(extra)
            try:
                resp = await client.get(u, headers=headers)
                resp.raise_for_status()
                payload = bytes(resp.content or b"")
                if len(payload) > max_bytes:
                    raise HTTPException(status_code=400, detail="SNS media too large (>25MB).")
                content_type = str(resp.headers.get("Content-Type") or "").strip()
                x_enc = str(resp.headers.get("x-enc") or "").strip()
                return payload, content_type, x_enc
            except HTTPException:
                raise
            except Exception as e:
                last_err = e
                continue

    raise last_err or RuntimeError("sns remote download failed")


async def _try_fetch_and_decrypt_sns_remote(
    *,
    account_dir: Path,
    url: str,
    key: str,
    token: str,
    use_cache: bool,
    trace: Optional[Any] = None,
    diagnostic_id: str = "",
    stage: str = "remote",
    force_original: bool = False,
) -> Optional[Response]:
    """Try remote download+decrypt first (accurate when keys are present)."""
    if trace is not None:
        trace(f"{stage}:start", useCache=bool(use_cache))

    res = await _sns_media.try_fetch_and_decrypt_sns_image_remote(
        account_dir=account_dir,
        url=str(url or ""),
        key=str(key or ""),
        token=str(token or ""),
        use_cache=bool(use_cache),
        diagnostic_id=str(diagnostic_id or ""),
        force_original=bool(force_original),
    )
    if res is None:
        if trace is not None:
            trace(f"{stage}:miss", result="not-found")
        return None

    resp = Response(content=res.payload, media_type=res.media_type)
    resp.headers["Cache-Control"] = "public, max-age=86400" if use_cache else "no-store"
    resp.headers["X-SNS-Source"] = str(res.source or "remote")
    if diagnostic_id:
        resp.headers["X-SNS-Diagnostic-Id"] = str(diagnostic_id)
    if trace is not None:
        response_width, response_height = _image_size_from_bytes(res.payload, res.media_type)
        trace(
            f"{stage}:result",
            result=str(res.source or "remote"),
            mediaType=str(res.media_type or ""),
            bytes=len(res.payload or b""),
            responseWidth=int(response_width or 0),
            responseHeight=int(response_height or 0),
            responseSha256=hashlib.sha256(res.payload or b"").hexdigest(),
            xEnc=str(res.x_enc or ""),
            cachePath=str(res.cache_path or ""),
        )
    return resp


def _sns_remote_http_exception(
    exc: BaseException,
    *,
    diagnostic_id: str,
) -> HTTPException:
    headers = {"X-SNS-Diagnostic-Id": str(diagnostic_id or "")}
    if isinstance(exc, _sns_media.SnsWasmRuntimeUnavailable):
        return HTTPException(
            status_code=503,
            detail="SNS media decryption runtime is unavailable.",
            headers=headers,
        )
    if isinstance(
        exc,
        (_sns_media.SnsRemoteMediaDecodeError, _sns_media.SnsRemoteMediaUpstreamError),
    ):
        return HTTPException(
            status_code=502,
            detail="SNS CDN media could not be downloaded or decoded.",
            headers=headers,
        )
    if isinstance(exc, HTTPException):
        if exc.headers:
            headers.update(exc.headers)
        return HTTPException(status_code=exc.status_code, detail=exc.detail, headers=headers)
    return HTTPException(
        status_code=502,
        detail="SNS CDN media processing failed.",
        headers=headers,
    )


def _sns_media_value_hash(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _sns_media_url_trace_fields(url: object) -> dict[str, Any]:
    raw = str(url or "").strip()
    if not raw:
        return {
            "urlPresent": False,
            "urlHost": "",
            "urlIdentity": "",
            "urlHasQuery": False,
        }

    try:
        parsed = urlparse(raw)
        host = str(parsed.hostname or "").strip().lower()
        has_query = bool(parsed.query)
    except Exception:
        host = ""
        has_query = "?" in raw

    stable_url = _sns_media.normalize_sns_cache_url(
        _sns_media.fix_sns_cdn_url(raw, token="", is_video=False)
    )
    return {
        "urlPresent": True,
        "urlHost": host,
        "urlIdentity": _sns_media_value_hash(stable_url),
        "urlHasQuery": bool(has_query),
        "sizeSuffix": _sns_media._sns_cdn_size_suffix(raw),
        "mediaSource": _sns_media._sns_cdn_media_source(raw),
    }


def _sns_media_path_trace_fields(path: object, *, create_time: int = 0) -> dict[str, Any]:
    path_text = str(path or "")
    fields: dict[str, Any] = {"candidatePath": path_text}
    if not path_text:
        return fields

    try:
        stat = Path(path_text).stat()
        fields["candidateBytes"] = int(stat.st_size)
        fields["candidateMtime"] = round(float(stat.st_mtime), 3)
        if int(create_time or 0) > 0:
            fields["candidateMtimeDeltaSec"] = round(
                abs(float(stat.st_mtime) - float(create_time)),
                3,
            )
    except Exception as exc:
        fields["candidateStatErrorType"] = type(exc).__name__
    return fields


def _sns_media_payload_trace_fields(payload: bytes, media_type: str) -> dict[str, Any]:
    data = bytes(payload or b"")
    response_width, response_height = _image_size_from_bytes(data, media_type)
    return {
        "mediaType": str(media_type or ""),
        "bytes": len(data),
        "responseWidth": int(response_width or 0),
        "responseHeight": int(response_height or 0),
        "responseSha256": hashlib.sha256(data).hexdigest() if data else "",
    }


@router.get("/api/sns/media", summary="获取朋友圈图片（本地缓存优先）")
async def get_sns_media(
        account: Optional[str] = None,
        create_time: int = 0,
        width: int = 0,
        height: int = 0,
        total_size: int = 0,
        idx: int = 0,
        post_id: Optional[str] = None,
        media_id: Optional[str] = None,
        post_type: int = 1,
        media_type: int = 2,
        md5: Optional[str] = None,
        token: Optional[str] = None,
        key: Optional[str] = None,
        use_cache: int = 1,
        url: Optional[str] = None,
        variant: Optional[str] = None,
):
    account_dir = _resolve_account_dir(account)
    wxid_dir = _resolve_account_wxid_dir(account_dir)

    try:
        use_cache_flag = bool(int(1 if use_cache is None else use_cache))
    except Exception:
        use_cache_flag = True

    variant_norm = str(variant or "").strip().lower()
    # `/0` is credential-bound and must never be inferred from a loose alias.
    # Only the documented `variant=full` contract may request the original path.
    prefer_remote_original = variant_norm == "full"
    post_type_i = int(post_type or 1)
    media_type_i = int(media_type or 2)
    md5_norm = _normalize_hex32(md5)
    request_id = f"sns-media-{time.time_ns()}-{threading.get_ident()}"
    _trace_id, trace = create_perf_trace(
        logger,
        "sns.media",
        requestId=request_id,
        account=str(account_dir.name),
        accountDir=str(account_dir),
        wxidDir=str(wxid_dir or ""),
        postId=str(post_id or ""),
        mediaId=str(media_id or ""),
        postType=post_type_i,
        mediaType=media_type_i,
        createTime=int(create_time or 0),
        width=int(width or 0),
        height=int(height or 0),
        totalSize=int(total_size or 0),
        idx=max(0, int(idx or 0)),
        md5=md5_norm,
        variant=variant_norm,
        preferRemoteOriginal=prefer_remote_original,
        useCacheRequested=str(use_cache),
        useCacheEffective=use_cache_flag,
        tokenPresent=bool(str(token or "")),
        tokenLength=len(str(token or "")),
        tokenHash=_sns_media_value_hash(token),
        keyPresent=bool(str(key or "")),
        keyLength=len(str(key or "")),
        keyHash=_sns_media_value_hash(key),
        **_sns_media_url_trace_fields(url),
    )
    trace("request:start")

    # 点击预览需要高清原图：本地 SNS 缓存有时只命中缩略图，所以 full/original 请求先按
    # 原图 URL/token/key 链路取图；只有真实 404 才回退本地缓存，runtime/解密错误保持可诊断。
    if prefer_remote_original and str(url or "").strip():
        try:
            remote_resp = await _try_fetch_and_decrypt_sns_remote(
                account_dir=account_dir,
                url=str(url or ""),
                key=str(key or ""),
                token=str(token or ""),
                use_cache=use_cache_flag,
                trace=trace,
                diagnostic_id=request_id,
                stage="remote-original",
                force_original=True,
            )
        except Exception as exc:
            http_exc = _sns_remote_http_exception(exc, diagnostic_id=request_id)
            trace(
                "response:error",
                result="remote-original-error",
                statusCode=int(http_exc.status_code),
                errorType=type(exc).__name__,
            )
            raise http_exc from exc
        if remote_resp is not None:
            remote_resp.headers["X-SNS-Variant"] = "full"
            remote_resp.headers["X-SNS-Diagnostic-Id"] = request_id
            trace(
                "response:ready",
                result=str(remote_resp.headers.get("X-SNS-Source") or "remote"),
                matchedBy="remote-original",
                **_sns_media_payload_trace_fields(
                    bytes(getattr(remote_resp, "body", b"") or b""),
                    str(remote_resp.media_type or ""),
                ),
            )
            return remote_resp

    if use_cache_flag:
        if wxid_dir and post_id and media_id and post_type_i == 7:
            try:
                raw_key = f"{post_id}_{media_id}_4"
                bkg_md5 = hashlib.md5(raw_key.encode("utf-8", errors="ignore")).hexdigest()
                bkg_path = wxid_dir / "business" / "sns" / "bkg" / bkg_md5[:2] / bkg_md5
                bkg_matched = bkg_path.exists() and bkg_path.is_file()
                trace(
                    "local-cover:probe",
                    cacheKey=bkg_md5,
                    matched=bool(bkg_matched),
                    **_sns_media_path_trace_fields(bkg_path, create_time=int(create_time or 0)),
                )
                if bkg_matched:
                    trace(
                        "response:ready",
                        result="local-cover-cache",
                        matchedBy="local-cover",
                        mediaType="image/jpeg",
                        **_sns_media_path_trace_fields(bkg_path, create_time=int(create_time or 0)),
                    )
                    return FileResponse(
                        str(bkg_path),
                        media_type="image/jpeg",
                        headers={
                            "Cache-Control": "public, max-age=31536000",
                            "X-SNS-Source": "local-cover-cache",
                            "X-SNS-Diagnostic-Id": request_id,
                        },
                    )
            except Exception as exc:
                trace("local-cover:error", errorType=type(exc).__name__)

        local_path = ""
        matched_by = ""

        # 1) 精确路径匹配：md5(tid_mediaId_type)。
        if wxid_dir and post_id and media_id:
            try:
                key_post = _generate_sns_cache_key(str(post_id), str(media_id), post_type_i)
                local_path = _resolve_sns_cached_image_path_by_cache_key(
                    wxid_dir=wxid_dir,
                    cache_key=key_post,
                    create_time=0,
                ) or ""
                matched_by = "local-key-post" if local_path else ""
                trace(
                    "local-key-post:probe",
                    cacheKey=key_post,
                    matched=bool(local_path),
                    **_sns_media_path_trace_fields(local_path, create_time=int(create_time or 0)),
                )
            except Exception as exc:
                local_path = ""
                trace("local-key-post:error", errorType=type(exc).__name__)

            if (not local_path) and post_type_i != media_type_i:
                try:
                    key_media = _generate_sns_cache_key(str(post_id), str(media_id), media_type_i)
                    local_path = _resolve_sns_cached_image_path_by_cache_key(
                        wxid_dir=wxid_dir,
                        cache_key=key_media,
                        create_time=0,
                    ) or ""
                    matched_by = "local-key-media" if local_path else ""
                    trace(
                        "local-key-media:probe",
                        cacheKey=key_media,
                        matched=bool(local_path),
                        **_sns_media_path_trace_fields(local_path, create_time=int(create_time or 0)),
                    )
                except Exception as exc:
                    local_path = ""
                    trace("local-key-media:error", errorType=type(exc).__name__)

        # 2) 使用 XML 或 URL 里携带的 md5 匹配缓存布局。
        if (not local_path) and wxid_dir and md5_norm:
            try:
                local_path = _resolve_sns_cached_image_path_by_md5(
                    wxid_dir=wxid_dir,
                    md5=md5_norm,
                    create_time=int(create_time or 0),
                ) or ""
                matched_by = "local-md5" if local_path else ""
                trace(
                    "local-md5:probe",
                    matched=bool(local_path),
                    **_sns_media_path_trace_fields(local_path, create_time=int(create_time or 0)),
                )
            except Exception as exc:
                local_path = ""
                trace("local-md5:error", errorType=type(exc).__name__)

        # 3) 旧版启发式匹配：发布时间、尺寸、文件大小和同尺寸组内序号。
        if not local_path:
            try:
                # 启发式匹配需要枚举和解密候选文件，放到有界后台线程，
                # 避免一张图占住 FastAPI 事件循环并拖慢时间线和健康检查。
                local_path = await account_to_thread(account_dir,
                    _resolve_sns_cached_image_path_bounded,
                    account_dir_str=str(account_dir),
                    create_time=int(create_time or 0),
                    width=int(width or 0),
                    height=int(height or 0),
                    idx=max(0, int(idx or 0)),
                    total_size=int(total_size or 0),
                ) or ""
                matched_by = "local-heuristic" if local_path else ""
                trace(
                    "local-heuristic:probe",
                    matched=bool(local_path),
                    **_sns_media_path_trace_fields(local_path, create_time=int(create_time or 0)),
                )
            except Exception as exc:
                local_path = ""
                trace("local-heuristic:error", errorType=type(exc).__name__)

        if local_path:
            trace(
                "local-cache:decode-start",
                matchedBy=matched_by,
                **_sns_media_path_trace_fields(local_path, create_time=int(create_time or 0)),
            )
            try:
                payload, local_media_type = await account_to_thread(account_dir,
                    _read_and_maybe_decrypt_media,
                    Path(local_path),
                    account_dir,
                )
                if payload and str(local_media_type or "").startswith("image/"):
                    resp = Response(content=payload, media_type=str(local_media_type or "image/jpeg"))
                    resp.headers["Cache-Control"] = "public, max-age=31536000"
                    resp.headers["X-SNS-Source"] = "local-cache"
                    resp.headers["X-SNS-Diagnostic-Id"] = request_id
                    trace(
                        "response:ready",
                        result="local-cache",
                        matchedBy=matched_by,
                        **_sns_media_path_trace_fields(local_path, create_time=int(create_time or 0)),
                        **_sns_media_payload_trace_fields(payload, str(local_media_type or "")),
                    )
                    return resp
                trace(
                    "local-cache:decode-rejected",
                    matchedBy=matched_by,
                    reason="empty-or-non-image",
                    **_sns_media_payload_trace_fields(payload or b"", str(local_media_type or "")),
                )
            except Exception as exc:
                trace(
                    "local-cache:decode-error",
                    matchedBy=matched_by,
                    errorType=type(exc).__name__,
                )
    else:
        trace("local-cache:skip", reason="use-cache-disabled")

    # 4) 最后再走远程：WeFlow 风格下载、解密和远程缓存。
    try:
        remote_resp = await _try_fetch_and_decrypt_sns_remote(
            account_dir=account_dir,
            url=str(url or ""),
            key=str(key or ""),
            token=str(token or ""),
            use_cache=use_cache_flag,
            trace=trace,
            diagnostic_id=request_id,
            stage="remote-fallback",
        )
    except Exception as exc:
        http_exc = _sns_remote_http_exception(exc, diagnostic_id=request_id)
        trace(
            "response:error",
            result="remote-fallback-error",
            statusCode=int(http_exc.status_code),
            errorType=type(exc).__name__,
        )
        raise http_exc from exc
    if remote_resp is not None:
        remote_resp.headers["X-SNS-Diagnostic-Id"] = request_id
        trace(
            "response:ready",
            result=str(remote_resp.headers.get("X-SNS-Source") or "remote"),
            matchedBy="remote-fallback",
            **_sns_media_payload_trace_fields(
                bytes(getattr(remote_resp, "body", b"") or b""),
                str(remote_resp.media_type or ""),
            ),
        )
        return remote_resp

    trace("response:error", result="not-found", statusCode=404)
    raise HTTPException(
        status_code=404,
        detail="SNS media not found.",
        headers={"X-SNS-Diagnostic-Id": request_id},
    )


@router.get("/api/sns/article_thumb", summary="提取公众号文章封面图")
async def proxy_article_thumb(url: str):
    u = str(url or "").strip()
    if not u.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            resp = await client.get(u, headers=headers)
            resp.raise_for_status()
            html_text = resp.text

            match = re.search(r'["\'](https?://[^"\']*?mmbiz_[a-zA-Z]+[^"\']*?)["\']', html_text)

            if not match:
                raise HTTPException(status_code=404, detail="未在 HTML 中找到图片 URL")

            img_url = match.group(1)
            img_url = html.unescape(img_url).replace("&amp;", "&")

            img_resp = await client.get(img_url, headers=headers)
            img_resp.raise_for_status()

            return Response(
                content=img_resp.content,
                media_type=img_resp.headers.get("Content-Type", "image/jpeg")
            )

    except Exception as e:
        logger.warning(
            "[sns] article thumbnail failed urlIdentity=%s errorType=%s",
            _sns_media_value_hash(u),
            type(e).__name__,
        )
        raise HTTPException(status_code=404, detail="无法获取文章封面")


@router.get("/api/sns/video_remote", summary="获取朋友圈远程视频/实况（下载解密优先）")
async def get_sns_video_remote(
        account: Optional[str] = None,
        url: Optional[str] = None,
        token: Optional[str] = None,
        key: Optional[str] = None,
        use_cache: int = 1,
):
    account_dir = _resolve_account_dir(account)
    request_id = f"sns-video-{time.time_ns()}-{threading.get_ident()}"

    try:
        use_cache_flag = bool(int(1 if use_cache is None else use_cache))
    except Exception:
        use_cache_flag = True

    try:
        path = await _materialize_sns_remote_video(
            account_dir=account_dir,
            url=str(url or ""),
            key=str(key or ""),
            token=str(token or ""),
            use_cache=use_cache_flag,
            diagnostic_id=request_id,
        )
    except Exception as exc:
        raise _sns_remote_http_exception(exc, diagnostic_id=request_id) from exc
    if path is None:
        raise HTTPException(
            status_code=404,
            detail="SNS remote video not found.",
            headers={"X-SNS-Diagnostic-Id": request_id},
        )

    headers = {
        "Cache-Control": "public, max-age=86400" if use_cache_flag else "no-store",
        "X-SNS-Diagnostic-Id": request_id,
    }

    if use_cache_flag:
        return FileResponse(str(path), media_type="video/mp4", headers=headers)

    # Cache disabled: delete the temp file after response.
    return FileResponse(
        str(path),
        media_type="video/mp4",
        headers=headers,
        background=BackgroundTask(_best_effort_unlink, str(path)),
    )


@router.get("/api/sns/video", summary="获取朋友圈本地缓存视频")
async def get_sns_video(
        account: Optional[str] = None,
        post_id: Optional[str] = None,
        media_id: Optional[str] = None,
):
    if not post_id or not media_id:
        raise HTTPException(status_code=400, detail="Missing post_id or media_id")

    account_dir = _resolve_account_dir(account)
    wxid_dir = _resolve_account_wxid_dir(account_dir)

    if not wxid_dir:
        raise HTTPException(status_code=404, detail="WXID dir not found")

    video_path = _resolve_sns_cached_video_path(wxid_dir, post_id, media_id)

    if not video_path:
        raise HTTPException(status_code=404, detail="Local video cache not found")

    return FileResponse(video_path, media_type="video/mp4")
