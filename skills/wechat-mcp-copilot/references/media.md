# Media

Use this for image, video, emoji, file, link, and voice resources.

## Tools

- `wechat.media.get_avatar_url`
- `wechat.media.get_chat_image_url`
- `wechat.media.get_chat_emoji_url`
- `wechat.media.get_chat_video_thumb_url`
- `wechat.media.get_chat_video_url`
- `wechat.media.get_chat_voice_url`
- `wechat.media.get_decrypted_resource_url`
- `wechat.media.get_proxy_image_url`
- `wechat.media.get_favicon_url`
- `wechat.biz.get_proxy_image_url`
- `wechat.moments.get_media_url`
- `wechat.moments.get_article_thumb_url`
- `wechat.moments.get_remote_video_url`
- `wechat.moments.get_local_video_url`

## Rules

- Media tools return URLs or resource metadata; they do not inline large binary payloads.
- Voice resources are files only. Do not transcribe voice messages.
- For phone clients, prefer `wechat.mobile.get_media_links` first.
- MCP does not open local folders or download media into cache; use returned URLs in the client.
- Locate the message first, then fetch media URL by message fields such as `server_id`, `username`, `md5`, or returned media references.
- Chat images default to `prefer_live=true`, comparing the actual dimensions of local candidates so an old thumbnail does not hide a larger downloaded image. Returning a URL does not mean the image was read; the client must open the URL and inspect the image.
- For the best available local image, call `wechat.media.get_chat_image_url` with the original message's `server_id`, `username`, `account`, and available `md5` / `file_id`. The current tool searches local files; missing files produce an error. Download the original attachment through WeChat when needed.
- Preserve returned locator fields such as `src_create_time`, `file_size`, `record_index`, `record_index_path`, and `record_attach`, especially for images in merged forwarded records.
- Explain failures using the actual error. Do not promise high-resolution recovery or describe a thumbnail as the original image.
- For Moments, prefer local media URL fields from timeline records. Use remote video/article helpers only when the timeline record has a remote URL or article URL.
