<template>
  <section class="content-search-results">
          <fieldset class="search-sources"><legend>搜索来源</legend><label v-for="source in sourceOptions" :key="source.id"><input v-model="selectedSources" type="checkbox" :value="source.id" />{{ source.label }}</label></fieldset>
        <p class="search-scope-note">搜索已有本地内容；不会自动提取附件、识别图片或调用模型。聊天结果来自索引快照；资料夹仅搜索当前资料，不包含专题历史版本。</p>
        <p v-if="formError" class="search-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ formError }}</p>
        <p v-if="!submitted" class="search-state" role="status">{{ notice || '输入关键词并选择来源，点击“搜索”开始。' }}</p>
        <div v-else class="search-groups">
          <section v-for="source in activeSources" :key="source.id" :data-source="source.id" class="search-group" :aria-labelledby="`search-${source.id}`" :aria-busy="groups[source.id].loading">
            <header class="search-group-heading"><h2 :id="`search-${source.id}`">{{ source.label }}</h2><button v-if="source.id === 'documents' || source.id === 'ocr'" type="button" class="search-button" @click="$emit('attachments')">查看与处理附件</button><span v-if="groups[source.id].result" class="search-meta">已显示 {{ groups[source.id].items.length }} 条 · 本次查询 {{ formatElapsed(groups[source.id].result.elapsed_ms) }} ms</span></header>
            <template v-if="groups[source.id].result">
              <p class="search-coverage" :class="{ 'privacy-blur': privacyMode }">范围内 {{ count(groups[source.id].result.coverage.available) }} 项 · 可搜索内容 {{ count(groups[source.id].result.coverage.searched) }} 项。{{ groups[source.id].result.coverage.message }}</p>
              <p v-if="groups[source.id].result.coverage.index?.upToDate === false" class="search-coverage-warning" role="status">索引快照后还有更新，当前结果不覆盖全部最新消息。</p>
              <ul v-if="groups[source.id].result.coverage.counts" class="search-extraction-counts" aria-label="附件文字覆盖情况"><li v-for="(label, key) in countLabels" :key="key">{{ label }} {{ count(groups[source.id].result.coverage.counts[key]) }}</li></ul>
              <p v-if="groups[source.id].result.status !== 'ready'" class="search-state" :class="{ 'search-error': groups[source.id].result.status === 'index_error' }" :role="groups[source.id].result.status === 'index_error' ? 'alert' : 'status'">{{ statusLabels[groups[source.id].result.status] }}</p>
            </template>
            <p v-if="groups[source.id].loading" class="search-state" role="status">{{ groups[source.id].items.length ? '正在加载更多…' : '正在查询此来源…' }}</p>
            <p v-if="groups[source.id].error" class="search-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ groups[source.id].error }} <button type="button" class="search-button" :disabled="groups[source.id].loading" @click="loadSource(source.id, groups[source.id].failedOffset)">重试此来源</button></p>
            <p v-if="groups[source.id].actionError" class="search-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ groups[source.id].actionError }}</p>
            <p v-if="groups[source.id].result?.status === 'ready' && !groups[source.id].loading && !groups[source.id].error && !groups[source.id].items.length" class="search-state">当前已检索范围内没有匹配结果。</p>
            <article v-for="item in groups[source.id].items" :key="item.id" class="search-result">
              <div class="search-result-copy" :class="{ 'privacy-blur': privacyMode }">
                <h3>{{ item.title }}</h3>
                <p class="search-meta"><template v-if="source.id === 'notes'">{{ item.folder_name }} · {{ kindLabels[item.item_kind] }}</template><template v-else>{{ item.conversation_name }} · {{ item.sender_name || item.sender }}</template> · <time>{{ formatDate(item.timestamp) }}</time><span v-if="item.is_revoked" class="search-revoked"> · 已撤回<template v-if="item.revoke_time">（{{ formatDate(item.revoke_time) }}）</template></span></p>
                <p class="search-match">命中{{ item.matched_fields.map(field => fieldLabels[field] || field).join('、') }}<template v-if="source.id === 'ocr'"> · OCR 识别文字，请核对原图</template><template v-if="item.extraction?.status === 'partial' || item.extraction?.complete === false"> · 仅提取了部分内容</template></p>
                <p class="search-snippet">{{ item.snippet }}</p>
              </div>
              <button v-if="item.library_item_id || (item.username && item.anchor)" type="button" class="search-button" @click="openResult(source.id, item)">{{ item.library_item_id ? '打开资料' : '打开原消息' }}</button>
            </article>
            <button v-if="groups[source.id].result?.has_more && !groups[source.id].error" type="button" class="search-button search-more" :disabled="groups[source.id].loading" @click="loadSource(source.id, groups[source.id].result.next_offset)">加载更多</button>
            <button v-else-if="groups[source.id].result && groups[source.id].result.status !== 'ready' && !groups[source.id].error" type="button" class="search-button search-more" :disabled="groups[source.id].loading" @click="loadSource(source.id, 0)">重新查询此来源</button>
          </section>
        </div>
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { isNavigationFailure } from 'vue-router';
import { usePrivacyStore } from '~/stores/privacy';
import { useUnifiedSearchApi } from '~/composables/useUnifiedSearchApi';

const props = defineProps({ account: { type: String, required: true }, q: { type: String, default: '' }, start: { type: String, default: '' }, end: { type: String, default: '' }, revision: { type: Number, default: 0 } });
defineEmits(['attachments']);
const { privacyMode } = storeToRefs(usePrivacyStore()), api = useUnifiedSearchApi();
const navigation = useState('ai-navigation-target', () => null), libraryNavigation = useState('library-navigation-target', () => null);
const sourceOptions = [{ id: 'chat', label: '聊天记录' }, { id: 'documents', label: '附件正文' }, { id: 'ocr', label: 'OCR 文字' }, { id: 'notes', label: '资料夹内容与备注' }];
const statusLabels = { unavailable: '此来源暂不可用，请查看上方覆盖说明。', index_building: '索引正在准备，完成后可重新查询此来源。', index_error: '索引准备失败，请查看上方原因并重试。' };
const fieldLabels = { title: '标题', content: '正文', notes: '备注', tags: '标签', url: '链接', quoteTitle: '引用标题', quoteContent: '引用正文', amount: '金额', extracted_text: '已提取文字' };
const countLabels = { complete: '完整提取', partial: '部分提取', no_text: '未提取到文字', not_extracted: '尚未提取', stale: '来源已变化', document_text: '文档提取', local_ocr: 'OCR 识别' };
const kindLabels = { message: '聊天原文', report: 'AI 报告', attachment: '附件副本', extracted_text: '识别/提取文字' };
const selectedSources = ref(sourceOptions.map(source => source.id));
const groups = ref({}), submitted = ref(false), formError = ref(''), notice = ref('');
const activeSources = computed(() => sourceOptions.filter(source => groups.value[source.id]));
const controllers = new Map();
let generation = 0, submittedQuery;
const message = cause => typeof cause.data?.detail === 'string' ? cause.data.detail : cause.data?.detail?.message || cause.message;
const count = value => value === null ? '未知' : value.toLocaleString();
const formatElapsed = value => value.toLocaleString(undefined, { maximumFractionDigits: 1 });
const formatDate = value => new Date(value * 1000).toLocaleString();

function invalidate(reason) {
  generation++;
  for (const controller of controllers.values()) controller.abort();
  controllers.clear(); groups.value = {}; submitted.value = false; submittedQuery = null; formError.value = ''; notice.value = reason;
}
watch([() => props.q, selectedSources, () => props.start, () => props.end], () => invalidate('搜索条件已改变，请重新提交查询。'), { deep: true, flush: 'sync' });
watch(() => props.account, () => invalidate('账号已改变，请重新提交查询。'), { flush: 'sync' });
watch(() => props.revision, () => invalidate('资料已恢复，原搜索结果已失效，请重新提交查询。'), { flush: 'sync' });

async function search() {
  invalidate('');
  const text = props.q.trim();
  if (!text) { formError.value = '请输入要查找的关键词。'; return; }
  if (text.split(/\s+/).length > 8) { formError.value = '每次最多搜索 8 个词，请减少空格分隔的关键词。'; return; }
  if (!props.account) { formError.value = '请选择资料账号。'; return; }
  if (!selectedSources.value.length) { formError.value = '请至少选择一个搜索来源。'; return; }
  const start = props.start ? Math.floor(new Date(`${props.start}T00:00:00`).getTime() / 1000) : null;
  const end = props.end ? Math.floor(new Date(`${props.end}T23:59:59`).getTime() / 1000) : null;
  if (start !== null && end !== null && start > end) { formError.value = '开始日期不能晚于结束日期。'; return; }
  submittedQuery = { account: props.account, q: text, ...(start === null ? {} : { start_time: start }), ...(end === null ? {} : { end_time: end }) };
  for (const source of selectedSources.value) groups.value[source] = { items: [], result: null, loading: false, error: '', actionError: '', failedOffset: 0 };
  submitted.value = true;
  await Promise.all(selectedSources.value.map(source => loadSource(source, 0)));
}
async function loadSource(source, offset) {
  const token = generation, group = groups.value[source], { account, ...parameters } = submittedQuery;
  controllers.get(source)?.abort();
  const controller = new AbortController(); controllers.set(source, controller);
  const current = () => token === generation && controllers.get(source) === controller;
  group.loading = true; group.error = ''; group.failedOffset = offset;
  try {
    const result = await api.search(account, { ...parameters, source, offset, limit: 20 }, controller.signal);
    if (!current()) return;
    group.result = result;
    group.items = result.status === 'ready' ? (offset ? [...group.items, ...result.items] : result.items) : [];
  } catch (cause) {
    if (current() && !controller.signal.aborted) group.error = message(cause);
  } finally {
    if (current()) group.loading = false;
  }
}
async function openResult(source, item) {
  const token = generation, group = groups.value[source], account = submittedQuery.account;
  const state = item.library_item_id ? libraryNavigation : navigation;
  state.value = item.library_item_id ? { account, item_id: item.library_item_id } : { kind: 'source', origin: 'search', account, username: item.username, anchor: item.anchor };
  const target = state.value;
  group.actionError = '';
  try {
    const result = await navigateTo(item.library_item_id ? '/library' : '/chat');
    if (result === false || isNavigationFailure(result)) throw new Error('未能打开来源，请重新点击。');
  } catch (cause) {
    if (state.value === target) state.value = null;
    if (token === generation) group.actionError = message(cause);
  }
}
defineExpose({ search });
onUnmounted(() => invalidate(''));
</script>

<style scoped>
.search-scope-note, .search-meta, .search-coverage, .search-match { font-size: 12px; line-height: 1.7; color: var(--app-text-secondary); }
.search-sources input { accent-color: var(--app-accent); }
.search-button { min-height: 36px; padding: 7px 12px; border: 1px solid var(--app-border); border-radius: 7px; color: var(--app-text-primary); background: var(--app-surface-bg); font-size: 13px; white-space: nowrap; cursor: pointer; }
.search-button:hover:not(:disabled) { background: var(--app-surface-muted); }
.search-button:disabled { opacity: .55; cursor: default; }
.search-button:focus-visible, .search-sources input:focus-visible { outline: 2px solid var(--app-accent); outline-offset: 2px; }
.search-sources { display: flex; gap: 8px 22px; flex-wrap: wrap; padding: 0; margin: 20px 0 14px; border: 0; }
.search-sources legend { margin-bottom: 6px; font-size: 12px; color: var(--app-text-secondary); }
.search-sources label { display: flex; align-items: center; gap: 7px; min-height: 30px; font-size: 12px; }
.search-scope-note { margin: 16px 0 20px; }
.search-state { padding: 14px 0; font-size: 13px; color: var(--app-text-secondary); }
.search-error { font-size: 13px; color: var(--danger-color, #b91c1c); line-height: 1.7; overflow-wrap: anywhere; }
.search-group { border-top: 1px solid var(--app-border); padding: 20px 0 12px; }
.search-group-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.search-group h2 { margin: 0; font-size: 17px; font-weight: 600; }
.search-coverage { margin: 8px 0 16px; overflow-wrap: anywhere; }
.search-coverage-warning { margin: 8px 0 12px; font-size: 13px; color: var(--app-text-primary); }
.search-extraction-counts { display: flex; flex-wrap: wrap; gap: 5px 18px; list-style: none; padding: 0; margin: 8px 0 16px; font-size: 12px; color: var(--app-text-secondary); font-variant-numeric: tabular-nums; }
.search-revoked { color: var(--app-text-primary); }
.search-result { display: flex; gap: 20px; align-items: start; padding: 16px 0; border-top: 1px solid var(--app-border); }
.search-result-copy { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.search-result h3 { font-size: 14px; font-weight: 600; margin: 0 0 5px; }
.search-meta, .search-match { margin: 4px 0; }
.search-snippet { white-space: pre-wrap; margin: 8px 0 0; line-height: 1.7; font-size: 14px; max-width: 76ch; }
.search-more { margin: 12px 0 4px; }
@media (max-width: 680px) {
  .search-result { flex-direction: column; gap: 12px; }
}
</style>
