<template>
  <main class="records-page time-machine-page">
    <div class="records-page__scroll"><div class="records-page__frame">
      <header class="records-masthead">
        <div class="records-masthead__identity"><h1>聊天时光机</h1></div>
        <button class="time-button" type="button" :disabled="!selectedAccount || refreshing" @click="refreshIndex">{{ refreshing ? '正在请求…' : '更新本地索引' }}</button>
      </header>
      <div class="time-workspace">
        <p class="time-intro">把不同年份的聊天并排打开。选一个月日看往年记录，或回到一段具体的日子。</p>
        <p v-if="accounts.error" class="time-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ accounts.error }}</p>
        <p v-if="!selectedAccount" class="time-state">请先选择已解密或已导入的微信账号。</p>
        <template v-else>
          <form class="time-filters" @submit.prevent="explore">
            <label>回看方式<select v-model="filters.mode"><option value="day">往年同日</option><option value="range">一段时间</option></select></label>
            <label v-if="filters.mode === 'day'">选择对照日期<input v-model="filters.day" type="date" required /><small>取月和日，对照各个年份</small></label>
            <template v-else><label>开始日期<input v-model="filters.start" type="date" required /></label><label>结束日期<input v-model="filters.end" type="date" :min="filters.start" required /></label></template>
            <label class="time-scope">聊天范围<select v-model="filters.username" :class="{ 'privacy-blur': privacyMode }"><option value="">全部普通聊天</option><option v-for="item in catalog?.conversations || []" :key="item.username" :value="item.username">{{ item.name }}{{ item.username.endsWith('@chatroom') ? ' · 群聊' : '' }}</option></select></label>
            <button type="submit" class="time-button primary" :disabled="loading">{{ loading ? '正在回看…' : '开始回看' }}</button>
            <button v-if="filters.mode === 'day'" type="button" class="time-button" @click="today">回到今日</button>
          </form>
          <p class="time-meta">仅显示当前账号的本地聊天，不包含公众号和隐藏会话。原文与图片保留出处，不会调用 AI。</p>
          <p v-if="applied" class="time-meta" :class="{ 'privacy-blur': privacyMode }">当前显示：{{ applied.name }} · {{ applied.mode === 'day' ? `${applied.day.slice(5).replace('-', ' 月 ')} 日的跨年记录` : `${applied.start} 至 ${applied.end}` }}。修改筛选后点击“开始回看”应用。</p>
          <p v-if="catalog" class="time-meta">索引建立于 {{ new Date(catalog.built_at * 1000).toLocaleString('zh-CN') }}；日期按此电脑时区（{{ catalog.timezone }}）划分。<strong v-if="!catalog.up_to_date">源数据已有更新，点击“更新本地索引”后重新回看。</strong></p>
          <p v-if="notice" class="time-notice" role="status">{{ notice }}</p>
          <p v-if="error || navigationError" class="time-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ error || navigationError }} <button type="button" class="time-button" @click="explore">重新回看</button></p>
          <p v-if="loading && !columns.length" class="time-state" role="status">正在读取年份与聊天目录…</p>
          <p v-else-if="catalog && !columns.length && !error" class="time-state">当前筛选范围内没有可回看的聊天年份。</p>
          <div v-if="columns.length" class="time-years" aria-label="按年份对照聊天" tabindex="0">
            <section v-for="column in columns" :key="`${selectedAccount}:${catalog.revision}:${column.year}:${column.start}:${column.end}`" class="time-year" :aria-label="`${column.year} 年聊天`">
              <header class="time-year-heading"><h2>{{ column.year }}</h2><p>{{ column.start === column.end ? column.start.slice(5).replace('-', ' / ') : `${column.start} — ${column.end}` }}</p></header>
              <p v-if="column.missingDate" class="time-state">这一年没有 2 月 29 日。</p>
              <template v-else>
                <TimeMachineMessage v-for="item in column.items" :key="`${item.username}:${item.id}`" :item="item" :image-url="attachments.contentUrl(selectedAccount, item)" :privacy="privacyMode" @locate="locate" />
                <p v-if="column.error" class="time-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ column.error }} <button class="time-button" type="button" :disabled="column.loading" @click="loadColumn(column)">重试本页</button></p>
                <p v-else-if="column.loading" class="time-state" role="status">正在读取聊天…</p>
                <p v-else-if="column.loaded && !column.items.length" class="time-state">这些日子没有聊天记录。</p>
                <p v-else-if="column.loaded" class="time-count">已显示 {{ column.items.length }} 条{{ column.hasMore ? '' : ' · 本时段已读完' }}</p>
                <button v-if="column.hasMore && !column.error" type="button" class="time-button time-more" :disabled="column.loading" @click="loadColumn(column)">继续读取 {{ column.year }} 年</button>
              </template>
            </section>
          </div>
        </template>
      </div>
    </div></div>
  </main>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { isNavigationFailure } from 'vue-router';
import { useChatAccountsStore } from '~/stores/chatAccounts';
import { usePrivacyStore } from '~/stores/privacy';
import { localDate, timeMachineError, useTimeMachine } from '~/composables/useTimeMachine';
import TimeMachineMessage from '~/components/TimeMachineMessage.vue';
import '~/assets/css/record-pages.css';

useHead({ title: '聊天时光机 - xwechat' });
const accounts = useChatAccountsStore(), { selectedAccount } = storeToRefs(accounts);
const { privacyMode } = storeToRefs(usePrivacyStore());
const filters = ref({ mode: 'day', day: localDate(), start: localDate(), end: localDate(), username: '' });
const { catalog, columns, applied, error, loading, refreshing, notice, explore, loadColumn, refreshIndex } = useTimeMachine(selectedAccount, filters);
const attachments = useAttachmentsApi(), navigation = useState('ai-navigation-target', () => null), navigationError = ref('');
watch(selectedAccount, () => { navigationError.value = ''; });
onMounted(() => accounts.ensureLoaded());
function today() { filters.value.day = localDate(); void explore(); }
async function locate(item) {
  const account = selectedAccount.value;
  navigationError.value = ''; navigation.value = { kind: 'source', account, username: item.username, anchor: item.id };
  const target = navigation.value;
  try {
    const result = await navigateTo('/chat');
    if (result === false || isNavigationFailure(result)) throw new Error('未能打开聊天原消息，请重试。');
  } catch (e) {
    if (navigation.value === target) navigation.value = null;
    if (selectedAccount.value === account) navigationError.value = timeMachineError(e);
  }
}
</script>

<style scoped>
.time-workspace { min-height: 0; padding: 0 24px 24px; overflow-y: auto; }
.time-intro { margin: 18px 0; color: var(--wx-text-secondary); font-size: 14px; line-height: 1.7; }
.time-filters { display: flex; align-items: start; flex-wrap: wrap; gap: 14px; padding: 20px 0; border-top: 1px solid var(--border-color); border-bottom: 1px solid var(--border-color); }
.time-filters label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--wx-text-secondary); }
.time-filters input, .time-filters select { min-height: 36px; padding: 6px 10px; max-width: 100%; background: var(--chat-input-bg); color: var(--text-primary); border: 1px solid var(--chat-input-border); border-radius: 8px; font: inherit; font-size: 13px; }
.time-scope { width: min(240px, 100%); }
.time-filters small { font-size: 11px; color: var(--wx-text-muted); }
.time-filters > button { margin-top: 24px; }
.time-button { min-height: 34px; padding: 6px 12px; border: 1px solid var(--chat-input-border); border-radius: 8px; background: var(--chat-input-bg); color: var(--chat-accent); font-size: 12px; line-height: 1.5; white-space: nowrap; cursor: pointer; }
.time-button:hover:not(:disabled) { background: var(--chat-subtle-bg); }
.time-button:disabled { opacity: .55; cursor: wait; }
.time-button.primary { background: var(--chat-accent); color: var(--chat-input-bg); }
.time-button.primary:hover:not(:disabled) { background: var(--chat-accent-hover); }
.time-meta { font-size: 12px; color: var(--wx-text-muted); line-height: 1.8; margin: 12px 0; }
.time-meta strong { font-weight: 500; color: var(--wx-amber); }
.time-error { color: var(--wx-red); font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }
.time-notice { color: var(--chat-accent); font-size: 13px; }
.time-state { padding: 24px 0; font-size: 13px; line-height: 1.7; color: var(--wx-text-muted); }
.time-years { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(290px, 1fr); gap: 24px; overflow-x: auto; align-items: start; padding-bottom: 12px; }
.time-year { min-width: 0; }
.time-year-heading { display: flex; align-items: baseline; gap: 14px; padding: 18px 0 12px; border-bottom: 2px solid var(--chat-input-border); }
.time-year-heading h2 { margin: 0; color: var(--chat-accent); font-size: 30px; font-weight: 500; font-variant-numeric: tabular-nums; }
.time-year-heading p { margin: 0; color: var(--wx-text-muted); font-size: 12px; }
.time-count { margin-top: 16px; color: var(--wx-text-muted); font-size: 12px; }
.time-more { margin-top: 10px; }
@media (max-width: 600px) { .time-workspace { padding: 0 14px 16px; } .time-years { grid-auto-columns: min(300px, 85vw); gap: 18px; } .time-filters { gap: 10px; } }
</style>
