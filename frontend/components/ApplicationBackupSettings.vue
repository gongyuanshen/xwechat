<template>
  <div class="backup-settings" :aria-busy="!!pending">
    <p class="backup-scope">备份所选账号的事项、资料夹、笔记、报告、保存的搜索、历史版本及引用附件。聊天数据库请使用账号归档。</p>
    <div class="backup-export">
      <label>应用资料账号
        <select v-model="account" :disabled="!!pending || !accountNames.length" :class="{ 'privacy-blur': privacyMode }">
          <option v-if="!accountNames.length" value="">暂无可用资料账号</option>
          <option v-for="name in accountNames" :key="name" :value="name">{{ name }}</option>
        </select>
      </label>
      <button type="button" :disabled="!account || !!pending" @click="exportBackup">{{ pending === 'export' ? '正在生成备份…' : '下载资料备份' }}</button>
    </div>
    <p v-if="accounts.error" class="backup-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ verification ? '恢复已完成，账号列表刷新失败：' : '' }}{{ accounts.error }} <button type="button" :disabled="accounts.loading" @click="accounts.ensureLoaded({ force: true })">刷新资料账号</button></p>
    <p v-if="!account" class="backup-note">导入微信账号或恢复应用资料后可导出；已有备份可直接在下方恢复。</p>
    <div class="backup-restore">
      <h3>恢复到本机资料</h3>
      <p>选择本应用导出的 ZIP，校验后补回缺失资料。发生冲突的已有资料保留本机版本；冲突及跳过内容会在结果中列出。无需先导入微信账号。</p>
      <p class="backup-note">新恢复的事项会按原提醒设置参与调度，可能触发已到期的提醒。</p>
      <label>资料备份 ZIP
        <input ref="fileInput" type="file" accept=".zip,application/zip" :disabled="!!pending" :class="{ 'privacy-blur': privacyMode }" @change="chooseFile" />
      </label>
      <button type="button" :disabled="!archive || !!pending" @click="restoreBackup">{{ pending === 'restore' ? '正在校验并恢复…' : '恢复缺失资料，保留本机版本' }}</button>
    </div>
    <p v-if="notice" class="backup-notice" role="status">{{ notice }}</p>
    <p v-if="error" class="backup-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ error }}</p>
    <div v-if="verification" class="backup-result">
      <h3 role="status">资料已恢复</h3>
      <p>本次恢复已完成，本机已有版本保持不变；未加入的内容请查看跳过原因。</p>
      <p v-if="verification.cleanup_error" class="backup-error" :class="{ 'privacy-blur': privacyMode }" role="alert">资料已恢复，但暂存文件清理失败：{{ verification.cleanup_error }}</p>
      <p v-if="cleanupNotice" class="backup-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ cleanupNotice }}</p>
      <dl class="backup-details">
        <div><dt>备份账号</dt><dd class="backup-result-account" :class="{ 'privacy-blur': privacyMode }">{{ verification.account }}</dd></div>
        <div><dt>本机资料位置</dt><dd><code class="backup-result-path" :class="{ 'privacy-blur': privacyMode }">{{ verification.data_path }}</code></dd></div>
      </dl>
      <table class="backup-merge-counts"><caption>本次恢复结果</caption><thead><tr><th scope="col">内容</th><th scope="col">新增</th><th scope="col">保留本机</th></tr></thead><tbody><tr v-for="row in mergeLabels" :key="row.key"><th scope="row">{{ row.label }}</th><td>{{ verification.added[row.key] }}</td><td>{{ verification.kept[row.key] }}</td></tr></tbody></table>
      <p class="backup-note">提醒的“保留本机”表示沿用本机事项的提醒状态、未导入备份中的旧通知，不代表本机仍有相同数量的待投递通知。</p>
      <details v-if="verification.skipped.length" class="backup-skipped"><summary>查看 {{ verification.skipped.length }} 项跳过原因</summary><ul :class="{ 'privacy-blur': privacyMode }"><li v-for="(entry, index) in verification.skipped" :key="index">{{ skippedKinds[entry.kind] || entry.kind }} · {{ entry.id }}：{{ skippedReasons[entry.reason] || entry.reason }}</li></ul></details>
      <div class="backup-actions"><button type="button" :disabled="!accountNames.includes(verification.account)" @click="openRestored('/library')">查看资料</button><button type="button" :disabled="!accountNames.includes(verification.account)" @click="openRestored('/matters')">查看事项</button></div>
      <h3 class="backup-archive-heading">备份包中的内容</h3>
      <dl class="backup-counts">
        <div v-for="row in countLabels" :key="row.key"><dt>{{ row.label }}</dt><dd>{{ verification.counts[row.key] }}</dd></div>
      </dl>
      <p class="backup-note">笔记、报告只统计当前资料；历史内容计入版本。附件包含当前资料与历史版本引用的文件，按附件去重。</p>
      <dl class="backup-details">
        <div><dt>备份包附件总大小</dt><dd>{{ formatFileSize(verification.attachment_bytes) }}</dd></div>
        <div><dt>备份包内容 SHA-256</dt><dd><code>{{ verification.content_sha256 }}</code></dd></div>
      </dl>
      <p class="backup-note">原消息定位和重新搜索仍需导入对应账号的聊天数据；已恢复的资料、报告和附件副本可直接查看。</p>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue';
import { isNavigationFailure, NavigationFailureType } from 'vue-router';
import { storeToRefs } from 'pinia';
import { useApplicationAccountsStore } from '~/stores/applicationAccounts';
import { usePrivacyStore } from '~/stores/privacy';
import { formatFileSize as formatSmallFileSize } from '~/lib/chat/formatters';

const accounts = useApplicationAccountsStore();
const { closeDialog } = useSettingsDialog();
const { privacyMode } = storeToRefs(usePrivacyStore());
const { accounts: accountNames } = storeToRefs(accounts);
const account = ref(accounts.selectedAccount);
const apiBase = useApiBase();
const fileInput = ref(null), archive = ref(null), pending = ref('');
const verification = ref(null), error = ref(''), notice = ref(''), cleanupNotice = ref('');
const formatFileSize = bytes => bytes === 0 ? '0 B' : bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : formatSmallFileSize(bytes);
const countLabels = [
  { key: 'matters', label: '事项' }, { key: 'folders', label: '资料夹' }, { key: 'items', label: '当前资料' },
  { key: 'versions', label: '历史版本' }, { key: 'searches', label: '保存的搜索' }, { key: 'notes', label: '当前笔记' },
  { key: 'reports', label: '当前报告' }, { key: 'attachments', label: '附件' }, { key: 'pending_reminders', label: '未投递提醒' },
];
const mergeLabels = countLabels.filter(row => !['notes', 'reports'].includes(row.key));
const skippedKinds = { matter:'事项', library_folder:'资料夹', library_item:'资料', library_version:'历史版本', library_search:'保存的搜索', attachment:'附件', notification:'提醒' };
const skippedReasons = { local_record_kept:'已保留本机已有资料', version_number_conflict:'版本编号与本机冲突，未加入该版本', conflicting_previous_version:'前一版本与本机内容冲突，未加入此版本', missing_previous_version:'缺少前一版本，未加入此版本', local_matter_kept:'已保留本机事项及其提醒安排', existing_notification_kept:'已保留本机已有提醒', local_metadata_or_dependency_kept:'已保留本机附件信息或关联资料，未加入此附件' };
let generation = 0, controller;

function invalidate() {
  generation++; controller?.abort(); pending.value = ''; error.value = ''; notice.value = '';
}
watch(accountNames, names => {
  if (!names.includes(account.value)) account.value = names.includes(accounts.selectedAccount) ? accounts.selectedAccount : names[0] || '';
});
watch(() => accounts.selectedAccount, selected => { account.value = selected; }, { flush: 'sync' });
watch(account, () => {
  if (pending.value === 'export') invalidate();
}, { flush: 'sync' });
onMounted(() => accounts.ensureLoaded());
onUnmounted(invalidate);

function chooseFile(event) {
  invalidate();
  verification.value = null; cleanupNotice.value = '';
  const file = event.target.files[0];
  archive.value = null;
  if (!file) return;
  if (!/\.zip$/i.test(file.name)) { error.value = '请选择本应用导出的 ZIP 资料备份。'; event.target.value = ''; return; }
  archive.value = file;
}

async function errorMessage(cause) {
  let data = cause.data;
  if (data instanceof Blob) {
    try { data = JSON.parse(await data.text()); }
    catch (parseError) { return `错误响应无法解析：${parseError.message}`; }
  }
  return typeof data?.detail === 'string' ? data.detail : data?.detail?.message || cause.message;
}

async function exportBackup() {
  invalidate();
  const token = generation, owner = account.value;
  controller = new AbortController();
  const signal = controller.signal;
  pending.value = 'export';
  try {
    const blob = await $fetch(`${apiBase}/data-backup/export`, { method: 'POST', query: { account: owner }, responseType: 'blob', signal, retry: 0 });
    if (token !== generation) return;
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    try { link.href = url; link.download = 'xwechat-application-data.zip'; link.click(); }
    finally { URL.revokeObjectURL(url); }
    notice.value = '已发起下载，请在下载列表中确认文件已保存。';
  } catch (cause) {
    const detail = await errorMessage(cause);
    if (token === generation && !signal.aborted) error.value = `备份下载失败：${detail}`;
  } finally { if (token === generation) pending.value = ''; }
}

async function restoreBackup() {
  invalidate();
  verification.value = null; cleanupNotice.value = '';
  const token = generation, body = new FormData();
  body.append('file', archive.value);
  controller = new AbortController();
  const signal = controller.signal;
  pending.value = 'restore';
  try {
    const result = await $fetch(`${apiBase}/data-backup/restore`, { method: 'POST', body, signal, retry: 0 });
    if (token !== generation) return;
    if (result.status !== 'restored') throw new Error('恢复响应未确认资料已写入。');
    verification.value = result;
  } catch (cause) {
    const detail = await errorMessage(cause);
    if (token === generation && !signal.aborted) {
      const outcome = cause.data?.detail;
      if (outcome?.code === 'restore_cleanup_failed' && outcome.restored?.status === 'restored') {
        verification.value = outcome.restored; cleanupNotice.value = outcome.message;
      } else error.value = cause.data ? `恢复失败：${detail}` : `恢复结果未确认：${detail} 请先查看本机资料核对结果，不会自动重试。`;
    }
  } finally { if (token === generation) pending.value = ''; }
  if (token === generation && verification.value) {
    accounts.notifyRestored(verification.value.account);
    await accounts.ensureLoaded({ force: true });
  }
}

async function openRestored(path) {
  if (accounts.selectedAccount !== verification.value.account && !window.confirm('查看恢复资料会切换应用资料账号。当前页面如有未保存修改，请先保存；继续切换？')) return;
  accounts.setSelectedAccount(verification.value.account);
  try {
    const result = await navigateTo(path);
    if (result === false || (isNavigationFailure(result) && !isNavigationFailure(result, NavigationFailureType.duplicated))) throw new Error('页面导航被取消，请重新打开。');
    closeDialog();
  }
  catch (cause) { error.value = `资料已恢复，打开页面失败：${cause.message}`; }
}
</script>

<style scoped>
.backup-settings { color: var(--app-text-primary); font-size: 12px; line-height: 1.65; }
.backup-settings p { margin: 8px 0; overflow-wrap: anywhere; }
.backup-settings h3 { margin: 0 0 6px; font-size: 13px; font-weight: 600; }
.backup-scope { max-width: 72ch; }
.backup-export { display: flex; align-items: end; gap: 12px; flex-wrap: wrap; margin: 14px 0 8px; }
.backup-settings label { display: grid; gap: 5px; min-width: 0; }
.backup-export label { flex: 1; max-width: 360px; }
.backup-settings select, .backup-settings button, .backup-settings input { min-height: 32px; max-width: 100%; border: 1px solid var(--app-border, #dfe4df); border-radius: 6px; background: var(--app-surface-bg, #fff); padding: 5px 10px; font: inherit; color: inherit; }
.backup-settings select { width: 100%; min-width: 0; }
.backup-settings input { width: 100%; box-sizing: border-box; }
.backup-settings button { cursor: pointer; white-space: nowrap; }
.backup-settings button:hover:not(:disabled) { background: var(--app-neutral-btn-hover); }
.backup-settings :disabled { opacity: .5; cursor: not-allowed; }
.backup-settings :is(button, select, input):focus-visible { outline: 2px solid var(--app-accent, #079b57); outline-offset: 2px; }
.backup-restore { margin-top: 22px; padding-top: 18px; border-top: 1px solid var(--app-border, #dfe4df); }
.backup-restore label { margin: 12px 0; }
.backup-note { color: var(--app-text-secondary, #666); }
.backup-error { color: #b42318; }
:global(html[data-theme='dark'] .backup-settings .backup-error) { color: var(--setup-danger); }
.backup-notice { font-weight: 500; }
.backup-result { margin-top: 22px; }
.backup-merge-counts { width: 100%; border-collapse: collapse; margin: 14px 0; text-align: left; font-variant-numeric: tabular-nums; }
.backup-merge-counts caption { text-align: left; font-weight: 600; }
.backup-merge-counts :is(th, td) { padding: 6px 8px 6px 0; border-bottom: 1px solid var(--app-border); }
.backup-merge-counts tbody th { font-weight: 400; }
.backup-skipped { margin: 12px 0; overflow-wrap: anywhere; }
.backup-actions { display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0 22px; }
.backup-details { margin: 10px 0; }
.backup-details > div { margin: 8px 0; }
.backup-details dt { color: var(--app-text-secondary, #666); }
.backup-details dd { margin: 2px 0 0; overflow-wrap: anywhere; }
.backup-details code { font-size: 11px; }
.backup-counts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 18px; margin: 14px 0; }
.backup-counts > div { display: flex; justify-content: space-between; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--app-border, #dfe4df); }
.backup-counts dd { margin: 0; font-variant-numeric: tabular-nums; }
@media (max-width: 540px) { .backup-counts { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (pointer: coarse) { .backup-settings :is(button, select, input) { min-height: 44px; } }
</style>
