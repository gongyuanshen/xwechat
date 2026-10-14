<template>
  <section class="dossier-panel" aria-label="专题档案">
    <div class="dossier-toolbar">
      <h2>专题档案</h2>
      <button :disabled="saving || downloading" type="button" @click="$emit('close')">返回资料列表</button>
      <button :disabled="loading || saving" type="button" @click="load">刷新变化对照</button>
    </div>
    <p class="intro">将不同聊天的原文、附件和报告保存到同一资料夹。每次整理后保存版本，下次可对照新增资料与内容变化。</p>
    <p v-if="error" role="alert" class="dossier-error" :class="{ 'privacy-blur': privacyMode }">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="loading" role="status">正在读取专题版本…</p>
    <div v-if="preview" class="dossier-content">
      <label class="history-picker">查看版本
        <select v-model="historyId" name="history-version" :disabled="saving" :class="{ 'privacy-blur': privacyMode }" @change="loadVersion">
          <option value="">当前资料 · 尚未保存为版本</option>
          <option v-for="version in preview.versions" :key="version.id" :value="version.id">第 {{ version.number }} 版 · {{ version.name }} · {{ formatDate(version.created_at) }}</option>
        </select>
      </label>
      <p v-if="historyLoading" role="status">正在读取历史内容…</p>
      <div v-if="!historyId || history" class="dossier-version">
        <h3 :class="{ 'privacy-blur': privacyMode }">{{ history ? `第 ${history.number} 版 · ${history.name}` : preview.folder_name }}</h3>
        <template v-if="history">
          <p class="metadata">保存于 {{ formatDate(history.created_at) }} · {{ history.items.length }} 份资料 · 历史版本只读</p>
          <dl class="version-notes">
            <dt>安排变化（人工记录）</dt><dd :class="{ 'privacy-blur': privacyMode }">{{ history.arrangements || '本版未填写' }}</dd>
            <dt>待修正结论（人工记录）</dt><dd :class="{ 'privacy-blur': privacyMode }">{{ history.conclusions || '本版未填写' }}</dd>
          </dl>
          <div class="dossier-toolbar">
            <button type="button" aria-label="基于此版本提问" :disabled="asking" @click="askVersion">{{ asking ? '正在打开 AI 助手…' : '基于此版本提问' }}</button>
            <button type="button" :aria-pressed="historyView === 'changes'" @click="historyView = 'changes'">与前一版本对照</button>
            <button type="button" :aria-pressed="historyView === 'materials'" @click="historyView = 'materials'">此版本全部资料</button>
          </div>
        </template>
        <p v-else class="metadata">当前 {{ preview.item_count }} 份资料 · {{ preview.versions.length ? `与第 ${preview.versions[0].number} 版对照` : '尚无历史版本，保存后建立第一次记录' }}</p>
        <div v-if="history && historyView === 'materials'" class="version-materials">
          <p v-if="!history.items.length">此版本未收录资料。</p>
          <LibraryDossierEvidence v-for="item in history.items" :key="item.id" :item="item" :version-id="history.id" :privacy-mode="privacyMode" :busy="downloading" @locate="$emit('locate', $event)" @download="download" />
        </div>
        <div v-else class="dossier-changes">
          <p class="change-counts">新增 {{ changes.added.length }} · 移除 {{ changes.removed.length }} · 修改 {{ changes.changed.length }}</p>
          <p v-if="!history && preview.previous_folder_name && preview.previous_folder_name !== preview.folder_name" :class="{ 'privacy-blur': privacyMode }">专题名称：{{ preview.previous_folder_name }} → {{ preview.folder_name }}</p>
          <p v-if="!changes.added.length && !changes.removed.length && !changes.changed.length">材料内容与前一版本一致。仍可记录安排变化或待修正结论。</p>
          <section v-for="category in ['added', 'removed']" :key="category">
            <h4 v-if="changes[category].length">{{ category === 'added' ? '新增资料' : '移出当前专题' }}</h4>
            <LibraryDossierEvidence v-for="item in changes[category]" :key="`${category}:${item.id}`" :item="item" :version-id="category === 'removed' ? previousVersionId : history?.id"
              :privacy-mode="privacyMode" :busy="downloading" @locate="$emit('locate', $event)" @download="download" />
          </section>
          <h4 v-if="changes.changed.length">修改资料 · 前后对照</h4>
          <article v-for="change in changes.changed" :key="change.after.id" class="change-comparison">
            <p class="metadata">变更字段：{{ change.fields.map(fieldLabel).join('、') }}</p>
            <div class="comparison-columns">
              <div><h5>前一版本</h5><LibraryDossierEvidence :item="change.before" :version-id="previousVersionId" expanded :privacy-mode="privacyMode" :busy="downloading" @locate="$emit('locate', $event)" @download="download" /></div>
              <div><h5>{{ history ? '此版本' : '当前资料' }}</h5><LibraryDossierEvidence :item="change.after" :version-id="history?.id" expanded :privacy-mode="privacyMode" :busy="downloading" @locate="$emit('locate', $event)" @download="download" /></div>
            </div>
          </article>
        </div>
        <form v-if="!historyId" class="version-form" @submit.prevent="saveVersion">
          <fieldset :disabled="saving || loading || busy">
            <legend>保存这次整理</legend>
            <label>版本名称<input v-model="draft.name" name="version-name" maxlength="200" required placeholder="例如：投稿前检查、行程调整" :class="{ 'privacy-blur': privacyMode }" /></label>
            <label>安排变化（人工记录）<textarea v-model="draft.arrangements" name="arrangements" rows="3" maxlength="20000" placeholder="哪些日期、分工或计划发生了变化？" :class="{ 'privacy-blur': privacyMode }" /></label>
            <label>待修正结论（人工记录）<textarea v-model="draft.conclusions" name="conclusions" rows="3" maxlength="20000" placeholder="哪些旧结论需要重新核对？可注明已修正结果。" :class="{ 'privacy-blur': privacyMode }" /></label>
            <p class="metadata">保存当前已提交的资料及引用。安排与结论按你的记录保留；不会自动进行 AI 分析。历史版本随资料夹一起删除。</p>
            <button class="save-version" :disabled="saving || !draft.name.trim()" type="submit">{{ saving ? '正在保存…' : '保存新版本' }}</button>
          </fieldset>
        </form>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { isNavigationFailure } from 'vue-router';
import { useLibraryApi } from '~/composables/useLibraryApi';
import { saveAttachmentDownload } from '~/utils/attachmentDownload';
import LibraryDossierEvidence from './LibraryDossierEvidence.vue';
const props = defineProps({ account: { type: String, required: true }, folderId: { type: String, required: true },
  privacyMode: Boolean, busy: Boolean, revision: String });
const emit = defineEmits(['locate', 'close', 'dirty', 'busy']);
const api = useLibraryApi();
const preview = ref(null), history = ref(null), historyId = ref(''), historyView = ref('changes');
const loading = ref(false), saving = ref(false), downloading = ref(false), historyLoading = ref(false);
const error = ref(''), notice = ref(''), draft = ref({ name: '', arrangements: '', conclusions: '' });
const asking = ref(false);
const agentNavigation = useState('agent-library-navigation-target', () => null);
let generation = 0, previewRequest = 0, versionRequest = 0;
const message = e => typeof e.data?.detail === 'string' ? e.data.detail : e.data?.detail?.message || e.message;
const formatDate = seconds => new Date(seconds * 1000).toLocaleString();
const fieldLabel = key => ({ title: '标题', content: '正文', notes: '备注', tags: '标签', verified: '核验状态', user_edited: '人工编辑标记', source: '原始来源', report: '原始报告', attachment: '附件', extraction: '提取记录' })[key] || key;
const changes = computed(() => history.value ? history.value.changes : preview.value.changes);
const previousVersionId = computed(() => history.value ? history.value.previous_id : preview.value.versions[0]?.id);
watch(draft, value => emit('dirty', Object.values(value).some(text => text.trim())), { deep: true });
watch([saving, downloading], ([write, download]) => emit('busy', write || download));
watch(() => [props.account, props.folderId], () => {
  generation++; versionRequest++;
  preview.value = null; history.value = null; historyId.value = ''; error.value = ''; notice.value = '';
  draft.value = { name: '', arrangements: '', conclusions: '' };
  saving.value = false; downloading.value = false; historyLoading.value = false;
  load();
}, { immediate: true });
watch(() => props.revision, () => load());
onBeforeUnmount(() => { generation++; });
async function askVersion() {
  const version = history.value, token = generation;
  agentNavigation.value = { account: props.account, folder_id: props.folderId, folder_name: version.folder_name,
    version_id: version.id, version_name: version.name, version_number: version.number, saved_at: version.created_at, item_count: version.items.length };
  const target = agentNavigation.value;
  asking.value = true; error.value = '';
  try {
    const result = await navigateTo('/agent');
    if (result === false || isNavigationFailure(result)) throw new Error('未能打开 AI 助手，请重试。');
  } catch (cause) {
    if (agentNavigation.value === target) agentNavigation.value = null;
    if (token === generation) error.value = message(cause);
  } finally { if (token === generation) asking.value = false; }
}
async function load() {
  const token = generation, request = ++previewRequest;
  loading.value = true; error.value = ''; preview.value = null;
  try {
    const result = await api.getDossier(props.account, props.folderId);
    if (token === generation && request === previewRequest) preview.value = result;
  } catch (e) {
    if (token === generation && request === previewRequest) error.value = message(e);
  } finally {
    if (token === generation && request === previewRequest) loading.value = false;
  }
}
async function loadVersion() {
  const token = generation, request = ++versionRequest, id = historyId.value;
  history.value = null; error.value = ''; historyView.value = 'changes';
  if (!id) { historyLoading.value = false; return; }
  historyLoading.value = true;
  try {
    const result = await api.getVersion(props.account, props.folderId, id);
    if (token === generation && request === versionRequest) history.value = result;
  } catch (e) {
    if (token === generation && request === versionRequest) error.value = message(e);
  } finally {
    if (token === generation && request === versionRequest) historyLoading.value = false;
  }
}
async function saveVersion() {
  const token = generation;
  saving.value = true; error.value = ''; notice.value = '';
  try {
    const version = await api.saveVersion(props.account, props.folderId, { ...draft.value, name: draft.value.name.trim(), expected_token: preview.value.token });
    if (token !== generation) return;
    notice.value = `已保存第 ${version.number} 版，可在“查看版本”中回看。`;
    draft.value = { name: '', arrangements: '', conclusions: '' };
    await load();
  } catch (e) {
    if (token === generation) error.value = message(e);
  } finally {
    if (token === generation) saving.value = false;
  }
}
async function download({ item, versionId }) {
  const token = generation;
  downloading.value = true; error.value = '';
  try {
    const result = versionId
      ? await api.downloadVersionAttachment(props.account, props.folderId, versionId, item.id)
      : await api.downloadAttachment(props.account, item.id);
    if (token === generation) saveAttachmentDownload(result);
  } catch (e) {
    if (token === generation) error.value = message(e);
  } finally {
    if (token === generation) downloading.value = false;
  }
}
</script>

<style scoped>
.dossier-panel { min-width: 0; padding: 18px 24px; overflow: auto; font-size: 13px; line-height: 1.65; }
.dossier-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
h2 { font-size: 16px; font-weight: 600; margin-right: auto; }
h3 { font-size: 17px; margin-top: 24px; font-weight: 600; overflow-wrap: anywhere; }
h4 { margin-top: 22px; font-weight: 600; }
h5 { color: var(--app-text-secondary); font-weight: 500; }
.intro, .metadata { color: var(--app-text-secondary); margin: 10px 0; }
.intro { max-width: 70ch; }
.history-picker { margin: 20px 0; }
label { display: grid; gap: 6px; margin: 14px 0; }
input, textarea, select { width: 100%; min-width: 0; }
button, input, textarea, select { border: 1px solid var(--app-border); border-radius: 6px; padding: 7px 10px; background: var(--app-surface-bg); color: inherit; font: inherit; }
button { white-space: nowrap; }
button:hover, button[aria-pressed='true'] { background: var(--app-surface-muted); }
button:disabled { opacity: .5; }
button:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible { outline: 2px solid var(--app-accent); outline-offset: 2px; }
textarea { resize: vertical; }
.version-form { margin-top: 28px; padding-top: 20px; border-top: 1px solid var(--app-border); }
fieldset { border: 0; padding: 0; min-width: 0; }
legend { font-size: 15px; font-weight: 600; }
.save-version { border-color: var(--app-accent); color: var(--app-accent); }
.dossier-error { color: var(--danger-color, #b91c1c); }
.change-counts { font-variant-numeric: tabular-nums; margin-top: 14px; }
.change-comparison { padding-top: 8px; }
.comparison-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.version-notes { display: grid; gap: 6px; margin: 18px 0; }
.version-notes dt { color: var(--app-text-secondary); margin-top: 8px; }
.version-notes dd { white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 1200px) { .comparison-columns { grid-template-columns: minmax(0, 1fr); gap: 14px; } }
@media (max-width: 600px) { .dossier-panel { padding: 18px; } .dossier-toolbar h2 { flex-basis: 100%; } }
</style>
