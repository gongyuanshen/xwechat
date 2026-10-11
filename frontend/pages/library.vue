<template>
  <main class="library-page theme-scope">
    <header>
      <h1>本地资料夹</h1>
      <span>聊天原文、附件、识别/提取文字与 AI 报告的本地副本</span>
    </header>
    <p v-if="accounts.error || error" class="library-error" role="alert">
      {{ accounts.error || error }} <button @click="load">重试加载</button>
    </p>
    <p v-if="!selectedAccount">请先选择已解密或已导入的微信账号。</p>
    <div v-else class="library-layout" :class="{ 'has-detail': selected }">
      <aside>
        <h2>资料夹</h2>
        <form class="inline" @submit.prevent="createFolder">
          <input
            v-model="folderName"
            :class="{ 'privacy-blur': privacyMode }"
            aria-label="新资料夹名称"
            placeholder="新资料夹"
            maxlength="100"
          /><button :disabled="busy || !folderName.trim()">新建</button>
        </form>
        <button
          class="folder"
          :class="{ active: !folderId }"
          @click="chooseFolder('')"
        >
          全部资料</button
        ><button
          v-for="f in folders"
          :key="f.id"
          class="folder"
          :class="{ active: folderId === f.id }"
          @click="chooseFolder(f.id)"
        >
          <span :class="{ 'privacy-blur': privacyMode }">{{ f.name }}</span>
        </button>
        <div v-if="folderId" class="inline">
          <input
            v-model="renameName"
            :class="{ 'privacy-blur': privacyMode }"
            aria-label="资料夹新名称"
            placeholder="新名称"
          /><button
            :disabled="busy || !renameName.trim()"
            @click="renameFolder"
          >
            重命名</button
          ><button :disabled="busy" @click="removeFolder">删除资料夹</button>
        </div>
      </aside>
      <section class="item-list">
        <input
          v-model="query"
          :class="{ 'privacy-blur': privacyMode }"
          aria-label="搜索资料"
          placeholder="搜索标题、备注、标签"
        />
        <p v-if="loading" role="status">正在加载资料…</p>
        <p v-else-if="!filtered.length">
          暂无资料。可从聊天消息、附件中心或 AI 回答保存。
        </p>
        <button
          v-for="item in filtered"
          :key="item.id"
          class="item"
          :class="{ active: selected?.id === item.id }"
          @click="select(item)"
        >
          <strong :class="{ 'privacy-blur': privacyMode }">{{ item.title }}</strong
          ><small
            >{{ itemKind(item) }} ·
            {{ item.verified ? "已人工核验" : "待人工核验" }}</small
          ><small :class="{ 'privacy-blur': privacyMode }">{{ item.tags.join(" · ") }}</small>
        </button>
      </section>
      <section v-if="selected" class="detail">
        <button class="detail-back" type="button" @click="backToList">
          返回资料列表
        </button>
        <div class="inline">
          <h2>{{ itemKind(selected) }}</h2>
          <button :disabled="busy" @click="removeItem">删除副本</button>
        </div>
        <p class="metadata">
          {{ selected.verified ? "已人工核验" : "待人工核验" }} · 更新于
          {{ formatDate(selected.updated_at) }}
        </p>
        <div v-if="selected.kind === 'extracted_text'" class="extraction-summary metadata">
          <p>{{ { local_ocr: '本地 OCR 识别文字', document_text: '本地文档提取文字' }[selected.extraction.extraction_method] }}<span v-if="selected.extraction.complete"> · 提取流程已完成</span></p>
          <p v-if="selected.extraction.extraction_method === 'local_ocr'">OCR 引擎：{{ selected.extraction.ocr_engine }} · 识别 {{ selected.extraction.ocr_units }} 个图片部件</p>
          <p>提取于 {{ formatDate(selected.extraction.updated) }} · 正文只读，可编辑备注与标签。</p>
          <p class="extraction-source" :class="{ 'privacy-blur': privacyMode }">原消息：{{ selected.source.text }}</p>
        </div>
        <form @submit.prevent="save">
          <fieldset :disabled="busy">
            <label
              >所在资料夹<select v-model="draft.folder_id" :class="{ 'privacy-blur': privacyMode }">
                <option
                  v-for="folder in folders"
                  :key="folder.id"
                  :value="folder.id"
                >
                  {{ folder.name }}
                </option>
              </select></label
            ><label class="verified"
              ><input
                v-model="draft.verified"
                type="checkbox"
              />我已人工核验此资料</label
            ><label>标题<input v-model="draft.title" :class="{ 'privacy-blur': privacyMode }" maxlength="200" /></label
            ><label>备注<textarea v-model="draft.notes" :class="{ 'privacy-blur': privacyMode }" rows="3" /></label
            ><label
              >标签<input
                v-model="draft.tags"
                :class="{ 'privacy-blur': privacyMode }"
                placeholder="以逗号分隔" /></label
            ><template v-if="selected.kind === 'report'">
              <button
                type="button"
                :disabled="busy"
                @click="editingReport = !editingReport"
              >
                {{ editingReport ? "预览正文" : "编辑正文" }}
              </button>
              <label v-if="editingReport"
                >报告正文（可编辑）<textarea
                  v-model="draft.content"
                  :class="{ 'privacy-blur': privacyMode }"
                  rows="14"
                />
              </label>
              <AgentAnswer
                v-else
                :class="{ 'privacy-blur': privacyMode }"
                :text="draft.content"
                :citations="selected.report.citations"
                :references="selected.report.references"
                @locate="locate"
              />
            </template>
            <div v-else-if="selected.kind === 'attachment'" class="saved-attachment">
              <p><strong :class="{ 'privacy-blur': privacyMode }">{{ selected.attachment.name }}</strong> · {{ formatFileSize(selected.attachment.size) }}</p>
              <p>已保存附件实体副本，下载不依赖原附件。</p>
              <p v-if="selected.attachment.preservation_note">{{ selected.attachment.preservation_note }}</p>
              <button v-if="['image', 'video'].includes(selected.attachment.kind)" type="button" @click="showAttachmentPreview = !showAttachmentPreview">{{ showAttachmentPreview ? '收起预览' : '预览附件副本' }}</button>
              <p v-if="attachmentPreviewError" class="library-error" role="alert">{{ attachmentPreviewError }}</p>
              <template v-if="showAttachmentPreview">
                <img v-if="selected.attachment.kind === 'image'" :class="{ 'privacy-blur': privacyMode }" :src="api.attachmentUrl(selectedAccount, selected.id)" :alt="selected.attachment.name" @error="attachmentPreviewError = '图片副本读取失败，请下载查看具体错误。'" />
                <video v-else :class="{ 'privacy-blur': privacyMode }" :src="api.attachmentUrl(selectedAccount, selected.id)" controls preload="metadata" @error="attachmentPreviewError = '此视频无法在浏览器预览，可下载副本后使用本地播放器打开。'" />
              </template>
              <pre :class="{ 'privacy-blur': privacyMode }">{{ selected.content }}</pre>
            </div>
            <pre v-else :class="{ 'privacy-blur': privacyMode }">{{ selected.content }}</pre>
            <button :disabled="busy">保存修改</button
            ><span v-if="savedNotice" role="status">{{ savedNotice }}</span>
          </fieldset>
        </form>
        <p v-if="dirty">请先保存修改，再导出当前资料。</p>
        <div class="inline">
          <button v-if="selected.kind === 'attachment'" :disabled="busy" @click="downloadAttachment">下载附件副本</button>
          <button :disabled="busy || dirty" @click="exportFile('markdown')">
            导出 Markdown</button
          ><button :disabled="busy || dirty" @click="exportFile('html')">
            导出 HTML</button
          ><button
            v-if="selected.source?.username && selected.source?.anchor"
            @click="locate(selected.source)"
          >
            打开原消息
          </button>
        </div>
        <p v-if="selected.kind === 'attachment'" class="metadata">Markdown / HTML 仅导出文字说明，附件文件请单独下载。</p>
        <template v-if="selected.kind === 'report'"
          ><h3>原始报告与证据</h3>
          <p>
            {{
              selected.user_edited
                ? "正文已编辑，原始回答保留"
                : "正文与原始回答一致"
            }}
          </p>
          <dl class="report-summary">
            <div>
              <dt>聊天范围</dt>
              <dd :class="{ 'privacy-blur': privacyMode }">{{ reportSummary.scope }}</dd>
            </div>
            <div>
              <dt>时间范围</dt>
              <dd>{{ reportSummary.time }}</dd>
            </div>
            <div>
              <dt>处理覆盖</dt>
              <dd>{{ reportSummary.coverage }}</dd>
            </div>
            <div>
              <dt>分析模型</dt>
              <dd>{{ reportSummary.model }}</dd>
            </div>
          </dl>
          <p
            v-for="warning in selected.report.coverage_warnings"
            :key="warning"
            class="coverage-warning"
            :class="{ 'privacy-blur': privacyMode }"
          >
            {{ warning }}
          </p>
          <p>正文修改只影响保存副本，原始回答与引用保留。</p>
          <details>
            <summary>查看原始回答</summary>
            <AgentAnswer
              :class="{ 'privacy-blur': privacyMode }"
              :text="selected.report.answer"
              :citations="selected.report.citations"
              :references="selected.report.references"
              @locate="locate"
            />
          </details>
          <div
            v-for="(source, index) in selected.report?.citations"
            :key="index"
          >
            <button
              v-if="source.username && source.anchor"
              @click="locate(source)"
            >
              原文 {{ index + 1 }} · <span :class="{ 'privacy-blur': privacyMode }">{{ source.text }}</span>
            </button>
            <p v-else :class="{ 'privacy-blur': privacyMode }">{{ source.text }}</p>
          </div></template
        >
      </section>
      <section v-else class="detail empty">
        选择一份资料查看内容与来源。
      </section>
    </div>
  </main>
</template>
<script setup>
import AgentAnswer from "~/components/chat/AgentAnswer.vue";
import "~/assets/css/agent.css";
import { useLibraryApi } from "~/composables/useLibraryApi";
import { computed, onMounted, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { isNavigationFailure } from "vue-router";
import { useChatAccountsStore } from "~/stores/chatAccounts";
import { usePrivacyStore } from "~/stores/privacy";
import { formatFileSize } from "~/lib/chat/formatters";
import { saveAttachmentDownload } from "~/utils/attachmentDownload";
useHead({ title: "本地资料夹 - xwechat" });
const accounts = useChatAccountsStore(),
  { selectedAccount } = storeToRefs(accounts),
  api = useLibraryApi();
const { privacyMode } = storeToRefs(usePrivacyStore());
const folders = ref([]),
  items = ref([]),
  folderId = ref(""),
  folderName = ref(""),
  renameName = ref(""),
  query = ref(""),
  selected = ref(null),
  draft = ref({}),
  error = ref(""),
  loading = ref(false),
  busy = ref(false),
  savedNotice = ref(""),
  editingReport = ref(false);
const showAttachmentPreview = ref(false), attachmentPreviewError = ref('');
watch(selected, () => { showAttachmentPreview.value = false; attachmentPreviewError.value = ''; });
const itemKind = (item) => ({ report: 'AI 报告', message: '聊天原文', attachment: '附件副本', extracted_text: '识别/提取文字' })[item.kind];
const navigation = useState("ai-navigation-target", () => null);
let generation = 0;
let loadRequest = 0;
const filtered = computed(() =>
  items.value.filter(
    (item) =>
      (!folderId.value || item.folder_id === folderId.value) &&
      [item.title, item.notes, ...item.tags]
        .join(" ")
        .toLowerCase()
        .includes(query.value.toLowerCase()),
  ),
);
const message = (e) => typeof e.data?.detail === 'string' ? e.data.detail : e.data?.detail?.message || e.message;
const formatDate = (value) => new Date(value * 1000).toLocaleString();
const reportSummary = computed(() => {
  const report = selected.value?.report;
  if (!report) return {};
  const chats = report.query_scope || [];
  const interval = report.time_range;
  const format = (seconds) =>
    new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "UTC",
    }).format(new Date((seconds + (report.timezone_offset || 0)) * 1000));
  const coverage =
    {
      complete: "范围已处理完成",
      partial: "仅覆盖部分范围",
      not_applicable: "不适用完整覆盖",
      unknown: "覆盖情况未知",
    }[report.coverage_state] || "未记录覆盖情况";
  const profile = report.model_metadata?.profile;
  return {
    scope: chats.length
      ? `${chats.length} 个聊天：${chats.map((chat) => (typeof chat === "string" ? chat : chat.name || chat.username)).join("、")}`
      : "未记录查询聊天范围",
    time:
      interval?.start != null && interval?.end != null
        ? `${format(interval.start)} 至 ${format(Math.max(interval.start, interval.end - 1))}${report.timezone_offset == null ? "（UTC）" : ""}`
        : "未限定时间或未记录时间范围",
    coverage: `${coverage}${Number.isFinite(report.read_count) ? ` · 已读取 ${report.read_count} 条` : ""}`,
    model: profile?.model || profile?.name || "未记录模型名称",
  };
});
const dirty = computed(
  () =>
    selected.value &&
    (draft.value.title !== selected.value.title ||
      draft.value.notes !== selected.value.notes ||
      draft.value.content !== selected.value.content ||
      draft.value.tags !== selected.value.tags.join(", ") ||
      draft.value.verified !== selected.value.verified ||
      draft.value.folder_id !== selected.value.folder_id),
);
watch(
  selectedAccount,
  () => {
    generation++;
    folders.value = [];
    items.value = [];
    selected.value = null;
    folderId.value = "";
    error.value = "";
    query.value = "";
    savedNotice.value = "";
    busy.value = false;
    loading.value = false;
    load();
  },
  { immediate: true, flush: "sync" },
);
function chooseFolder(id) {
  if (dirty.value && !window.confirm("放弃尚未保存的修改？")) return;
  selected.value = null;
  folderId.value = id;
  savedNotice.value = "";
  renameName.value = folders.value.find((f) => f.id === id)?.name || "";
}
async function load() {
  const request = ++loadRequest;
  const token = generation,
    account = selectedAccount.value;
  if (!account) return;
  loading.value = true;
  error.value = "";
  try {
    const result = await Promise.all([
      api.listFolders(account),
      api.listItems(account),
    ]);
    if (token === generation && request === loadRequest) {
      folders.value = result[0];
      items.value = result[1];
    }
  } catch (e) {
    if (token === generation && request === loadRequest)
      error.value = message(e);
  } finally {
    if (token === generation && request === loadRequest) loading.value = false;
  }
}
function select(item, confirmed = false) {
  if (!confirmed && dirty.value && !window.confirm("放弃尚未保存的修改？"))
    return;
  selected.value = item;
  editingReport.value = false;
  draft.value = {
    title: item.title,
    notes: item.notes,
    tags: item.tags.join(", "),
    content: item.content,
    verified: item.verified,
    folder_id: item.folder_id,
  };
  savedNotice.value = "";
}
function backToList() {
  if (dirty.value && !window.confirm("放弃尚未保存的修改？")) return;
  selected.value = null;
  savedNotice.value = "";
}
async function perform(action, onRecordDeleted) {
  const token = generation,
    account = selectedAccount.value;
  busy.value = true;
  error.value = "";
  try {
    await action(account, () => token === generation);
  } catch (e) {
    if (token === generation) {
      if (e.data?.detail?.record_deleted === true) onRecordDeleted?.();
      error.value = message(e);
    }
  } finally {
    if (token === generation) busy.value = false;
  }
}
function createFolder() {
  const name = folderName.value.trim();
  return perform(async (account, current) => {
    const folder = await api.createFolder(account, { name });
    if (current()) {
      folders.value.push(folder);
      folderName.value = "";
      chooseFolder(folder.id);
    }
  });
}
function renameFolder() {
  const id = folderId.value,
    name = renameName.value.trim();
  return perform(async (account, current) => {
    const folder = await api.updateFolder(account, id, { name: name.trim() });
    if (current())
      folders.value = folders.value.map((f) => (f.id === id ? folder : f));
  });
}
function removeFolder() {
  const id = folderId.value;
  if (!window.confirm("删除资料夹及其中保存的副本？微信原记录不会被删除。"))
    return;
  const clearDeleted = () => {
    folderId.value = "";
    if (selected.value?.folder_id === id) selected.value = null;
    folders.value = folders.value.filter((f) => f.id !== id);
    items.value = items.value.filter((i) => i.folder_id !== id);
  };
  return perform(async (account, current) => {
    await api.deleteFolder(account, id);
    if (current()) clearDeleted();
  }, clearDeleted);
}
function removeItem() {
  const id = selected.value.id;
  if (!window.confirm("删除此保存副本？微信原记录不会被删除。")) return;
  const clearDeleted = () => {
    items.value = items.value.filter((i) => i.id !== id);
    if (selected.value?.id === id) selected.value = null;
  };
  return perform(async (account, current) => {
    await api.deleteItem(account, id);
    if (current()) clearDeleted();
  }, clearDeleted);
}
function save() {
  const id = selected.value.id,
    body = {
      folder_id: draft.value.folder_id,
      verified: draft.value.verified,
      title: draft.value.title,
      notes: draft.value.notes,
      tags: draft.value.tags
        .split(/[,，]/)
        .map((t) => t.trim())
        .filter(Boolean),
    };
  if (selected.value.kind === "report") body.content = draft.value.content;
  return perform(async (account, current) => {
    const item = await api.updateItem(account, id, body);
    if (current()) {
      items.value = items.value.map((i) => (i.id === id ? item : i));
      if (selected.value?.id === id) {
        select(item, true);
        savedNotice.value = "已保存";
      }
    }
  });
}
function exportFile(format) {
  const item = selected.value;
  return perform(async (account, current) => {
    const blob = await api.exportItem(account, item.id, format);
    if (!current()) return;
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = `${item.title.replace(/[<>:"/\\|?*]/g, "_")}.${format === "markdown" ? "md" : "html"}`;
    link.click();
    URL.revokeObjectURL(url);
  });
}
function downloadAttachment() {
  const item = selected.value;
  return perform(async (account, current) => {
    const response = await api.downloadAttachment(account, item.id);
    if (!current()) return;
    saveAttachmentDownload(response);
  });
}
function locate(source) {
  return perform(async (account, current) => {
    const target = { ...source, kind: "source", account };
    navigation.value = target;
    try {
      const result = await navigateTo("/chat");
      if (result === false || isNavigationFailure(result))
        throw new Error("未能打开聊天原消息，请重试定位");
    } catch (e) {
      if (navigation.value === target) navigation.value = null;
      throw e;
    }
  });
}
onMounted(() => accounts.ensureLoaded());
</script>
<style scoped>
.saved-attachment { margin: 16px 0; }
.saved-attachment p { margin: 8px 0; }
.saved-attachment img, .saved-attachment video { display: block; max-width: 100%; max-height: 400px; margin: 12px 0; }
.extraction-summary { margin: 12px 0; line-height: 1.7; overflow-wrap: anywhere; }
.library-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--app-surface-bg, #fff);
  color: var(--app-text-primary, #1f2937);
}
header {
  padding: 20px 24px;
  border-bottom: 1px solid var(--app-border, #e5e7eb);
}
h1 {
  font-size: 20px;
  font-weight: 600;
}
header span,
.metadata,
small {
  font-size: 12px;
  color: var(--app-text-secondary, #4b5563);
}
h2,
h3 {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 12px;
}
h3 {
  margin-top: 24px;
}
.library-layout {
  display: grid;
  grid-template-columns: 200px 260px minmax(0, 1fr);
  min-height: 0;
  flex: 1;
}
aside,
.item-list,
.detail {
  padding: 18px;
  overflow: auto;
}
aside,
.item-list {
  border-right: 1px solid var(--app-border, #e5e7eb);
}
button,
input,
textarea,
select {
  border: 1px solid var(--app-border, #d1d5db);
  border-radius: 6px;
  padding: 7px 10px;
  background: transparent;
  color: inherit;
  font-size: 13px;
}
input,
textarea,
select {
  width: 100%;
}
.verified {
  display: flex;
  align-items: center;
}
.verified input {
  width: auto;
}
textarea {
  resize: vertical;
}
button:disabled {
  opacity: 0.5;
}
button:hover {
  background: var(--app-surface-muted, #f3f4f6);
}
button:focus-visible,
input:focus-visible,
textarea:focus-visible {
  outline: 2px solid var(--app-accent, #2563eb);
  outline-offset: 2px;
}
.inline {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.inline input {
  min-width: 0;
  flex: 1;
}
.folder,
.item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  width: 100%;
  border: 0;
  text-align: left;
  margin: 5px 0;
  padding: 10px;
  gap: 6px;
}
.active {
  background: var(--app-surface-muted, #f3f4f6);
}
fieldset {
  border: 0;
  padding: 0;
  min-width: 0;
}
label {
  display: grid;
  gap: 6px;
  font-size: 13px;
  margin: 14px 0;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  font-size: 13px;
  line-height: 1.7;
  margin: 14px 0;
}
.library-error {
  color: var(--danger-color, #b91c1c);
  padding: 12px 24px;
}
.detail > p,
.empty,
.item-list > p {
  font-size: 13px;
}
.detail .inline {
  margin-top: 16px;
}
.detail form > span {
  font-size: 13px;
  margin-left: 8px;
}
.item strong {
  overflow-wrap: anywhere;
}
.detail-back {
  display: none;
}
.report-summary {
  display: grid;
  gap: 10px;
  margin: 16px 0;
  font-size: 13px;
}
.report-summary div {
  display: grid;
  grid-template-columns: 80px minmax(0, 1fr);
  gap: 12px;
}
.report-summary dt {
  color: var(--app-text-secondary);
}
.report-summary dd {
  overflow-wrap: anywhere;
}
.coverage-warning {
  color: var(--app-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
@media (max-width: 1050px) {
  .library-layout {
    grid-template-columns: 170px minmax(0, 1fr);
  }
  .library-layout.has-detail {
    grid-template-columns: minmax(0, 1fr);
  }
  .library-layout.has-detail aside,
  .library-layout.has-detail .item-list {
    display: none;
  }
  .detail {
    grid-column: 1/-1;
  }
  .detail-back {
    display: inline-block;
    margin-bottom: 18px;
  }
  .detail.empty {
    display: none;
  }
  .item-list {
    border-right: 0;
  }
}
@media (max-width: 600px) {
  .library-layout {
    grid-template-columns: 1fr;
    overflow: auto;
  }
  aside,
  .item-list {
    overflow: visible;
  }
  .library-layout.has-detail {
    overflow: hidden;
  }
  aside {
    border-right: 0;
    border-bottom: 1px solid var(--app-border, #e5e7eb);
  }
  header {
    padding: 16px;
  }
}
</style>
