<template>
  <aside class="ai-sidebar ai-ui" aria-label="聊天 AI 助手">
    <header class="ai-sidebar-header"><span class="ai-assistant-icon"><WandSparkles :size="16" :stroke-width="1.8" aria-hidden="true" /></span><strong class="ai-title">AI 助手</strong><button type="button" class="ai-icon-button" title="AI 服务设置" aria-label="AI 服务设置" @click="settings.openDialog('ai')"><SlidersHorizontal :size="16" :stroke-width="1.8" aria-hidden="true" /></button><button type="button" class="ai-icon-button" aria-label="关闭 AI 面板" @click="$emit('close')"><X :size="16" :stroke-width="1.8" aria-hidden="true" /></button></header>
    <nav class="ai-sidebar-tabs" aria-label="AI 功能"><button v-for="item in tabs" :key="item.key" type="button" :aria-current="tab === item.key ? 'page' : undefined" @click="tab = item.key; editId = ''">{{ item.label }}</button></nav>
    <div class="ai-sidebar-body">
    <p v-if="error" class="ai-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="ai-success" role="status">{{ notice }}</p>
    <div v-if="tab !== 'history'" ref="composerView" v-show="tab !== 'summary' || !activeTask || composerExpanded">
      <div class="ai-context-card">
        <span class="ai-context-icon"><MessageCircleMore v-if="scope === 'current'" :size="16" :stroke-width="1.8" aria-hidden="true" /><Layers v-else :size="16" :stroke-width="1.8" aria-hidden="true" /></span>
        <div><span>{{ scope === 'current' ? '当前会话' : '批量处理' }}</span><strong :title="targetTitle">{{ targetTitle }}</strong></div>
        <button v-if="scope === 'current'" type="button" class="ai-text-button" @click="beginBatch">{{ tab === 'summary' ? '批量总结' : '多个会话' }}</button>
        <button v-else type="button" class="ai-text-button" @click="useCurrent">返回当前</button>
      </div>
      <div v-if="scope === 'batch'" class="ai-batch-picker">
      <label><span class="ai-visually-hidden">搜索群聊或好友</span><input v-model="query" type="search" placeholder="搜索要一起处理的会话" /></label>
      <div class="ai-targets" aria-label="批量会话选择">
        <label v-for="c in filteredContacts" :key="c.username" class="ai-check"><input v-model="selected" type="checkbox" :value="c.username" />{{ c.name || c.displayName || c.username }}</label>
      </div>
      <p v-if="contactsLoading" class="ai-muted">正在加载更多会话…</p>
      <p v-if="contactsError" class="ai-error">{{ contactsError }} <button type="button" @click="loadContacts">重试</button></p>
      <p class="ai-muted">已选 {{ selected.length }} 个会话{{ tab === 'summary' ? '，分别总结后生成总览' : '' }}</p>
      </div>
      <p v-if="!targets.length && scope === 'current'" class="ai-muted">在左侧打开一个聊天，即可使用 AI 助手。</p>
      <label v-if="tab !== 'summary'">规则名称<input v-model.trim="rule.name" placeholder="例如：项目进展日报" /></label>
      <label v-if="tab === 'alert'">关注条件<textarea v-model.trim="rule.condition" rows="3" placeholder="例如：有人讨论交付延期，或需要我确认方案时提醒我" /></label>
      <div v-if="tab !== 'alert'" class="ai-row">
        <label>总结范围<UiSelect v-model="range.mode" label="总结范围" :options="rangeOptions" /></label>
        <label v-if="range.mode === 'count'">消息条数<input v-model.number="range.count" type="number" min="1" max="100000" /></label>
        <label v-if="range.mode === 'hours'">小时数<input v-model.number="range.hours" type="number" min="0.1" step="0.1" /></label>
      </div>
      <div v-if="tab !== 'alert' && range.mode === 'dates'" class="ai-row"><label>开始时间<input v-model="startDate" type="datetime-local" /></label><label>结束时间<input v-model="endDate" type="datetime-local" /></label></div>
      <template v-if="tab === 'auto'">
        <label>触发方式<UiSelect v-model="rule.trigger" label="触发方式" :options="triggerOptions" /></label>
        <label v-if="rule.trigger === 'daily'">每天（本机时区）<input v-model="rule.daily_time" type="time" /></label>
        <label v-if="rule.trigger === 'count'">所选会话合计新增条数<input v-model.number="rule.threshold" type="number" min="1" /></label>
      </template>
      <div v-if="tab === 'alert' || (tab === 'auto' && rule.trigger === 'interval')" class="ai-row">
        <label>{{ tab === 'alert' ? '检测间隔' : '执行间隔' }}<UiSelect v-model="intervalPreset" :label="tab === 'alert' ? '检测间隔' : '执行间隔'" :options="intervalOptions" @change="applyInterval" /></label>
        <label v-if="intervalPreset === 'custom'">间隔秒数<input v-model.number="rule.interval_seconds" type="number" min="10" /></label>
      </div>
      <label class="ai-check ai-media-toggle"><input v-model="media" type="checkbox" /><span>包含图片与附件<small>同时分析图片和文档中的内容</small></span></label>
      <details class="ai-more-settings"><summary>更多设置<span>模型 · 附件 · 通知</span></summary><div>
      <label>文本模型<UiSelect v-model="profileId" label="文本模型" :options="textModelOptions" /></label>
      <div v-if="media" class="ai-row"><label>视觉模型<UiSelect v-model="visionProfileId" label="视觉模型" :options="visionModelOptions" /></label><label>单附件上限（MB）<input v-model.number="maxMb" type="number" min="1" max="200" /></label></div>
      <div class="ai-row ai-wrap"><label class="ai-check"><input v-model="notify" type="checkbox" />桌面通知</label><label v-if="notify" class="ai-check"><input v-model="hideContent" type="checkbox" />隐藏通知内容</label></div>
      </div></details>
      <template v-if="tab !== 'summary'"><label class="ai-check"><input v-model="rule.enabled" type="checkbox" />启用此规则</label><p class="ai-muted">仅应用运行期间执行，最小化到托盘后仍有效。无新增消息不调用模型。</p></template>
      <div class="ai-submit-area"><p v-if="tab === 'summary'" class="ai-muted">{{ scope === 'current' ? '仅处理当前会话' : `处理已选 ${targets.length} 个会话` }} · {{ profileId ? '使用指定模型' : '使用全局默认模型' }}</p><button class="ai-primary ai-submit" type="button" :disabled="busy || !account || !targets.length || (tab === 'summary' && taskRunning)" @click="submit"><WandSparkles :size="16" :stroke-width="1.8" aria-hidden="true" />{{ busy ? '处理中…' : tab === 'summary' ? taskRunning ? '总结进行中…' : '开始总结' : editId ? '保存规则修改' : '创建规则' }}</button></div>
      <div v-if="tab !== 'summary'">
        <article v-for="r in rules.filter(r => r.kind === (tab === 'alert' ? 'alert' : 'summary'))" :key="r.id" class="ai-card">
          <strong>{{ r.name }}</strong><p class="ai-muted">{{ r.enabled ? '已启用' : '已暂停' }} · {{ r.conversations.length }} 个会话</p><p v-if="r.error" class="ai-error">{{ r.error }}</p>
          <div class="ai-row ai-wrap"><button type="button" @click="editRule(r)">编辑</button><button type="button" :disabled="busy" @click="toggleRule(r)">{{ r.enabled ? '暂停' : '启用' }}</button><button type="button" :disabled="busy" @click="runRule(r)">立即执行</button><button type="button" :disabled="busy" @click="deleteRule(r)">删除</button></div>
        </article>
      </div>
    </div>
    <div v-else>
      <div class="ai-row"><strong>关注记录</strong><button type="button" :disabled="busy" @click="clearAlerts">清空记录</button></div>
      <article v-for="a in alerts.filter(a => !a.hidden)" :key="a.id" class="ai-card"><strong>{{ a.name }}</strong><p>{{ a.reason }}</p><button type="button" @click="$emit('locate', a)">查看原消息</button></article>
      <strong>总结与检测任务</strong>
      <article v-for="t in tasks" :key="t.id" class="ai-card"><button type="button" @click="openTask(t.id)">{{ date(t.created) }} · {{ t.kind === 'alert' ? '关注检测' : '消息总结' }}</button><p class="ai-muted">{{ t.stage }} · {{ t.conversations.length }} 个会话</p></article>
      <button v-if="moreTasks" type="button" :disabled="busy" @click="loadMoreTasks">加载更早任务</button>
      <p v-if="!tasks.length" class="ai-muted">尚无任务记录</p>
    </div>
    <article v-if="activeTask" ref="taskView" class="ai-task-thread" aria-label="AI 处理记录">
      <div class="ai-request-bubble"><span>{{ activeTask.kind === 'alert' ? '关注检测请求' : '消息总结请求' }}</span><p>{{ taskRequest }}</p><small>截止 {{ date(activeTask.range.end) }}{{ activeTask.media ? ' · 包含图片与附件' : '' }}</small></div>
      <div class="ai-response-heading"><span class="ai-assistant-icon"><WandSparkles :size="16" :stroke-width="1.8" aria-hidden="true" /></span><strong>AI 助手</strong><span v-if="taskRunning || activeTask.finished_at" class="ai-task-duration">{{ taskRunning ? '已用时' : '用时' }} {{ duration(elapsed) }}</span></div>
      <div class="ai-task-status" :class="{ 'is-running': taskRunning }" role="status">
        <LoaderCircle v-if="taskRunning" class="agent-icon-spin" :size="16" :stroke-width="1.8" aria-hidden="true" /><CircleCheck v-else-if="activeTask.status === 'completed'" :size="16" :stroke-width="1.8" aria-hidden="true" /><Info v-else :size="16" :stroke-width="1.8" aria-hidden="true" /><strong>{{ taskStatus }}</strong>
      </div>
      <div v-if="taskRunning" class="ai-task-progress"><div><span>{{ activeTask.stage || '等待执行' }}</span><span>{{ activeTask.progress || 0 }}%</span></div><progress aria-label="处理阶段进度" max="100" :value="activeTask.progress || 0" /><p>{{ waitingHint }}</p></div>
      <details v-if="taskActivity.length" class="ai-task-activity" :open="taskRunning">
        <summary>处理记录<span>{{ taskActivity.length }} 个阶段</span></summary>
        <ol><li v-for="(entry, index) in taskActivity" :key="`${entry.time}-${index}`" :class="{ 'is-current': taskRunning && index === taskActivity.length - 1 }"><span class="ai-activity-dot"></span><span>{{ entry.stage }}</span><time>{{ duration(Math.max(0, Math.floor(entry.time - (activeTask.started_at || activeTask.created || entry.time)))) }}</time></li></ol>
      </details>
      <p v-if="pollError" class="ai-error" role="alert">{{ pollError }}</p>
      <p v-if="activeTask.error" class="ai-error">{{ activeTask.error }}</p>
      <div v-if="taskRunning" class="ai-task-controls"><button type="button" :disabled="busy" @click="taskAction('cancel')"><CircleStop :size="16" :stroke-width="1.8" aria-hidden="true" />停止处理</button><span>可切换聊天，任务会继续</span></div>
      <template v-if="activeTask.conversations.length > 1 && hasOverview"><h4 class="ai-section-title">跨会话总览</h4><AiSummaryResult :account="account" :sources="activeTask.results.flatMap(r => r.sources)" :summary="activeTask.overview" @locate="locate" /></template>
      <section v-for="result in activeTask.results" :key="result.username" class="ai-task-result"><h4 class="ai-title">{{ result.name }}</h4><p class="ai-muted">已分析 {{ result.count }} 条消息</p><p v-if="result.warning">{{ result.warning }}</p><p v-if="result.error" class="ai-error">{{ result.error }}</p><template v-if="activeTask.kind === 'alert' && result.summary && !result.error"><div v-for="(match, index) in result.summary.matches || []" :key="index" class="ai-alert-match"><p>{{ match.reason }}</p><button v-for="(source, sourceIndex) in match.sources" :key="source" type="button" class="ai-source" @click="locate(source)">查看原消息{{ match.sources.length > 1 ? ` ${sourceIndex + 1}` : '' }}</button></div><p v-if="!result.summary.matches?.length" class="ai-muted">本次未发现符合关注条件的新消息。</p></template><AiSummaryResult v-else-if="result.summary && activeTask.kind !== 'alert'" :account="account" :sources="result.sources" :summary="result.summary" @locate="locate" /><details v-if="result.coverage?.length"><summary>媒体处理情况</summary><p v-for="c in result.coverage" :key="c.source">{{ c.status }} <button type="button" class="ai-source" @click="locate(c.source)">原消息</button></p></details></section>
      <div v-if="!taskRunning" class="ai-task-actions"><button v-if="hasResult" type="button" @click="copyResult"><Copy :size="16" :stroke-width="1.8" aria-hidden="true" />复制结果</button><button v-if="['failed', 'partial', 'cancelled'].includes(activeTask.status)" type="button" :disabled="busy" @click="taskAction('retry')">重试</button><button type="button" :disabled="busy" @click="deleteTask">删除记录</button></div>
      <button v-if="tab === 'summary' && !taskRunning && !composerExpanded" class="ai-new-summary" type="button" @click="expandComposer"><Plus :size="16" :stroke-width="1.8" aria-hidden="true" />调整范围，再次总结</button>
    </article>
    </div>
  </aside>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { CircleCheck, CircleStop, Copy, Info, Layers, LoaderCircle, MessageCircleMore, Plus, SlidersHorizontal, WandSparkles, X } from '@lucide/vue'
import AiSummaryResult from './AiSummaryResult.vue'
import UiSelect from '../UiSelect.vue'
import '~/assets/css/ai.css'
const props = defineProps({ account: String, contact: Object, contacts: Array, focusTaskId: String })
const emit = defineEmits(['close', 'locate'])
const api = useAiApi(), settings = useSettingsDialog()
const tabs = [{ key: 'summary', label: '消息总结' }, { key: 'auto', label: '自动任务' }, { key: 'alert', label: '关注提醒' }, { key: 'history', label: '历史记录' }]
const tab = ref('summary'), query = ref(''), selected = ref(props.contact ? [props.contact.username] : [])
const scope = ref('current'), contactsLoading = ref(false), contactsError = ref('')
// 当前会话直接取聊天页状态，避免账号加载或切换聊天后使用过期的选择。
const targets = computed(() => scope.value === 'current' ? (props.contact?.username ? [props.contact.username] : []) : selected.value)
const targetTitle = computed(() => scope.value === 'current' ? (props.contact?.name || props.contact?.displayName || props.contact?.username || '尚未打开聊天') : `已选 ${selected.value.length} 个会话`)
const useCurrent = () => { scope.value = 'current'; selected.value = []; query.value = '' }
const beginBatch = () => { selected.value = [...targets.value]; scope.value = 'batch'; void loadContacts() }
const rangeOptions = computed(() => [{ value: 'count', label: '最近消息' }, { value: 'hours', label: '过去几小时' }, { value: 'dates', label: '自定义时间' }, ...(tab.value === 'auto' ? [{ value: 'since', label: '上次总结以来' }] : [])])
const triggerOptions = [{ value: 'interval', label: '固定间隔' }, { value: 'daily', label: '每天固定时间' }, { value: 'count', label: '累计新增消息条数' }]
const intervalOptions = [{ value: '10', label: '10 秒' }, { value: '60', label: '1 分钟' }, { value: '300', label: '5 分钟' }, { value: '3600', label: '1 小时' }, { value: 'custom', label: '自定义' }]
const profiles = ref([]), rules = ref([]), tasks = ref([]), alerts = ref([]), activeTask = ref(null), allContacts = ref([])
const taskRunning = computed(() => ['queued', 'running'].includes(activeTask.value?.status))
const clock = ref(Date.now()), pollError = ref(''), composerExpanded = ref(false), composerView = ref(null), taskView = ref(null), observedFinish = ref(0)
const expandComposer = async () => { composerExpanded.value = true; await nextTick(); composerView.value?.scrollIntoView?.({ block: 'start', behavior: 'smooth' }) }
// 老任务没有结束时间时使用首次观察到结束的时间，避免完成后计时继续增长。
const elapsed = computed(() => {
  const task = activeTask.value
  if (!task) return 0
  const end = taskRunning.value ? clock.value / 1000 : task.finished_at || task.updated_at || observedFinish.value || clock.value / 1000
  return Math.max(0, Math.floor(end - (task.started_at || task.created || end)))
})
const duration = seconds => { const s = Math.max(0, Math.floor(seconds)); return s >= 3600 ? `${Math.floor(s / 3600)}小时${Math.floor(s % 3600 / 60)}分` : s >= 60 ? `${Math.floor(s / 60)}分${s % 60}秒` : `${s}秒` }
const taskActivity = computed(() => activeTask.value?.activity || [])
const hasOverview = computed(() => !!activeTask.value?.overview && Object.keys(activeTask.value.overview).length > 0)
const hasResult = computed(() => hasOverview.value || activeTask.value?.results?.some(r => r.summary))
const taskStatus = computed(() => ({ queued: '正在排队', running: '正在处理你的消息', completed: activeTask.value?.kind === 'alert' ? '关注检测已完成' : '总结已完成', partial: '部分会话已完成', failed: '本次处理未完成', cancelled: '已停止处理' }[activeTask.value?.status] || '正在获取状态'))
const waitingHint = computed(() => /生成摘要|合并|整理/.test(activeTask.value?.stage || '') ? '正在等待模型返回结果，较长的对话可能需要几分钟。' : '进度随实际处理阶段更新，图片和附件可能需要更久。')
const taskRequest = computed(() => {
  const task = activeTask.value
  if (!task) return ''
  const names = task.conversation_names || task.results?.map(r => r.name)
  const title = names?.length ? names.join('、') : task.conversations.map(id => [props.contact, ...(props.contacts || [])].find(c => c?.username === id)?.name || id).join('、')
  const r = task.range || {}
  const scopeText = r.mode === 'count' ? `最近 ${r.count} 条消息` : r.mode === 'hours' ? `过去 ${r.hours} 小时的消息` : r.mode === 'since' ? '上次处理以来的消息' : '指定时间段的消息'
  return `${task.kind === 'alert' ? '检测' : '总结'} ${title} 的${scopeText}`
})
const busy = ref(false), error = ref(''), notice = ref(''), editId = ref('')
const profileId = ref(''), visionProfileId = ref(''), media = ref(true), maxMb = ref(20), notify = ref(true), hideContent = ref(false)
const textModelOptions = computed(() => [{ value: '', label: '使用全局默认' }, ...profiles.value.map(p => ({ value: p.id, label: p.name, description: p.model }))])
const visionModelOptions = computed(() => [{ value: '', label: '使用全局默认' }, ...profiles.value.filter(p => p.vision).map(p => ({ value: p.id, label: p.name, description: p.model }))])
const range = reactive({ mode: 'count', count: 100, hours: 24 }), startDate = ref(''), endDate = ref('')
const rule = reactive({ name: '', condition: '', trigger: 'interval', interval_seconds: 60, daily_time: '09:00', threshold: 100, enabled: false })
const intervalPreset = ref('60')
const moreTasks = ref(false)
const filteredContacts = computed(() => {
  const list = [...(allContacts.value.length ? allContacts.value : props.contacts || [])]
  if (props.contact) {
    const currentIndex = list.findIndex(c => c.username === props.contact.username)
    if (currentIndex >= 0) list.splice(currentIndex, 1)
    list.unshift(props.contact)
  }
  return list.filter(c => `${c.name || ''} ${c.displayName || ''} ${c.username}`.toLowerCase().includes(query.value.toLowerCase()))
})
const date = value => new Date(value * 1000).toLocaleString()
const action = async fn => { busy.value = true; error.value = ''; notice.value = ''; try { await fn() } catch (e) { error.value = e.message } finally { busy.value = false } }
let generation = 0, closeEvents = null, refreshTimer = null, taskTimer = null, polling = false, lastPoll = 0, disposed = false
const pollTask = async () => {
  if (!taskRunning.value || polling || !props.account) return
  const id = activeTask.value.id, account = props.account
  polling = true; lastPoll = Date.now()
  try {
    const detail = await api.request(`/tasks/${id}`, { query: { account }, timeout: 12000 })
    if (!disposed && account === props.account && id === activeTask.value?.id && taskRunning.value && (detail.updated_at || 0) >= (activeTask.value.updated_at || 0)) {
      activeTask.value = detail; pollError.value = ''
    } else api.diagnostic?.('response.stale', { task_id: id, component: 'ai' })
  } catch { if (!disposed && id === activeTask.value?.id && account === props.account) pollError.value = '暂时无法获取最新进度，正在重试。任务可能仍在后台执行，请勿重复提交。' }
  finally { polling = false }
}
const refresh = async () => {
  const account = props.account, current = ++generation
  if (!account) return
  const [s, r, t, a] = await Promise.all([api.request('/settings'), api.request('/rules', { query: { account } }), api.request('/tasks', { query: { account } }), api.request('/alerts', { query: { account } })])
  if (current !== generation || account !== props.account) { api.diagnostic?.('response.stale', { component: 'ai' }); return }
  profiles.value = s.profiles; rules.value = r; tasks.value = t; alerts.value = a
  moreTasks.value = t.length === 50
  // 重开面板时接回当前会话的未完成任务，避免误以为未提交而重复调用模型。
  const pending = t.find(task => ['queued', 'running'].includes(task.status) && task.conversations.includes(props.contact?.username))
  const id = activeTask.value?.id || props.focusTaskId || pending?.id
  if (id) {
    const detail = await api.request(`/tasks/${id}`, { query: { account } })
    if (current === generation && account === props.account && (!activeTask.value || activeTask.value.id === id) && (detail.updated_at || 0) >= (activeTask.value?.updated_at || 0) && !(activeTask.value?.status === 'completed' && detail.status !== 'completed')) activeTask.value = detail
  }
}
const openTask = id => action(async () => { const account = props.account; const detail = await api.request(`/tasks/${id}`, { query: { account } }); if (account === props.account) activeTask.value = detail })
const loadMoreTasks = () => action(async () => { const account = props.account; const older = await api.request('/tasks', { query: { account, offset: tasks.value.length } }); if (account !== props.account) return; const known = new Set(tasks.value.map(t => t.id)); tasks.value.push(...older.filter(t => !known.has(t.id))); moreTasks.value = older.length === 50 })
const loadContacts = async () => {
  const account = props.account
  if (!account || contactsLoading.value) return
  contactsLoading.value = true; contactsError.value = ''
  try {
    const result = await api.request('/conversations', { query: { account } })
    if (account === props.account) allContacts.value = result
  } catch { if (account === props.account) contactsError.value = '完整会话列表加载失败，仍可选择已显示的会话。' }
  finally { if (account === props.account) contactsLoading.value = false }
}
const applyInterval = () => { if (intervalPreset.value !== 'custom') rule.interval_seconds = Number(intervalPreset.value) }
const payload = () => ({ account: props.account, conversations: [...targets.value], range: { ...range,
  start: range.mode === 'dates' && startDate.value ? Math.floor(new Date(startDate.value).getTime() / 1000) : null,
  end: range.mode === 'dates' && endDate.value ? Math.floor(new Date(endDate.value).getTime() / 1000) : null },
  profile_id: profileId.value, vision_profile_id: visionProfileId.value, media: media.value, max_attachment_mb: maxMb.value, notify: notify.value, hide_content: hideContent.value })
const submit = () => action(async () => {
  const account = props.account
  if (!account || !targets.value.length) return
  if (tab.value === 'summary' && taskRunning.value) return
  if (tab.value === 'summary') {
    const task = await api.request('/tasks', { method: 'POST', body: payload() })
    if (account !== props.account) return
    activeTask.value = task
    composerExpanded.value = false
  }
  else {
    const data = { ...payload(), ...rule, kind: tab.value === 'alert' ? 'alert' : 'summary' }
    if (tab.value === 'alert') data.range = { mode: 'since' }
    await api.request(editId.value ? `/rules/${editId.value}` : '/rules', { method: editId.value ? 'PUT' : 'POST', body: data })
    editId.value = ''; notice.value = '规则已保存'
  }
  await refresh()
})
const editRule = r => { scope.value = 'batch'; void loadContacts(); editId.value = r.id; selected.value = [...r.conversations]; for (const key of Object.keys(rule)) rule[key] = r[key]; Object.assign(range, r.range); profileId.value = r.profile_id; visionProfileId.value = r.vision_profile_id; media.value = r.media; maxMb.value = r.max_attachment_mb; notify.value = r.notify; hideContent.value = r.hide_content; intervalPreset.value = 'custom'; const local = t => t ? new Date(t * 1000 - new Date(t * 1000).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ''; startDate.value = local(r.range.start); endDate.value = local(r.range.end) }
const toggleRule = r => action(async () => { await api.request(`/rules/${r.id}`, { method: 'PUT', body: { ...r, enabled: !r.enabled } }); await refresh() })
const deleteRule = r => action(async () => { await api.request(`/rules/${r.id}`, { method: 'DELETE', query: { account: props.account } }); await refresh() })
const runRule = r => action(async () => { const result = await api.request(`/rules/${r.id}/run`, { method: 'POST', query: { account: props.account } }); activeTask.value = result.task; if (!result.task) notice.value = '没有新增消息，或该规则已有任务正在执行'; await refresh() })
const taskAction = verb => action(async () => { const account = props.account; const task = await api.request(`/tasks/${activeTask.value.id}/${verb}`, { method: 'POST', query: { account } }); if (account !== props.account) return; activeTask.value = task; await refresh() })
const deleteTask = () => action(async () => { await api.request(`/tasks/${activeTask.value.id}`, { method: 'DELETE', query: { account: props.account } }); activeTask.value = null; await refresh() })
const clearAlerts = () => action(async () => { await api.request('/alerts', { method: 'DELETE', query: { account: props.account } }); await refresh() })
const locate = source => { const found = activeTask.value?.results.flatMap(r => r.sources).find(s => s.source === source); if (found) emit('locate', found); else error.value = '该来源暂不可定位' }
const copyResult = () => action(async () => { await navigator.clipboard.writeText(JSON.stringify({ overview: activeTask.value.overview, results: activeTask.value.results }, null, 2)); notice.value = '结果已复制' })
const connect = () => { closeEvents?.(); if (!props.account) return; closeEvents = api.events(props.account, () => { clearTimeout(refreshTimer); refreshTimer = setTimeout(() => refresh().catch(e => { error.value = e.message }), 300) }) }
watch(() => props.account, () => { generation++; activeTask.value = null; tasks.value = []; rules.value = []; alerts.value = []; useCurrent(); allContacts.value = []; contactsLoading.value = false; contactsError.value = ''; editId.value = ''; connect(); action(refresh) })
watch(() => props.focusTaskId, id => { if (id) { tab.value = 'history'; activeTask.value = null; action(refresh) } })
watch(tab, value => {
  // 操作提示只属于当前工具，切换后不沿用上一页的成功或失败提示。
  error.value = ''; notice.value = ''
  if (value === 'summary' && range.mode === 'since') range.mode = 'count'
})
watch(() => activeTask.value?.id, async () => { pollError.value = ''; lastPoll = 0; composerExpanded.value = false; observedFinish.value = Date.now() / 1000; await nextTick(); taskView.value?.scrollIntoView?.({ block: 'start', behavior: 'smooth' }) })
watch(taskRunning, running => { if (!running) observedFinish.value = Date.now() / 1000 })
onMounted(() => {
  if (props.focusTaskId) tab.value = 'history'; connect(); action(refresh)
  // SSE 之外定期查询当前任务，断线或漏掉完成事件时也能拿到最终结果。
  taskTimer = setInterval(() => { clock.value = Date.now(); if (Date.now() - lastPoll >= 3000) void pollTask() }, 1000)
})
onUnmounted(() => { disposed = true; generation++; closeEvents?.(); clearTimeout(refreshTimer); clearInterval(taskTimer) })
</script>

<style scoped>
.ai-ui svg.lucide { display:block; flex:none; }
.ai-sidebar { --side-bg: var(--app-surface-bg, #fff); --side-soft: var(--app-surface-soft, #f7f8fa); --side-border: var(--app-border, #e7e9ed); width: 380px; max-width: calc(100vw - 70px); min-height: 0; flex-shrink: 0; display: flex; flex-direction: column; overflow: hidden; padding: 0; border-left: 1px solid var(--side-border); background: var(--side-bg); color: var(--app-text-primary, #25352d); z-index: 30; font-size: 12px; line-height: 1.5; }
.ai-sidebar-header { display: flex; align-items: center; gap: 10px; padding: 17px 16px 13px; flex-shrink: 0; }
.ai-sidebar-header .ai-title { margin: 0; flex: 1; font-size: 15px; }
.ai-assistant-icon { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 9px; color: #079b57; background: #edf8f1; }
.ai-ui .ai-icon-button { padding: 0; width: 28px; height: 28px; border-color: transparent; background: transparent; color: var(--app-text-secondary, #79828e); }
.ai-sidebar-tabs { display: flex; flex-shrink: 0; margin: 0 16px; border-bottom: 1px solid var(--side-border); }
.ai-ui .ai-sidebar-tabs button { flex: 1; padding: 10px 0; font-size: 12px; white-space: nowrap; border: 0; border-bottom: 2px solid transparent; border-radius: 0; color: var(--app-text-secondary, #79828e); background: transparent; }
.ai-ui .ai-sidebar-tabs button[aria-current=page] { color: #079b57; border-bottom-color: #079b57; font-weight: 600; }
.ai-sidebar-body { flex: 1; min-height: 0; overflow-y: auto; padding: 18px 16px; scrollbar-width: thin; }
.ai-context-card { display: flex; align-items: center; gap: 10px; padding: 12px; margin-bottom: 18px; border: 1px solid var(--side-border); border-radius: 8px; background: var(--side-soft); }
.ai-context-icon { color: #079b57; font-size: 17px; }
.ai-context-card > div { min-width: 0; flex: 1; }
.ai-context-card > div > span { display: block; font-size: 10px; color: var(--app-text-secondary, #79828e); margin-bottom: 3px; }
.ai-context-card strong { display: block; font-weight: 550; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-ui .ai-text-button { padding: 4px 0; border: 0; color: #079b57; background: transparent; font-size: 10px; white-space: nowrap; }
.ai-sidebar label { font-size: 11px; gap: 6px; margin-bottom: 12px; }
.ai-sidebar input:not([type=checkbox]), .ai-sidebar textarea { border-color: var(--side-border); background: var(--side-bg); color: inherit; font-size: 12px; padding: 7px 9px; }
.ai-sidebar input:not([type=checkbox]) { height: 34px; }
.ai-sidebar input[type=checkbox] { width: 13px; height: 13px; margin: 0; flex-shrink: 0; }
.ai-sidebar .ai-row { align-items: flex-start; gap: 10px; margin: 12px 0 0; }
.ai-sidebar .ai-check { gap: 8px; }
.ai-sidebar .ai-media-toggle { padding: 12px; border: 1px solid var(--side-border); border-radius: 8px; font-size: 12px; margin: 2px 0 12px; }
.ai-media-toggle small { display: block; margin-top: 3px; color: var(--app-text-secondary, #79828e); font-size: 10px; }
.ai-more-settings { border-bottom: 1px solid var(--side-border); margin-bottom: 16px; }
.ai-more-settings > summary { cursor: pointer; padding: 10px 0 12px; font-size: 11px; color: var(--app-text-secondary, #79828e); }
.ai-more-settings > summary > span { float: right; font-size: 10px; }
.ai-more-settings > div { padding: 4px 0 2px; }
.ai-more-settings > summary:focus-visible { outline: 2px solid #079b57; outline-offset: 2px; }
.ai-submit-area > .ai-muted { text-align: center; font-size: 10px; margin: 0 0 8px; }
.ai-ui .ai-submit { display: flex; justify-content: center; align-items: center; gap: 8px; width: 100%; min-height: 37px; font-size: 12px; font-weight: 550; }
.ai-targets { max-height: 180px; overflow-y: auto; border: 1px solid var(--side-border); border-radius: 7px; padding: 8px; background: var(--side-bg); }
.ai-targets .ai-check { padding: 5px 3px; margin: 0; font-size: 12px; overflow-wrap: anywhere; }
.ai-batch-picker { margin: -6px 0 18px; }
.ai-batch-picker > .ai-muted { font-size: 10px; margin-top: 8px; }
.ai-visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; }
.ai-sidebar .ai-card { border-color: var(--side-border); background: var(--side-bg); }
.ai-task-thread { padding: 0 0 8px; overflow-wrap: anywhere; }
.ai-request-bubble { margin: 0 0 24px 24px; padding: 13px 15px; border-radius: 12px 12px 3px 12px; background: var(--side-soft); border: 1px solid var(--side-border); }
.ai-request-bubble > span, .ai-request-bubble small { color: var(--app-text-secondary, #79828e); font-size: 10px; }
.ai-request-bubble p { margin: 6px 0; font-size: 12px; line-height: 1.7; }
.ai-response-heading { display: flex; align-items: center; gap: 9px; margin-bottom: 16px; }
.ai-response-heading strong { flex: 1; font-size: 13px; }
.ai-task-duration { color: var(--app-text-secondary, #79828e); font-variant-numeric: tabular-nums; font-size: 10px; }
.ai-task-status { display: flex; gap: 8px; align-items: center; color: #079b57; font-size: 12px; }
.ai-task-progress { margin-top: 12px; padding: 12px; border: 1px solid var(--side-border); border-radius: 8px; }
.ai-task-progress > div { display: flex; gap: 8px; justify-content: space-between; font-size: 11px; }
.ai-task-progress > div > span:first-child { flex: 1; }
.ai-task-progress progress { display: block; width: 100%; height: 4px; margin: 10px 0; appearance: none; border: 0; border-radius: 4px; overflow: hidden; background: var(--side-border); }
.ai-task-progress progress::-webkit-progress-bar { background: var(--side-border); }
.ai-task-progress progress::-webkit-progress-value { background: #079b57; transition: width .3s ease; }
.ai-task-progress progress::-moz-progress-bar { background: #079b57; }
.ai-task-progress p { margin: 0; color: var(--app-text-secondary, #79828e); font-size: 10px; line-height: 1.7; }
.ai-task-activity { margin: 16px 0; color: var(--app-text-secondary, #79828e); }
.ai-task-activity summary { cursor: pointer; font-size: 11px; padding: 4px 0; }
.ai-task-activity summary > span { float: right; font-size: 10px; }
.ai-task-activity ol { list-style: none; margin: 12px 0 0; padding: 0; max-height: 210px; overflow-y: auto; scrollbar-width: thin; }
.ai-task-activity li { display: flex; align-items: baseline; gap: 9px; position: relative; padding: 0 0 12px 2px; font-size: 10px; }
.ai-task-activity li:not(:last-child)::before { content: ''; position: absolute; left: 5px; top: 10px; bottom: 0; width: 1px; background: var(--side-border); }
.ai-activity-dot { width: 7px; height: 7px; flex-shrink: 0; border-radius: 50%; background: #b4c2b9; }
.ai-task-activity li > span:nth-child(2) { flex: 1; }
.ai-task-activity time { white-space: nowrap; font-variant-numeric: tabular-nums; }
.ai-task-activity .is-current { color: #079b57; }
.ai-task-activity .is-current .ai-activity-dot { background: #079b57; }
.ai-task-controls, .ai-task-actions { display: flex; align-items: center; gap: 10px; margin: 16px 0; }
.ai-task-controls > span { font-size: 10px; color: var(--app-text-secondary, #79828e); }
.ai-ui .ai-task-controls button, .ai-ui .ai-task-actions button { font-size: 10px; display: flex; gap: 5px; align-items: center; padding: 5px 8px; }
.ai-task-result { border-top: 1px solid var(--side-border); padding-top: 16px; margin-top: 18px; }
.ai-task-result .ai-title { font-size: 12px; }
.ai-task-result > .ai-muted { font-size: 10px; margin: 4px 0 12px; }
.ai-ui .ai-new-summary { width: 100%; margin-top: 10px; padding: 10px; color: #079b57; font-size: 11px; background: var(--side-soft); }
.ai-new-summary :is(i, svg) { margin-right: 6px; }
@media (prefers-reduced-motion: reduce) { .ai-task-progress progress::-webkit-progress-value { transition: none; } .ai-task-status .agent-icon-spin { animation: none; } }
:global(html[data-theme=dark]) .ai-sidebar { --side-bg: #222629; --side-soft: #292e31; --side-border: #383e42; }
:global(html[data-theme=dark]) .ai-assistant-icon { color: #65d29d; background: #173a2b; }
@media (max-width: 1000px) { .ai-sidebar { position: absolute; top: 0; bottom: 0; right: 0; box-shadow: -8px 0 30px #0002; } }
</style>
