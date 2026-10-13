<template>
  <main class="matters-page theme-scope">
    <header><div><h1>事项状态面板</h1><p>从聊天原文保存事项，确认期限，持续记录进展。</p><label class="matter-account">应用资料账号<select v-model="selectedAccount" aria-label="应用资料账号" :disabled="busy || accounts.loading" :class="{ 'privacy-blur': privacyMode }"><option v-if="!accounts.accounts.length" value="">暂无资料账号</option><option v-for="name in accounts.accounts" :key="name" :value="name">{{ name }}</option></select></label></div><button :disabled="loading || busy || accounts.loading" @click="refresh">刷新</button></header>
    <p v-if="accounts.error || error" class="matter-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ accounts.error || error }}</p>
    <p v-if="!selectedAccount" class="page-notice">暂无应用资料账号。可以导入微信账号，或在设置的“资料备份”中恢复。</p>
    <p v-else-if="!canOpenChat" class="page-notice">已恢复的事项可查看和更新状态；打开原消息或依据原文确认期限前，请导入对应账号的聊天数据。</p>
    <template v-if="selectedAccount">
      <nav class="matter-filters" aria-label="事项状态筛选"><button v-for="filter in filters" :key="filter.value" :aria-pressed="status === filter.value" :class="{ active: status === filter.value }" @click="status = filter.value">{{ filter.label }} <span>{{ count(filter.value) }}</span></button><input v-model="query" type="search" aria-label="搜索事项标题" placeholder="搜索事项标题" :class="{ 'privacy-blur': privacyMode }" /></nav>
      <p v-if="loading" class="page-notice" role="status">正在加载事项…</p>
      <div v-else class="matters-layout" :class="{ 'has-detail': selected }">
        <section class="matter-list" aria-label="事项列表">
          <p v-if="!filtered.length" class="empty">{{ items.length ? '没有符合筛选条件的事项。' : '还没有保存事项。可在聊天原消息菜单选择“保存事项 / 更新期限”，或在 AI 总结的待办旁选择“保存事项”。' }}</p>
          <button v-for="item in filtered" :key="item.id" class="matter-row" :class="{ active: selected?.id === item.id }" @click="selectedId = item.id">
            <span class="matter-state" :class="item.status">{{ labels[item.status] }}</span><strong :class="{ 'privacy-blur': privacyMode }">{{ item.title }}</strong><small :class="{ overdue: isOverdue(item) }">{{ dueText(item) }}</small>
          </button>
        </section>
        <section v-if="selected" class="matter-detail" aria-label="事项详情">
          <button class="detail-back" @click="selectedId = ''">返回事项列表</button>
          <p class="matter-state" :class="selected.status">{{ labels[selected.status] }}</p><h2 :class="{ 'privacy-blur': privacyMode }">{{ selected.title }}</h2>
          <p :class="{ overdue: isOverdue(selected) }">{{ dueText(selected) }}</p>
          <div class="matter-actions"><button v-for="option in states" :key="option.value" :disabled="busy || selected.status === option.value" @click="update({ status: option.value })">{{ option.action }}</button></div>
          <label class="reminder"><input type="checkbox" :checked="selected.reminder_enabled" :disabled="busy || selected.status === 'done' || selected.due_at === null" @change="update({ reminder_enabled: $event.target.checked })" />到期提醒一次</label>
          <p class="muted">{{ selected.status === 'done' ? '此事项已完成，后续提醒已停止。' : selected.due_at === null ? '尚未确认期限，不能启用到期提醒。' : selected.notified_revision === selected.reminder_revision ? '本次到期提醒已进入通知队列。' : '提醒需保持 xwechat 运行；应用关闭时不会发出通知。' }}</p>
          <h3>原消息</h3><p class="muted">{{ formatDate(selected.source.create_time) }} · <span :class="{ 'privacy-blur': privacyMode }">{{ selected.source.sender }}</span></p><blockquote :class="{ 'privacy-blur': privacyMode }">{{ selected.source.text }}</blockquote>
          <div class="matter-actions"><button @click="locate(selected.source)">打开原消息</button><button v-if="selected.status !== 'done'" :disabled="busy || !canOpenChat" @click="adjusting = true">依据此原文确认期限</button></div>
          <p class="muted">收到后续改期消息时，在该消息菜单选择“保存事项 / 更新期限”，再选“作为已有事项的改期依据”，核对原文并确认新期限。</p>
          <h3>变更记录</h3><ol class="history"><li v-for="(entry, index) in [...selected.history].reverse()" :key="selected.history.length - index"><time>{{ formatDate(entry.at) }}</time><p>{{ historyText(entry) }}</p><template v-if="entry.kind === 'rescheduled'"><blockquote :class="{ 'privacy-blur': privacyMode }">{{ entry.source.text }}</blockquote><button @click="locate(entry.source)">查看改期原消息</button></template></li></ol>
        </section>
        <section v-else class="detail-empty">选择一条事项，查看原文与变更记录。</section>
      </div>
    </template>
    <MatterSaveDialog v-if="adjusting && selected" :account="selectedAccount" :source="selected.source" :matter-id="selected.id" @close="adjusting = false" @saved="replaceItem" />
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { isNavigationFailure } from 'vue-router'
import { useApplicationAccountsStore } from '~/stores/applicationAccounts'
import { usePrivacyStore } from '~/stores/privacy'
import { useMattersApi } from '~/composables/useMattersApi'
import MatterSaveDialog from '~/components/chat/MatterSaveDialog.vue'
useHead({ title: '事项状态面板 - xwechat' })
const accounts = useApplicationAccountsStore(), { selectedAccount, revisions } = storeToRefs(accounts), { privacyMode } = storeToRefs(usePrivacyStore()), api = useMattersApi()
const canOpenChat = computed(() => accounts.chatAccounts.includes(selectedAccount.value))
const navigation = useState('ai-navigation-target', () => null)
const items = ref([]), selectedId = ref(''), status = ref('active'), query = ref(''), loading = ref(false), busy = ref(false), error = ref(''), adjusting = ref(false)
const clock = ref(Date.now())
const labels = { todo: '待办', in_progress: '进行中', done: '已完成' }
const states = [{ value: 'todo', action: '设为待办' }, { value: 'in_progress', action: '开始进行' }, { value: 'done', action: '标记完成' }]
const filters = [{ value: 'active', label: '未完成' }, { value: 'todo', label: '待办' }, { value: 'in_progress', label: '进行中' }, { value: 'done', label: '已完成' }, { value: 'all', label: '全部' }]
const matchesStatus = (item, value) => value === 'all' || (value === 'active' ? item.status !== 'done' : item.status === value)
const count = value => items.value.filter(item => matchesStatus(item, value)).length
const filtered = computed(() => items.value.filter(item => matchesStatus(item, status.value) && item.title.toLowerCase().includes(query.value.toLowerCase())))
const selected = computed(() => items.value.find(item => item.id === selectedId.value))
const formatDate = value => new Date(value * 1000).toLocaleString()
const isOverdue = item => item.status !== 'done' && item.due_at !== null && item.due_at * 1000 < clock.value
const dueText = item => item.due_at === null ? '尚未安排期限' : `${isOverdue(item) ? '已逾期 · ' : '期限 · '}${formatDate(item.due_at)}`
const historyText = entry => {
  if (entry.kind === 'created') return `保存事项 · ${entry.due_at === null ? '期限待确认' : formatDate(entry.due_at)}`
  if (entry.kind === 'rescheduled') return `确认期限：${entry.previous_due_at === null ? '尚未安排' : formatDate(entry.previous_due_at)} → ${formatDate(entry.due_at)}`
  return Object.entries(entry.after).map(([key, value]) => key === 'status' ? `状态：${labels[entry.before.status]} → ${labels[value]}` : key === 'reminder_enabled' ? `到期提醒：${value ? '启用' : '关闭'}` : '更新标题').join('；')
}
const message = e => typeof e.data?.detail === 'string' ? e.data.detail : e.message
let generation = 0, loadRequest = 0, pendingId = '', clockTimer
const load = async () => {
  const account = selectedAccount.value, token = generation, request = ++loadRequest
  if (!account) return
  loading.value = true; error.value = ''
  try {
    const result = await api.list(account)
    if (token !== generation || request !== loadRequest) return
    items.value = result
    if (pendingId) { selectedId.value = pendingId; status.value = 'all'; pendingId = ''; if (!selected.value) error.value = '通知对应的事项已不存在。' }
  } catch (e) { if (token === generation && request === loadRequest) error.value = message(e) }
  finally { if (token === generation && request === loadRequest) loading.value = false }
}
const replaceItem = item => { items.value = items.value.map(current => current.id === item.id ? item : current) }
const refresh = async () => { if (await accounts.ensureLoaded({ force: true })) await load() }
const update = async fields => {
  const account = selectedAccount.value, token = generation, item = selected.value
  busy.value = true; error.value = ''
  try { const result = await api.update(account, item.id, { revision: item.revision, ...fields }); if (token === generation) replaceItem(result) }
  catch (e) { if (token === generation) error.value = message(e) }
  finally { if (token === generation) busy.value = false }
}
const locate = async source => {
  if (!canOpenChat.value) { error.value = '请先导入对应账号的聊天数据，再打开原消息。已恢复的事项可以继续使用。'; return }
  const account = selectedAccount.value
  navigation.value = { kind: 'source', account, username: source.username, anchor: source.anchor }
  const target = navigation.value
  error.value = ''
  try {
    const result = await navigateTo('/chat')
    if (result === false || isNavigationFailure(result)) throw new Error('未能打开原消息，请重试。')
  } catch (e) {
    if (navigation.value === target) navigation.value = null
    if (account === selectedAccount.value) error.value = message(e)
  }
}
watch(selectedAccount, () => { generation++; items.value = []; selectedId.value = ''; query.value = ''; error.value = ''; busy.value = false; loading.value = false; adjusting.value = false; void load() }, { immediate: true })
watch(() => revisions.value[selectedAccount.value], () => { if (selectedAccount.value) void load() })
watch(navigation, target => {
  if (!target?.matter_id) return
  pendingId = target.matter_id; navigation.value = null
  if (selectedAccount.value !== target.account) accounts.setSelectedAccount(target.account)
  else void load()
}, { immediate: true })
onMounted(() => { accounts.ensureLoaded(); clockTimer = setInterval(() => { clock.value = Date.now() }, 30000) })
onUnmounted(() => { generation++; loadRequest++; clearInterval(clockTimer) })
</script>

<style scoped>
.matter-account { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; font-size: 12px; }
.matter-account select { max-width: 100%; font: inherit; color: inherit; background: var(--app-surface-bg); border: 1px solid var(--app-border); border-radius: 6px; padding: 6px 8px; }
.matters-page{height:100%;min-height:0;display:flex;flex-direction:column;background:var(--app-surface-bg);color:var(--app-text-primary);font-size:14px;line-height:1.65}header{padding:24px;display:flex;justify-content:space-between;align-items:center;gap:20px;border-bottom:1px solid var(--app-border)}h1{font-size:21px;font-weight:650;margin:0}header p{color:var(--app-text-secondary);margin:5px 0 0}h2{font-size:20px;overflow-wrap:anywhere;font-weight:650;margin:8px 0 12px}h3{font-size:15px;font-weight:600;margin:28px 0 8px}.matter-filters{display:flex;gap:8px;align-items:center;padding:14px 24px;border-bottom:1px solid var(--app-border);flex-wrap:wrap}.matter-filters input{margin-left:auto;width:220px}.matter-filters span{font-variant-numeric:tabular-nums;margin-left:4px}.matters-layout{display:grid;grid-template-columns:minmax(280px,2fr) minmax(360px,3fr);flex:1;min-height:0}.matter-list{overflow:auto;border-right:1px solid var(--app-border);padding:12px}.matter-row{display:grid;width:100%;grid-template-columns:auto 1fr;text-align:left;gap:5px 12px;padding:16px 12px;border:0;border-radius:8px;background:transparent}.matter-row strong{min-width:0;overflow-wrap:anywhere;font-weight:550}.matter-row small{grid-column:2;color:var(--app-text-secondary)}.matter-row.active,.matter-filters .active{background:var(--app-surface-muted)}.matter-state{font-size:12px;color:var(--app-text-secondary)}.matter-state.in_progress{color:var(--chat-accent)}.matter-state.done{color:var(--app-text-secondary)}.matter-detail{padding:24px;overflow:auto}.detail-empty{padding:40px;color:var(--app-text-secondary)}.matter-actions{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.reminder{display:flex;align-items:center;gap:8px}.muted{font-size:13px;color:var(--app-text-secondary)}.overdue,.matter-row small.overdue{color:var(--danger-color,#b91c1c)}blockquote{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--app-surface-muted);padding:14px;margin:12px 0;max-height:260px;overflow:auto}.history{padding:0;list-style:none}.history li{border-top:1px solid var(--app-border);padding:14px 0}.history p{margin:5px 0}.history time{color:var(--app-text-secondary);font-size:12px;font-variant-numeric:tabular-nums}.page-notice,.empty{padding:24px}.matter-error{padding:12px 24px;color:var(--danger-color,#b91c1c)}button,input{font:inherit;color:inherit;background:var(--app-input-bg);border:1px solid var(--app-border);border-radius:6px;padding:7px 10px}button{cursor:pointer;white-space:nowrap}button:hover{background:var(--app-surface-muted)}button:disabled{opacity:.5;cursor:default}button:focus-visible,input:focus-visible{outline:2px solid var(--chat-accent);outline-offset:2px}.detail-back{display:none}
@media(max-width:1000px){.matters-layout{grid-template-columns:1fr}.matter-detail{display:none}.has-detail .matter-list{display:none}.has-detail .matter-detail{display:block}.detail-empty{display:none}.detail-back{display:inline-block;margin-bottom:20px}}
@media(max-width:600px){header,.matter-detail{padding:18px}.matter-filters{padding:12px 18px}.matter-filters input{margin-left:0;width:100%}}
</style>
