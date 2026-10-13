<template>
  <section class="attachment-browser" aria-label="附件浏览与处理">
    <div class="attachment-filters" aria-label="附件筛选">
      <label>搜索位置<select v-model="filters.search_in"><option value="all">名称与已提取正文</option><option value="name">名称</option><option value="body">仅已提取正文</option></select></label>
      <label>类型<select v-model="filters.kind"><option value="all">全部附件</option><option value="file">文件</option><option value="image">图片</option><option value="video">视频</option></select></label>
      <label>会话<select v-model="filters.username" :disabled="filtersLoading" :class="{ 'privacy-blur': privacyMode }"><option value="">全部会话</option><option v-for="row in choices.conversations" :key="row.value" :value="row.value">{{ row.label }}</option></select></label>
      <label>发送者<select v-model="filters.sender" :disabled="filtersLoading" :class="{ 'privacy-blur': privacyMode }"><option value="">全部发送者</option><option v-for="row in choices.senders" :key="row.value" :value="row.value">{{ row.label }}</option></select></label>
    </div>
    <p v-if="filterError" class="attachment-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ filterError }} <button type="button" class="attachment-button" @click="loadFilters">重试筛选选项</button></p>
    <p v-else-if="filterStatus === 'index_building'" role="status" class="attachment-meta">会话和发送者筛选项正在准备，重新查询时会更新。</p>
    <div class="attachment-summary">
      <p>文档解析和 OCR 均在本机完成，不调用在线模型。PDF 识别包含扫描页和内嵌图片。<template v-if="coverage">{{ coverage.message }} 当前筛选范围内共 {{ coverage.total_files }} 个文件、{{ coverage.total_images }} 张图片，完整提取 {{ coverage.complete }} 项，部分提取 {{ coverage.partial }} 项。</template></p>
      <div class="attachment-actions">
        <button type="button" class="attachment-button" @click="settingsDialog.openLocalAcceleration()">管理本地加速</button>
        <template v-if="!extracting">
          <button type="button" class="attachment-button" :disabled="!extractable.length || loading" @click="extractFiles(extractable)">提取当前页文件正文</button>
          <button type="button" class="attachment-button" :disabled="!ocrExtractable.length || loading" @click="extractFiles(ocrExtractable, true)">识别当前页图片与 PDF 文字</button>
        </template>
        <button v-else type="button" class="attachment-button" :disabled="stopRequested" @click="stopRequested = true">{{ stopRequested ? '等待当前附件完成…' : `停止后续${extractionAction}` }}</button>
      </div>
    </div>
    <p v-if="notice" role="status" class="attachment-notice" :class="{ 'privacy-blur': privacyMode }">{{ notice }}</p>
    <p v-if="error" role="alert" class="attachment-error" :class="{ 'privacy-blur': privacyMode }">{{ error }} <button type="button" class="attachment-button" @click="loadPage(failedOffset)">重试本页</button></p>
    <p v-if="status === 'index_building'" role="status" class="attachment-state">附件索引正在准备，请稍后点击“查询附件”刷新。</p>
    <p v-else-if="status === 'index_error'" role="alert" class="attachment-error" :class="{ 'privacy-blur': privacyMode }">{{ indexError || '附件索引准备失败，请重新查询查看状态。' }}</p>
    <p v-else-if="loading && !items.length" role="status" class="attachment-state">正在读取附件…</p>
    <p v-else-if="needsSearch" class="attachment-state">搜索或筛选条件已改变，点击上方“查询附件”查看结果。</p>
    <p v-else-if="!items.length && !error" class="attachment-state">当前条件下没有附件。尚未提取或识别文字的附件不会命中正文搜索。</p>
    <section v-if="items.length" aria-label="附件查询结果" class="attachment-results">
      <article v-for="item in items" :key="key(item)" class="attachment-row">
        <div class="attachment-kind" aria-hidden="true"><FileTypeIcon v-if="item.kind === 'file'" :file-name="item.name" /><ImageIcon v-else-if="item.kind === 'image'" :size="30" /><Film v-else :size="30" /></div>
        <div class="attachment-copy" :class="{ 'privacy-blur': privacyMode }">
          <h2>{{ item.name }}</h2>
          <p class="attachment-meta">{{ kindName(item.kind) }}<template v-if="item.size != null"> · {{ formatFileSize(item.size) }}</template> · {{ item.conversation_name || item.username }} · {{ item.sender_name || item.sender }} · <time>{{ formatDate(item.create_time) }}</time></p>
          <template v-if="item.kind === 'file' || item.kind === 'image'">
            <p class="attachment-extraction">{{ extractionLabel(item) }}<span v-if="item.extraction?.message"> · {{ item.extraction.message }}</span></p>
            <p v-if="item.extraction?.extraction_method === 'local_ocr'" class="attachment-meta">本地 OCR · {{ item.extraction.runtime ? accelerationRuntimeText(item.extraction.runtime) : '此历史结果未记录运行设备' }} · 请核对原图</p>
            <p v-if="item.extraction?.text_excerpt" class="attachment-snippet"><strong>{{ item.extraction.extraction_method === 'local_ocr' ? '识别文字摘录' : '正文摘录' }}（已提取 {{ item.extraction.text_characters }} 字）</strong><br />{{ item.extraction.text_excerpt }}</p>
          </template>
          <p v-if="rowErrors[key(item)]" class="attachment-error" role="alert">{{ rowErrors[key(item)] }}</p>
        </div>
        <div class="attachment-actions">
          <button v-if="item.kind !== 'file'" type="button" class="attachment-button" :disabled="loading" @click="openPreview(item, $event.currentTarget)">预览</button>
          <button type="button" class="attachment-button" :disabled="loading || downloading === key(item)" @click="download(item)">{{ downloading === key(item) ? '正在下载…' : '下载' }}</button>
          <button type="button" class="attachment-button" :disabled="loading" @click="locate(item)">原消息</button>
          <button type="button" class="attachment-button" :disabled="loading" @click="saveItem(item)">加入资料夹</button>
          <button v-if="supportsDocumentText(item)" type="button" class="attachment-button" :disabled="loading || extracting" @click="extractFiles([item], false, true)">提取正文</button>
          <button v-if="supportsOcr(item)" type="button" class="attachment-button" :disabled="loading || extracting" @click="extractFiles([item], true, true)">{{ item.kind === 'file' && /\.pdf$/i.test(item.name) ? '识别 PDF 图片文字' : '识别图片文字' }}</button>
          <button v-if="item.extraction?.text_characters > 0" type="button" class="attachment-button" :disabled="loading || extracting || (textLoading && textItem && key(textItem) === key(item))" :aria-expanded="!!textItem && key(textItem) === key(item)" @click="openText(item)">查看全文</button>
        </div>
        <section v-if="textItem && key(textItem) === key(item)" class="attachment-fulltext" aria-label="附件提取全文">
          <div class="attachment-actions">
            <template v-if="textResult">
              <button type="button" class="attachment-button" :disabled="copyingText || !textResult.text" @click="copyText">{{ copyingText ? '正在复制…' : '复制全文' }}</button>
              <button type="button" class="attachment-button" :disabled="!textResult.text" @click="exportText">导出 TXT</button>
              <button type="button" class="attachment-button" :disabled="!textResult.complete || !textResult.text" @click="saveText">{{ textResult.extraction_method === 'local_ocr' ? '保存识别文字到资料夹' : '保存提取文字到资料夹' }}</button>
            </template>
            <button v-if="textError && !textResult && !textLoading" type="button" class="attachment-button" @click="openText(item)">重试读取全文</button>
            <button type="button" class="attachment-button" @click="closeText">关闭全文</button>
          </div>
          <p v-if="textLoading" class="attachment-meta" role="status">正在读取已提取的全文…</p>
          <template v-if="textResult">
            <p class="attachment-meta">{{ textResult.extraction_method === 'local_ocr' ? '本地OCR识别文字 · 请核对原图' : '本地文档提取文字' }}<span v-if="!textResult.complete"> · 部分结果，可复制或导出；完整提取后才能保存到资料夹。</span></p>
            <p v-if="textResult.extraction_method === 'local_ocr'" class="attachment-meta">运行设备：{{ textResult.runtime ? accelerationRuntimeText(textResult.runtime) : '此历史结果未记录运行设备' }}</p>
            <pre :class="{ 'privacy-blur': privacyMode }">{{ textResult.text }}</pre>
          </template>
          <p v-if="textError" class="attachment-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ textError }}</p>
          <p v-if="textNotice" class="attachment-meta" role="status">{{ textNotice }}</p>
        </section>
      </article>
    </section>
    <div v-if="items.length" class="attachment-pagination"><button type="button" class="attachment-button" :disabled="loading || offset === 0" @click="loadPage(Math.max(0, offset - PAGE_SIZE))">上一页</button><span>第 {{ Math.floor(offset / PAGE_SIZE) + 1 }} 页 · {{ items.length }} 项</span><button type="button" class="attachment-button" :disabled="loading || !hasMore" @click="loadPage(nextOffset)">下一页</button></div>
    <Teleport to="body">
      <dialog v-if="preview" ref="previewSection" class="attachment-preview" aria-label="附件预览" @cancel.prevent="closePreview" @keydown.esc.stop.prevent="closePreview" @click="onPreviewBackdrop">
        <div class="attachment-preview-heading"><h2 :class="{ 'privacy-blur': privacyMode }">{{ preview.name }}</h2><button type="button" class="attachment-button" autofocus @click="closePreview">关闭预览</button></div>
        <p v-if="preview.kind === 'image'" class="attachment-meta">预览使用本机当前可读取的图片，可能是缩略图，不保证原图。</p>
        <p v-if="previewError" class="attachment-error" role="alert">{{ previewError }}</p>
        <img v-if="preview.kind === 'image'" :key="key(preview)" :class="{ 'privacy-blur': privacyMode }" :src="api.contentUrl(selectedAccount, preview)" :alt="preview.name" @error="previewError = '图片无法预览。若尚未下载，请先在电脑版微信中打开并下载，再重试；若已下载仍无法预览，请点击“下载”查看具体错误。'" />
        <video v-else :key="key(preview)" :class="{ 'privacy-blur': privacyMode }" :src="api.contentUrl(selectedAccount, preview)" controls preload="metadata" @error="previewError = '视频无法预览。若尚未下载，请先在电脑版微信中打开并下载，再重试；若已下载仍无法预览，请点击“下载”查看具体错误。浏览器也可能不支持此视频编码，下载成功后可用本地播放器打开。'" />
      </dialog>
    </Teleport>
    <LibrarySaveDialog v-if="itemToSave" :open="true" :account="selectedAccount" :item="itemToSave" @close="itemToSave = null" @saved="onSaved" />
  </section>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { isNavigationFailure } from 'vue-router';
import { Image as ImageIcon, Film } from '@lucide/vue';
import FileTypeIcon from '~/components/chat/FileTypeIcon.vue';
import LibrarySaveDialog from '~/components/library/LibrarySaveDialog.vue';
import { useAttachmentsApi } from '~/composables/useAttachmentsApi';
import { useSettingsDialog } from '~/composables/useSettingsDialog';
import { accelerationRuntimeText } from '~/composables/useLocalAccelerationApi';
import { usePrivacyStore } from '~/stores/privacy';
import { formatFileSize } from '~/lib/chat/formatters';
import { saveAttachmentDownload } from '~/utils/attachmentDownload';

const props = defineProps({ account: { type: String, required: true }, q: { type: String, default: '' }, start: { type: String, default: '' }, end: { type: String, default: '' }, revision: { type: Number, default: 0 } });
const selectedAccount = computed(() => props.account);
const { privacyMode } = storeToRefs(usePrivacyStore());
const api = useAttachmentsApi();
const settingsDialog = useSettingsDialog();
const navigation = useState('ai-navigation-target', () => null);
const PAGE_SIZE = 40;
const defaults = () => ({ search_in: 'all', kind: 'all', username: '', sender: '' });
const filters = ref(defaults()), choices = ref({ conversations: [], senders: [] });
const items = ref([]), coverage = ref(null), error = ref(''), filterError = ref(''), notice = ref('');
const loading = ref(false), filtersLoading = ref(false), filterStatus = ref(''), needsSearch = ref(false), status = ref(''), indexError = ref('');
const offset = ref(0), nextOffset = ref(0), failedOffset = ref(0), hasMore = ref(false), rowErrors = ref({});
const preview = ref(null), previewError = ref(''), itemToSave = ref(null), downloading = ref('');
const textItem = ref(null), textResult = ref(null), textLoading = ref(false), textError = ref(''), textNotice = ref(''), copyingText = ref(false);
const previewSection = ref(null);
const extracting = ref(false), stopRequested = ref(false), extractionAction = ref('提取');
let generation = 0, filterGeneration = 0, previewTrigger;
let listController, filterController, extractionController, downloadController, textController;
const key = (item) => `${item.username}:${item.id}`;
const message = (e) => typeof e.data?.detail === 'string' ? e.data.detail : e.data?.detail?.message || e.message;
const kindName = (kind) => ({ file: '文件', image: '图片', video: '视频' })[kind];
const extractionLabel = (item) => item.extraction?.extraction_method === 'local_ocr' || item.kind === 'image'
  ? ({ complete: '文字已识别', partial: '文字部分识别', unsupported: '格式不支持', no_text: '未识别出文字' })[item.extraction?.status] || '文字未识别'
  : ({ not_extracted: '正文未提取', complete: '正文已提取', partial: '正文部分提取', unsupported: '格式不支持', no_text: '没有可提取正文' })[item.extraction?.status] || '正文未提取';
const formatDate = (value) => new Date(value * 1000).toLocaleString();
const supportsDocumentText = (item) => item.kind === 'file' && /\.(txt|md|csv|pdf|docx|xlsx|pptx)$/i.test(item.name);
const extractable = computed(() => items.value.filter((item) => supportsDocumentText(item) && !['complete', 'unsupported', 'no_text'].includes(item.extraction?.status)));
const supportsOcr = (item) => item.kind === 'image' || (item.kind === 'file' && /\.(pdf|png|jpe?g|webp|bmp|tiff?)$/i.test(item.name));
const ocrExtractable = computed(() => items.value.filter((item) => supportsOcr(item) && item.extraction?.extraction_method !== 'local_ocr'));
function invalidate() {
  generation++;
  listController?.abort(); extractionController?.abort(); downloadController?.abort();
  closeText();
  loading.value = false; extracting.value = false; stopRequested.value = false; downloading.value = '';
  closePreview(); itemToSave.value = null; notice.value = ''; rowErrors.value = {};
}
function clearResults() {
  invalidate(); items.value = []; coverage.value = null; status.value = ''; error.value = ''; indexError.value = '';
  offset.value = 0; nextOffset.value = 0; failedOffset.value = 0; hasMore.value = false; needsSearch.value = true;
}
watch(filters, clearResults, { deep: true, flush: 'sync' });
watch(() => [props.q, props.start, props.end], clearResults, { flush: 'sync' });
watch(() => [props.account, props.revision], ([account], previous) => {
  clearResults(); filterGeneration++; filterController?.abort(); choices.value = { conversations: [], senders: [] }; filterError.value = ''; filterStatus.value = ''; filtersLoading.value = false;
  if (!previous || account !== previous[0]) filters.value = defaults();
  if (account) search();
}, { immediate: true, flush: 'sync' });
async function loadFilters() {
  filterController?.abort(); filterController = new AbortController();
  const account = selectedAccount.value, token = ++filterGeneration;
  filtersLoading.value = true; filterError.value = '';
  try {
    const result = await api.filters(account, filterController.signal);
    if (token !== filterGeneration) return;
    filterStatus.value = result.status;
    if (result.status === 'index_error') filterError.value = result.message || result.index?.build?.error || '筛选项索引准备失败，请重试。';
    else choices.value = result;
  }
  catch (e) { if (token === filterGeneration && !filterController.signal.aborted) filterError.value = message(e); }
  finally { if (token === filterGeneration) filtersLoading.value = false; }
}
function query() {
  const values = filters.value;
  const start = props.start ? Math.floor(new Date(`${props.start}T00:00:00`).getTime() / 1000) : null;
  const end = props.end ? Math.floor(new Date(`${props.end}T23:59:59`).getTime() / 1000) : null;
  if (start !== null && end !== null && start > end) throw new Error('开始日期不能晚于结束日期。');
  return { q: props.q.trim(), search_in: values.search_in, kind: values.kind, username: values.username, sender: values.sender, ...(start === null ? {} : { start_time: start }), ...(end === null ? {} : { end_time: end }) };
}
function search() { loadFilters(); return loadPage(0); }
async function loadPage(pageOffset) {
  invalidate();
  const token = generation, account = selectedAccount.value;
  if (!account) return;
  const controller = new AbortController(); listController = controller;
  error.value = ''; status.value = ''; failedOffset.value = pageOffset; loading.value = true; needsSearch.value = false;
  try {
    const result = await api.list(account, { ...query(), offset: pageOffset, limit: PAGE_SIZE }, controller.signal);
    if (token !== generation) return;
    items.value = result.items; coverage.value = result.coverage; status.value = result.status;
    indexError.value = result.message || result.index?.build?.error || ''; offset.value = pageOffset; nextOffset.value = result.next_offset; hasMore.value = result.has_more;
  } catch (e) { if (token === generation && !controller.signal.aborted) error.value = message(e); }
  finally { if (token === generation) loading.value = false; }
}
async function openPreview(item, trigger) {
  const token = generation;
  previewTrigger = trigger; previewError.value = ''; preview.value = item;
  await nextTick();
  if (token !== generation || preview.value !== item) return;
  previewSection.value.showModal();
}
function closePreview() {
  previewSection.value?.close(); preview.value = null; previewError.value = '';
  if (previewTrigger?.isConnected) previewTrigger.focus({ preventScroll: true });
  previewTrigger = null;
}
function onPreviewBackdrop(event) {
  if (event.target !== previewSection.value) return;
  const rect = previewSection.value.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closePreview();
}
function saveItem(item) { itemToSave.value = { kind: 'attachment', source: { username: item.username, anchor: item.id }, title: item.name }; }
function onSaved() { notice.value = itemToSave.value.kind === 'extracted_text' ? '文字及出处已保存到本地资料夹。' : '附件副本已保存到本地资料夹。'; itemToSave.value = null; }
function closeText() {
  textController?.abort(); textItem.value = null; textResult.value = null; textLoading.value = false;
  textError.value = ''; textNotice.value = ''; copyingText.value = false;
}
async function openText(item) {
  closeText();
  const controller = new AbortController(); textController = controller;
  const token = generation, account = selectedAccount.value;
  textItem.value = item; textLoading.value = true;
  try {
    const result = await api.text(account, { username: item.username, anchor: item.id }, controller.signal);
    if (token !== generation || controller.signal.aborted) return;
    textResult.value = result;
  } catch (e) { if (token === generation && !controller.signal.aborted) textError.value = message(e); }
  finally { if (token === generation && !controller.signal.aborted) textLoading.value = false; }
}
async function copyText() {
  const token = generation, controller = textController;
  textError.value = ''; textNotice.value = ''; copyingText.value = true;
  try {
    if (!navigator.clipboard?.writeText) throw new Error('当前浏览器无法使用剪贴板，请手动选择全文复制或导出 TXT。');
    await navigator.clipboard.writeText(textResult.value.text);
    if (token === generation && !controller.signal.aborted) textNotice.value = '全文已复制。';
  } catch (e) { if (token === generation && !controller.signal.aborted) textError.value = message(e); }
  finally { if (token === generation && !controller.signal.aborted) copyingText.value = false; }
}
function exportText() {
  textError.value = ''; textNotice.value = '';
  try { saveAttachmentDownload({ blob: new Blob([textResult.value.text], { type: 'text/plain;charset=utf-8' }), filename: `${textItem.value.name}.txt`.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_') }); }
  catch (e) { textError.value = message(e); }
}
function saveText() {
  const item = textItem.value;
  itemToSave.value = { kind: 'extracted_text', source: { username: item.username, anchor: item.id }, cache_token: textResult.value.cache_token,
    title: `${item.name} · ${textResult.value.extraction_method === 'local_ocr' ? '识别文字' : '提取文字'}` };
}
async function download(item) {
  downloadController?.abort(); const controller = new AbortController(); downloadController = controller;
  const token = generation, account = selectedAccount.value; downloading.value = key(item); delete rowErrors.value[key(item)];
  try {
    const response = await api.download(account, item, controller.signal);
    if (token !== generation || controller.signal.aborted) return;
    saveAttachmentDownload(response);
  } catch (e) { if (token === generation && !controller.signal.aborted) rowErrors.value[key(item)] = message(e); }
  finally { if (token === generation && downloadController === controller) downloading.value = ''; }
}
async function locate(item) {
  const token = generation, target = { kind: 'source', origin: 'search', account: selectedAccount.value, username: item.username, anchor: item.id };
  navigation.value = target;
  try { const result = await navigateTo('/chat'); if (result === false || isNavigationFailure(result)) throw new Error('未能打开聊天原消息，请重试。'); }
  catch (e) { if (navigation.value === target) navigation.value = null; if (token === generation) rowErrors.value[key(item)] = message(e); }
}
async function extractFiles(files, ocr = false, showText = false) {
  const targets = [...files], token = generation, account = selectedAccount.value;
  if (textItem.value && targets.some(item => key(item) === key(textItem.value))) closeText();
  const controller = new AbortController(); extractionController = controller;
  extracting.value = true; stopRequested.value = false; extractionAction.value = ocr ? '识别' : '提取';
  let completed = 0, failed = 0;
  try {
    for (const item of targets) {
      if (stopRequested.value || token !== generation) break;
      notice.value = `正在${extractionAction.value} ${completed + failed + 1} / ${targets.length}：${item.name}`;
      delete rowErrors.value[key(item)];
      try {
        const result = await api.extract(account, { username: item.username, anchor: item.id, ...(ocr ? { ocr: true } : {}) }, controller.signal);
        if (token !== generation) return;
        item.extraction = result; completed++;
        if (showText && result.text_characters > 0) await openText(item);
      } catch (e) {
        if (token !== generation || controller.signal.aborted) return;
        rowErrors.value[key(item)] = message(e); failed++;
      }
    }
    if (token === generation) notice.value = `${stopRequested.value ? `已停止后续${extractionAction.value}。` : `${extractionAction.value}结束。`}处理 ${completed} 项，失败 ${failed} 项。重新查询可更新正文搜索结果。`;
  } finally { if (token === generation) extracting.value = false; }
}
defineExpose({ search });
onUnmounted(() => { invalidate(); filterGeneration++; filterController?.abort(); });
</script>

<style scoped>
.attachment-browser { min-width: 0; }
.attachment-summary, .attachment-meta, .attachment-extraction, .attachment-pagination { color: var(--app-text-secondary); font-size: 13px; }
.attachment-filters { display: flex; align-items: end; flex-wrap: wrap; gap: 12px; padding-bottom: 20px; border-bottom: 1px solid var(--app-border); }
.attachment-filters label { display: grid; gap: 6px; font-size: 12px; min-width: 0; flex: 1 1 150px; }
.attachment-filters select { width: 100%; min-width: 0; height: 36px; padding: 6px 10px; border: 1px solid var(--app-border); border-radius: 7px; color: var(--app-text-primary); background: var(--app-surface-bg); }
.attachment-button { border: 1px solid var(--app-border); border-radius: 7px; padding: 7px 11px; font-size: 13px; white-space: nowrap; background: var(--app-surface-bg); color: var(--app-text-primary); }
.attachment-button:hover:not(:disabled) { background: var(--app-neutral-btn-hover); }
.attachment-button:disabled { opacity: .5; cursor: not-allowed; }
.attachment-filters :focus-visible, .attachment-button:focus-visible { outline: 2px solid var(--chat-accent); outline-offset: 2px; }
.attachment-summary { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 16px 0; }
.attachment-summary p { max-width: 70ch; }
.attachment-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.attachment-row { display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; align-items: start; gap: 16px; padding: 18px 0; border-top: 1px solid var(--app-border); }
.attachment-kind { padding-top: 4px; color: var(--chat-accent); }
.attachment-kind :deep(.wechat-file-icon) { width: 34px; height: 40px; }
.attachment-copy h2, .attachment-preview h2 { font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
.attachment-meta { margin-top: 5px; line-height: 1.7; overflow-wrap: anywhere; }
.attachment-extraction { margin-top: 5px; }
.attachment-snippet { margin-top: 8px; font-size: 13px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; }
.attachment-fulltext { grid-column: 2 / -1; min-width: 0; padding: 14px; border: 1px solid var(--app-border); border-radius: 7px; }
.attachment-fulltext pre { margin-top: 10px; max-height: 50vh; overflow-y: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-family: inherit; font-size: 13px; line-height: 1.8; }
.attachment-error { color: var(--wx-red); margin: 10px 0; font-size: 13px; overflow-wrap: anywhere; }
.attachment-state { padding: 32px 0; color: var(--app-text-secondary); }
.attachment-notice { padding: 10px 0; font-size: 13px; }
.attachment-pagination { display: flex; gap: 14px; align-items: center; justify-content: center; padding: 20px 0; }
.attachment-preview { width: min(900px, calc(100vw - 32px)); max-width: none; max-height: calc(100dvh - 32px); margin: auto; padding: 20px; border: 1px solid var(--app-border); border-radius: 12px; background: var(--app-surface-bg); color: var(--app-text-primary); overflow: auto; overscroll-behavior: contain; scrollbar-color: var(--app-text-secondary) var(--app-surface-bg); }
.attachment-preview::backdrop { background: rgba(20, 30, 25, .55); }
.attachment-preview .attachment-error { color: var(--danger-color); }
.attachment-preview-heading { display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 14px; }
.attachment-preview img, .attachment-preview video { display: block; margin: 12px auto 0; max-width: 100%; max-height: 65vh; object-fit: contain; }
@media (max-width: 1000px) { .attachment-row { grid-template-columns: 36px minmax(0, 1fr); gap: 12px; } .attachment-row > .attachment-actions { grid-column: 2; } }
@media (max-width: 600px) { .attachment-summary { align-items: start; flex-direction: column; } .attachment-filters label { flex-basis: calc(50% - 12px); } .attachment-pagination { gap: 8px; } }
</style>
