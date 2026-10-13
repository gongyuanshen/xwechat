<template>
  <main class="records-page unified-search-page">
    <div class="records-page__scroll">
      <div class="records-page__frame">
        <header class="records-masthead">
          <div class="records-masthead__identity"><h1>搜索</h1></div>
          <button type="button" class="search-button" @click="navigateTo('/library')">打开本地资料夹</button>
        </header>
        <p class="search-intro">查找聊天、附件文字和资料笔记；切换到“附件”可浏览文件、下载或识别文字。</p>
        <div class="search-views" role="group" aria-label="查看范围">
          <button type="button" :aria-pressed="view === 'all'" @click="changeView('all')">全部内容</button>
          <button type="button" :aria-pressed="view === 'attachments'" @click="changeView('attachments')">附件</button>
        </div>
        <p v-if="accounts.error" class="search-error privacy-content" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ accounts.error }} <button type="button" class="search-button" @click="accounts.ensureLoaded({ force: true })">重试账号加载</button></p>
        <form class="search-form" @submit.prevent="search">
          <div class="search-controls">
            <label class="search-account">账号<select :value="selectedAccount" :disabled="accounts.loading" :class="{ 'privacy-blur': privacyMode }" @change="accounts.setSelectedAccount($event.target.value)"><option v-if="!accounts.accounts.length" value="">暂无资料账号</option><option v-for="account in accounts.accounts" :key="account" :value="account">{{ account }}</option></select></label>
            <label class="search-query">关键词<input v-model="query" :class="{ 'privacy-blur': privacyMode }" type="search" :placeholder="view === 'attachments' ? '附件名称或已识别文字，留空浏览附件' : '例如：合同 付款'" autocomplete="off" aria-describedby="search-keyword-help" /></label>
            <button type="submit" class="search-button primary" :disabled="!selectedAccount || accounts.loading || (view === 'attachments' && !hasChat)">{{ view === 'attachments' ? '查询附件' : '搜索' }}</button>
          </div>
          <p id="search-keyword-help" class="search-help">空格分隔的词需全部命中，最多 8 个词。只搜索已有内容，提取与识别需手动执行。</p>
          <div class="search-dates"><label>开始日期<input v-model="startDate" type="date" /></label><label>结束日期<input v-model="endDate" type="date" /></label><p>聊天与附件按消息时间，资料按更新时间筛选。</p></div>
        </form>
        <p v-if="formError" class="search-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ formError }}</p>
        <p v-if="!selectedAccount" class="search-state">暂无应用资料账号。可导入微信账号，或从“资料备份”恢复资料。</p>
        <ContentSearchResults v-else-if="view === 'all'" ref="panel" :account="selectedAccount" :q="query" :start="startDate" :end="endDate" :revision="revisions[selectedAccount] || 0" @attachments="changeView('attachments')" />
        <AttachmentBrowser v-else-if="hasChat" ref="panel" :account="selectedAccount" :q="query" :start="startDate" :end="endDate" :revision="revisions[selectedAccount] || 0" />
        <p v-else class="search-state" role="status">该账号尚未导入聊天数据库，无法浏览聊天附件。切换到“全部内容”可搜索已恢复的资料与笔记；保存的附件副本可在本地资料夹中查看。</p>
      </div>
    </div>
  </main>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { isNavigationFailure } from 'vue-router';
import ContentSearchResults from '~/components/search/ContentSearchResults.vue';
import AttachmentBrowser from '~/components/search/AttachmentBrowser.vue';
import { useApplicationAccountsStore } from '~/stores/applicationAccounts';
import { usePrivacyStore } from '~/stores/privacy';
import '~/assets/css/record-pages.css';

useHead({ title: '搜索 - xwechat' });
const accounts = useApplicationAccountsStore(), { selectedAccount, revisions } = storeToRefs(accounts);
const { privacyMode } = storeToRefs(usePrivacyStore());
const route = useRoute(), view = computed(() => route.query.view === 'attachments' ? 'attachments' : 'all');
const hasChat = computed(() => accounts.chatAccounts.includes(selectedAccount.value));
const query = ref(''), startDate = ref(''), endDate = ref(''), panel = ref(null), formError = ref('');
watch([query, startDate, endDate, selectedAccount, view], () => { formError.value = ''; });

async function changeView(next) {
  if (next === view.value) return;
  try {
    const result = await navigateTo({ path: '/search', query: next === 'attachments' ? { view: next } : {} }, { replace: true });
    if (result === false || isNavigationFailure(result)) throw new Error('未能切换搜索范围，请重试。');
  } catch (cause) { formError.value = cause.message; }
}
function search() {
  formError.value = '';
  if (!selectedAccount.value) { formError.value = '请选择资料账号。'; return; }
  if (view.value === 'attachments' && !hasChat.value) { formError.value = '该账号尚未导入聊天数据库，无法查询聊天附件。'; return; }
  if (query.value.trim().split(/\s+/).filter(Boolean).length > 8) { formError.value = '每次最多搜索 8 个词，请减少空格分隔的关键词。'; return; }
  if (startDate.value && endDate.value && startDate.value > endDate.value) { formError.value = '开始日期不能晚于结束日期。'; return; }
  return panel.value.search();
}
onMounted(() => accounts.ensureLoaded());
</script>

<style scoped>
.unified-search-page .records-page__scroll { overflow-y: auto; }
.unified-search-page .records-page__frame { height: auto; min-height: 100%; overflow: visible; padding: 0 24px 24px; }
.unified-search-page .records-masthead { margin: 0 -24px 20px; }
.search-intro { margin: 0 0 16px; font-size: 14px; line-height: 1.7; color: var(--app-text-secondary); }
.search-views { display: flex; gap: 20px; margin-bottom: 20px; border-bottom: 1px solid var(--app-border); }
.search-views button { padding: 8px 2px 12px; font-size: 14px; border-bottom: 2px solid transparent; color: var(--app-text-secondary); }
.search-views button[aria-pressed=true] { color: var(--app-text-primary); border-color: var(--app-accent); font-weight: 600; }
.search-controls { display: flex; align-items: end; gap: 12px; flex-wrap: wrap; }
.search-form label { display: grid; gap: 6px; min-width: 0; font-size: 12px; }
.search-account { flex: 1 1 180px; }
.search-query { flex: 3 1 300px; }
.search-form :is(input[type=search], input[type=date], select) { width: 100%; min-width: 0; height: 38px; padding: 7px 10px; border: 1px solid var(--app-border); border-radius: 7px; background: var(--app-surface-bg); color: var(--app-text-primary); caret-color: var(--app-accent); }
.search-button { min-height: 36px; padding: 7px 12px; border: 1px solid var(--app-border); border-radius: 7px; color: var(--app-text-primary); background: var(--app-surface-bg); font-size: 13px; white-space: nowrap; cursor: pointer; }
.search-button:hover:not(:disabled) { background: var(--app-surface-muted); }
.search-button:disabled { opacity: .55; cursor: default; }
.search-button.primary { background: var(--chat-accent); color: var(--chat-input-bg); border-color: var(--chat-accent); }
.search-button.primary:hover:not(:disabled) { background: var(--chat-accent-hover); }
.unified-search-page :is(button, input, select):focus-visible { outline: 2px solid var(--app-accent); outline-offset: 2px; }
.search-help, .search-dates p { font-size: 12px; line-height: 1.7; color: var(--app-text-secondary); }
.search-help { margin: 8px 0 14px; }
.search-dates { display: flex; gap: 12px; align-items: end; flex-wrap: wrap; margin-bottom: 20px; }
.search-dates label { flex: 0 1 170px; }
.search-dates p { margin: 0 0 8px; }
.search-state { padding: 14px 0; font-size: 13px; line-height: 1.7; color: var(--app-text-secondary); }
.search-error { font-size: 13px; color: var(--danger-color, #b91c1c); line-height: 1.7; overflow-wrap: anywhere; }
@media (max-width: 680px) {
  .unified-search-page { padding: 8px; }
  .unified-search-page .records-page__frame { padding: 0 16px 16px; }
  .unified-search-page .records-masthead { margin: 0 -16px 16px; gap: 10px; }
  .search-controls .primary { width: 100%; }
  .search-query { flex-basis: 100%; }
  .search-dates label { flex: 1 1 130px; }
}
</style>
