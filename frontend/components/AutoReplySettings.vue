<template>
  <section class="auto-reply-settings" :aria-busy="loading">
    <div class="ar-heading">
      <div><h4>自动回复</h4><p>选择需要回复的群聊和个人，保存后生效。</p></div>
      <span class="ar-state" data-testid="auto-reply-state">状态：{{ runtime ? stateLabels[runtime.state] : '未加载' }}</span>
    </div>
    <div class="ar-account" data-testid="auto-reply-account">
      <label v-if="accountStore">数据账号
        <UiSelect :model-value="account" :options="accountOptions" :privacy-mode="privacyMode" label="自动回复数据账号" @update:model-value="accountStore.setSelectedAccount($event)" />
      </label>
      <p v-else :class="{ 'privacy-blur': privacyMode }">数据账号：{{ account || '未选择' }}</p>
      <button type="button" :disabled="loading || !!busy || dirty || !account" @click="load">重新加载</button>
    </div>
    <p v-if="!account" class="ar-note">请先选择已解密的数据账号。</p>
    <p v-if="loading" role="status">正在读取自动回复配置…</p>
    <p v-if="error" class="ais-feedback is-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ error }}</p>
    <p v-if="notice" class="ais-feedback is-success" role="status">{{ notice }}</p>

    <div v-if="form && runtime" class="ar-content">
      <p v-if="runtime.active_account && runtime.active_account !== account" class="ar-warning" role="status">账号 <span :class="{ 'privacy-blur': privacyMode }">{{ runtime.active_account }}</span> 正在运行，请先切回该账号清空回复范围并应用。</p>
      <p v-if="runtime.error" class="ar-warning" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ errorText(runtime.error) }}</p>
      <div class="ar-configuration">
        <fieldset :disabled="!!busy">
          <legend>回复内容</legend>
          <div class="ar-model"><span>固定模型</span><AgentModelPicker v-model="modelChoice" :profiles="profiles" :profiles-loading="false" :profiles-error="''" @refresh="refreshProfiles" /></div>
          <p class="ar-note">使用这里选定的服务、模型与参数。顶部聊天模型变化不会影响自动回复；聊天内容会发送到所选服务。</p>
          <label>自定义指令<textarea v-model="form.system_prompt" :class="{ 'privacy-blur': privacyMode }" rows="3" maxlength="16000" placeholder="例如：用简短、自然的中文回答，不确定时说明。" /></label>
        </fieldset>

        <fieldset :disabled="!!busy">
          <legend>回复范围</legend>
          <UiSelect v-model="selectedSessionIds" :options="scopeOptions" :summary="scopeSummary" :privacy-mode="privacyMode" label="自动回复范围" placeholder="选择群聊或个人" multiple searchable search-placeholder="搜索群聊或个人" :disabled="!!busy" />
          <p class="ar-note">可单选、多选或全选。清空后保存即停止回复；以后新增的会话需再次选择。</p>
          <details v-if="form.sessions.length" class="ar-session-options">
            <summary>单独调整会话<span v-if="attentionCount"> · {{ attentionCount }} 个会话需处理</span></summary>
            <UiSelect v-model="editingSessionId" :options="sessionOptions" :privacy-mode="privacyMode" label="调整会话" placeholder="选择要调整的会话" searchable :disabled="!!busy" />
            <div v-if="editingSession" class="ar-session">
              <div class="ar-session-top"><strong :class="{ 'privacy-blur': privacyMode }">{{ sessionName(editingSession) }}</strong><span class="ar-state">{{ sessionLabel(editingSession) }}</span></div>
              <div class="ar-session-fields">
                <label>微信完整定位名称<input v-model.trim="editingSession.target_name" :class="{ 'privacy-blur': privacyMode }" required maxlength="256" :aria-label="`${editingSession.session_id}定位名称`" /></label>
                <label v-if="editingSession.session_id.endsWith('@chatroom')">群聊触发<UiSelect v-model="editingSession.group_trigger" :options="triggerOptions" :label="`${editingSession.target_name}群聊触发`" /></label>
              </div>
              <p class="ar-note">按完整名称定位微信会话；同名、名称变化或已有不同草稿时会暂停。新添加的群默认仅 @ 我。</p>
              <p v-if="sessionState(editingSession.session_id)?.error" class="ar-warning" :class="{ 'privacy-blur': privacyMode }">{{ errorText(sessionState(editingSession.session_id).error) }}</p>
              <div v-if="sessionState(editingSession.session_id)?.status === 'paused'" class="ar-resume">
                <label class="ar-check"><input v-model="resumeConfirmed[editingSession.session_id]" type="checkbox" :data-testid="`confirm-resume-${editingSession.session_id}`" />已处理暂停原因，并确认当前微信仍登录此数据账号</label>
                <button type="button" :disabled="!resumeConfirmed[editingSession.session_id] || dirty" @click="resume(editingSession.session_id)">恢复会话</button>
              </div>
              <p v-if="sessionState(editingSession.session_id)?.status === 'needs_review'" class="ar-note">先在下方记录中核对本次发送，再恢复会话；旧批次不会补发。</p>
            </div>
          </details>
        </fieldset>
      </div>

      <section v-if="needsStart" class="ar-runtime" aria-label="微信发送确认">
        <p v-if="runtime.state === 'paused'" class="ar-warning">回复已暂停。处理原因后，请重新选择窗口并确认账号，再保存并应用。</p>
        <div class="ar-add-row"><UiSelect v-model="selectedWindow" :options="windowOptions" label="微信发送窗口" placeholder="检测后选择一个窗口" :disabled="!!busy" /><button type="button" :disabled="!!busy" @click="findWindows">{{ busy === 'windows' ? '检测中…' : '检测微信窗口' }}</button></div>
        <p v-if="windowsChecked && !windows.length" class="ar-warning">没有可绑定的微信主窗口。请打开受支持的 Windows 微信客户端后重新检测。</p>
        <p class="ar-note">发送时会切换到所选微信窗口；运行期间请勿切换微信账号。重启后需在此重新确认并应用。</p>
        <label class="ar-check ar-consent"><input v-model="confirmed" type="checkbox" data-testid="confirm-start" :disabled="!!busy" /><span>我确认数据账号「<strong :class="{ 'privacy-blur': privacyMode }">{{ account }}</strong>」就是所选窗口当前登录的微信账号，并允许向所选会话自动发送。</span></label>
      </section>
      <p v-if="runtime.state === 'needs_review' || attentionCount" class="ar-warning">有 {{ attentionCount }} 个会话暂停或待核对，请在“单独调整会话”或下方回复记录中处理。保存范围不会解除这些状态。</p>
      <div class="ar-save"><p class="ar-note">{{ applyReason || (dirty ? '有未应用的修改；当前回复仍按已保存范围执行。' : '仅回复应用之后的新消息。') }}</p><button type="button" class="ar-primary" :disabled="!canApply" @click="applySettings">{{ busy === 'apply' ? '正在应用…' : '保存并应用' }}</button></div>

      <section class="ar-history" aria-label="自动回复记录">
        <div class="ar-heading"><div><h4>回复记录</h4><p>“客户端已显示”仅表示本机界面证据，不代表对方收到或已读。</p></div><button type="button" :disabled="historyLoading || !!busy" @click="refresh">刷新状态</button><button type="button" :disabled="historyLoading || !!busy || !history" @click="deleteHistory()">{{ busy === 'delete-all-history' ? '删除中…' : '一键删除' }}</button></div>
        <p v-if="historyNotice" class="ais-feedback is-success" role="status">{{ historyNotice }}</p>
        <p v-if="refreshError" class="ar-warning" role="alert"><span :class="{ 'privacy-blur': privacyMode }">{{ refreshError }}</span>。请点击“刷新状态”重试。</p>
        <div class="ar-filters"><UiSelect v-model="historySession" :options="historySessionOptions" :privacy-mode="privacyMode" :disabled="!!busy || historyLoading" label="记录会话筛选" /><UiSelect v-model="historyStatus" :options="historyStatusOptions" :disabled="!!busy || historyLoading" label="记录状态筛选" /></div>
        <p class="ar-note">每页最多 5 条。一键删除会清理当前账号所有会话、所有分页的可删除记录，不受筛选影响。删除仅移除本程序的历史记录，不会删除或撤回微信消息；正在处理或待核对的记录会保留。</p>
        <p v-if="historyLoading" role="status">正在读取记录…</p>
        <p v-else-if="history && !history.items.length" class="ar-empty">当前筛选下暂无回复记录。</p>
        <ol v-if="history" class="ar-records">
          <li v-for="attempt in history.items" :key="attempt.id">
            <div class="ar-record-heading"><strong :class="{ 'privacy-blur': privacyMode }">{{ attempt.target_name }}</strong><span>{{ attemptLabel(attempt) }}</span><time>{{ formatDate(attempt.created_at) }}</time><button v-if="deletableStatuses.has(attempt.status)" type="button" :disabled="!!busy || historyLoading" @click="deleteHistory(attempt.id)">删除记录</button></div>
            <p v-if="attempt.draft" class="ar-draft" :class="{ 'privacy-blur': privacyMode }">{{ attempt.draft }}</p>
            <p v-if="attempt.error" class="ar-warning" :class="{ 'privacy-blur': privacyMode }">{{ errorText(attempt.error) }}</p>
            <p v-if="attempt.review" class="ar-note">人工核对：{{ reviewLabels[attempt.review.conclusion] }}；旧批次不会重发。</p>
            <details><summary>查看触发消息与提交证据</summary><dl><dt>模型</dt><dd>{{ attempt.model ? JSON.stringify(attempt.model) : '尚未调用模型' }}</dd><dt>提交阶段</dt><dd>{{ submissionPhaseLabels[attempt.submission_phase] || attempt.submission_phase || '未记录提交阶段' }} · {{ attempt.submission_count }} 次提交</dd><dt>客户端证据</dt><dd :class="{ 'privacy-blur': privacyMode }">{{ attempt.evidence ? JSON.stringify(attempt.evidence) : '尚无明确客户端证据' }}</dd></dl><pre :class="{ 'privacy-blur': privacyMode }">{{ JSON.stringify(attempt.messages, null, 2) }}</pre></details>
            <div v-if="attempt.status === 'needs_review'" class="ar-review"><p>请先在微信核对实际会话和正文。选择结论只结束或保留旧批，不会补发。</p><div><button type="button" :disabled="!!busy" @click="review(attempt.id, 'sent')">已核对发出</button><button type="button" :disabled="!!busy" @click="review(attempt.id, 'not_sent')">已核对未发出</button><button type="button" :disabled="!!busy" @click="review(attempt.id, 'uncertain')">无法确定</button></div></div>
          </li>
        </ol>
        <nav v-if="history" class="ar-pagination" aria-label="回复记录分页"><span>共 {{ history.total }} 条 · 第 {{ history.page }} 页</span><button type="button" :disabled="!!busy || historyLoading || history.page <= 1" @click="changePage(history.page - 1)">上一页</button><button type="button" :disabled="!!busy || historyLoading || history.page * history.page_size >= history.total" @click="changePage(history.page + 1)">下一页</button></nav>
      </section>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useChatAccountsStore } from '~/stores/chatAccounts'
import { usePrivacyStore } from '~/stores/privacy'
import UiSelect from './UiSelect.vue'
import AgentModelPicker from './chat/AgentModelPicker.vue'
import '~/assets/css/agent.css'

const props = defineProps({ account: { type: String, default: undefined }, api: { type: Object, default: null } })
const { privacyMode } = storeToRefs(usePrivacyStore())
const accountStore = props.account === undefined ? useChatAccountsStore() : null
const account = computed(() => props.account === undefined ? accountStore.selectedAccount || '' : props.account)
const accountOptions = computed(() => accountStore?.accounts.map(value => ({ value, label: accountStore.accountInfoByName[value]?.displayName || value })) || [])
const api = props.api || useAiApi()
const form = ref(null), saved = ref(''), runtime = ref(null), profiles = ref([]), conversations = ref([])
const loading = ref(false), busy = ref(''), error = ref(''), notice = ref(''), refreshError = ref('')
const windows = ref([]), windowsChecked = ref(false), selectedWindow = ref(''), confirmed = ref(false), resumeConfirmed = ref({}), editingSessionId = ref('')
const history = ref(null), historyLoading = ref(false), historyNotice = ref(''), historySession = ref(''), historyStatus = ref(''), page = ref(1)
let epoch = 0, historyRequest = 0, statusRequest = 0, timer, refreshing = false
const stateLabels = { off: '未运行', starting: '启动中', running: '运行中', paused: '暂停', needs_review: '待核对' }
const attemptLabels = { collecting: '收集中', generating: '生成中', pending: '待发送', submitting: '提交中', completed: '已完成', cancelled: '已取消', failed: '提交前失败', interrupted: '已中断', needs_review: '待核对' }
const deletableStatuses = new Set(['completed', 'cancelled', 'failed', 'interrupted'])
const submissionPhaseLabels = { intent_persisted: '提交意图已保存', client_displayed: '客户端已显示' }
const reviewLabels = { sent: '已发出', not_sent: '未发出', uncertain: '无法确定' }
const triggerOptions = [{ value: 'mentions', label: '仅 @ 我' }, { value: 'all', label: '全部新消息' }]
const errorText = value => typeof value === 'string' ? value : `${value.code}：${value.detail}`
const query = (path, current = account.value) => `/auto-reply/${path}?account=${encodeURIComponent(current)}`
const owned = (stamp, current) => stamp === epoch && current === account.value
const configPayload = value => ({ account: value.account, version: value.version, profile_id: value.profile_id, model_id: value.model_id,
  system_prompt: value.system_prompt, reasoning_effort: value.reasoning_effort ?? null, thinking_mode: value.thinking_mode ?? null,
  thinking_budget: value.thinking_budget ?? null, sessions: value.sessions.map(({ session_id, target_name, enabled, group_trigger }) => ({ session_id, target_name, enabled, group_trigger })) })
const checkedConfig = (value, current) => {
  if (value?.account !== current || !Number.isInteger(value.version) || !Array.isArray(value.sessions)) throw new Error('自动回复配置响应与当前账号不符或格式无效')
  return configPayload(value)
}
const checkedStatus = (value, current) => {
  if (value?.account !== current || !Object.hasOwn(stateLabels, value.state) || !Array.isArray(value.sessions)) throw new Error('自动回复状态响应与当前账号不符或格式无效')
  return value
}
const checkedHistory = (value, current) => {
  if (!Array.isArray(value?.items) || !Number.isInteger(value.total) || !Number.isInteger(value.page) || !Number.isInteger(value.page_size)) throw new Error('自动回复记录响应格式无效')
  if (value.items.some(item => item.account !== current)) throw new Error('自动回复记录响应包含其他账号的数据，已停止展示')
  return value
}
const dirty = computed(() => !!form.value && JSON.stringify(configPayload(form.value)) !== saved.value)
const savedSessions = computed(() => new Map((saved.value ? JSON.parse(saved.value).sessions : []).map(session => [session.session_id, session])))
const invalidNames = computed(() => form.value?.sessions.some(session => !session.target_name.trim() || /[\r\n\0]/.test(session.target_name)))
const modelChoice = computed({ get: () => ({ profile_id: form.value.profile_id, model_id: form.value.model_id, reasoning_effort: form.value.reasoning_effort, thinking_mode: form.value.thinking_mode, thinking_budget: form.value.thinking_budget }),
  set: choice => { for (const key of ['profile_id', 'model_id', 'reasoning_effort', 'thinking_mode', 'thinking_budget']) form.value[key] = choice[key] ?? (key.endsWith('_id') ? '' : null) } })
const scopeOptions = computed(() => {
  const options = new Map(conversations.value.map(item => [item.username, { value: item.username, label: item.name || item.username, description: item.username.endsWith('@chatroom') ? '群聊' : '私聊' }]))
  for (const session of form.value?.sessions || []) {
    if (!options.has(session.session_id)) options.set(session.session_id, { value: session.session_id, label: session.target_name, description: '已保存的会话' })
  }
  return [...options.values()]
})
const selectedSessionIds = computed({
  get: () => form.value.sessions.filter(session => session.enabled).map(session => session.session_id),
  set: ids => {
    const selected = new Set(ids), existing = new Set(form.value.sessions.map(session => session.session_id))
    for (const session of form.value.sessions) session.enabled = selected.has(session.session_id)
    for (const id of selected) {
      if (existing.has(id)) continue
      const item = conversations.value.find(item => item.username === id)
      form.value.sessions.push({ session_id: id, target_name: item.name || '', enabled: true, group_trigger: 'mentions' })
    }
  },
})
const scopeSummary = computed(() => {
  const groups = selectedSessionIds.value.filter(id => id.endsWith('@chatroom')).length
  return selectedSessionIds.value.length ? `已选 ${groups} 个群聊、${selectedSessionIds.value.length - groups} 个私聊` : ''
})
const editingSession = computed(() => form.value?.sessions.find(session => session.session_id === editingSessionId.value))
const sessionOptions = computed(() => form.value.sessions.map(session => ({ value: session.session_id, label: sessionName(session), description: sessionLabel(session) })))
const attentionCount = computed(() => runtime.value?.sessions.filter(session => ['paused', 'needs_review'].includes(session.status)).length || 0)
const needsStart = computed(() => !!runtime.value && !!selectedSessionIds.value.length && ['off', 'paused'].includes(runtime.value.state))
const applyReason = computed(() => {
  if (!form.value || !runtime.value) return '请先加载配置。'
  if (!selectedSessionIds.value.length) return ''
  if (invalidNames.value) return '请在“单独调整会话”中填写有效的完整定位名称。'
  if (runtime.value.active_account && runtime.value.active_account !== account.value) return '请先停止其他账号的自动回复。'
  if (!form.value.profile_id || !form.value.model_id) return '请先选择固定模型。'
  if (runtime.value.state === 'starting') return '正在启动，请稍后应用修改。'
  if (needsStart.value && !selectedWindow.value) return '请检测并明确选择微信窗口。'
  if (needsStart.value && !confirmed.value) return '应用前需要确认当前微信账号。'
  return ''
})
const canApply = computed(() => !busy.value && !applyReason.value && (dirty.value || needsStart.value || (!selectedSessionIds.value.length && runtime.value.state !== 'off')))
const windowOptions = computed(() => windows.value.map(window => ({ value: String(window.hwnd), label: `微信窗口 ${window.hwnd} · 进程 ${window.pid}` })))
const historySessionOptions = computed(() => [{ value: '', label: '全部会话' }, ...(form.value?.sessions || []).map(session => ({ value: session.session_id, label: session.target_name }))])
const historyStatusOptions = [{ value: '', label: '全部状态' }, ...Object.entries(attemptLabels).map(([value, label]) => ({ value, label }))]
const sessionName = session => conversations.value.find(item => item.username === session.session_id)?.name || session.target_name
const sessionState = id => runtime.value?.sessions.find(session => session.session_id === id)
const sessionLabel = session => {
  const state = sessionState(session.session_id)?.status
  const enabled = !!savedSessions.value.get(session.session_id)?.enabled
  let label
  if (state === 'needs_review') label = '待核对'
  else if (!enabled) label = '未参与'
  else if (runtime.value.state === 'off') label = '已选·未运行'
  else if (runtime.value.state !== 'running') label = stateLabels[runtime.value.state]
  else label = state === 'off' || !state ? '未运行' : stateLabels[state]
  if (session.enabled !== enabled) label += session.enabled ? '·待保存加入' : '·待保存取消'
  return label
}
const formatDate = value => new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
const attemptLabel = attempt => attempt.evidence?.status === 'client_displayed' ? '客户端已显示' : attempt.review?.conclusion === 'sent' ? '人工核对已发出' : attemptLabels[attempt.status] || attempt.status
const historyPath = current => `${query('history', current)}&page=${page.value}&page_size=5${historySession.value ? `&session_id=${encodeURIComponent(historySession.value)}` : ''}${historyStatus.value ? `&status=${encodeURIComponent(historyStatus.value)}` : ''}`

async function load() {
  const stamp = ++epoch, current = account.value
  historyRequest++; statusRequest++; refreshing = false
  historyLoading.value = false
  form.value = null; runtime.value = null; history.value = null; busy.value = ''; error.value = ''; notice.value = ''; refreshError.value = ''; historyNotice.value = ''
  windows.value = []; windowsChecked.value = false; selectedWindow.value = ''; confirmed.value = false; resumeConfirmed.value = {}; editingSessionId.value = ''
  page.value = 1; historySession.value = ''; historyStatus.value = ''; loading.value = !!current
  if (!current) return
  try {
    const [configuration, state, settings, chats, records] = await Promise.all([
      api.request(query('config', current)), api.request(query('status', current)), api.request('/settings'),
      api.request(`/conversations?account=${encodeURIComponent(current)}`), api.request(historyPath(current)),
    ])
    if (!owned(stamp, current)) return
    const next = checkedConfig(configuration, current), nextState = checkedStatus(state, current), nextHistory = checkedHistory(records, current)
    if (!Array.isArray(settings?.profiles) || !Array.isArray(chats)) throw new Error('模型服务或会话名单响应格式无效')
    form.value = next; saved.value = JSON.stringify(next); runtime.value = nextState; profiles.value = settings.profiles; conversations.value = chats; history.value = nextHistory
  } catch (failure) { if (owned(stamp, current)) error.value = failure.message }
  finally { if (owned(stamp, current)) loading.value = false }
}

async function refreshProfiles() {
  const stamp = epoch, current = account.value
  try {
    const value = await api.request('/settings')
    if (!owned(stamp, current)) return
    if (!Array.isArray(value?.profiles)) throw new Error('模型服务响应格式无效')
    profiles.value = value.profiles
  } catch (failure) { if (owned(stamp, current)) error.value = failure.message }
}

async function refreshStatus(stamp = epoch, current = account.value) {
  const requestId = ++statusRequest
  const value = await api.request(query('status', current))
  if (owned(stamp, current) && requestId === statusRequest) runtime.value = checkedStatus(value, current)
}
async function loadHistory() {
  if (!form.value) return
  const stamp = epoch, current = account.value, requestId = ++historyRequest
  historyLoading.value = true
  try {
    let value = await api.request(historyPath(current))
    if (!owned(stamp, current) || requestId !== historyRequest) return
    let next = checkedHistory(value, current)
    const lastPage = Math.max(1, Math.ceil(next.total / next.page_size))
    if (next.page > lastPage) {
      page.value = lastPage
      value = await api.request(historyPath(current))
      if (!owned(stamp, current) || requestId !== historyRequest) return
      next = checkedHistory(value, current)
    }
    history.value = next
  } catch (failure) { if (owned(stamp, current) && requestId === historyRequest) refreshError.value = failure.message }
  finally { if (owned(stamp, current) && requestId === historyRequest) historyLoading.value = false }
}
async function refresh() {
  if (!form.value || refreshing || busy.value) return
  const stamp = epoch, current = account.value
  refreshing = true; refreshError.value = ''
  try { await Promise.all([refreshStatus(stamp, current), loadHistory()]) }
  catch (failure) { if (owned(stamp, current)) refreshError.value = failure.message }
  finally { if (owned(stamp, current)) refreshing = false }
}
async function applySettings() {
  if (!canApply.value) return
  const stamp = epoch, current = account.value, shouldStart = needsStart.value
  const shouldStop = !selectedSessionIds.value.length && runtime.value.state !== 'off'
  const binding = shouldStart ? { ...windows.value.find(window => String(window.hwnd) === selectedWindow.value) } : null
  let stopped = false
  busy.value = 'apply'; error.value = ''; notice.value = ''
  try {
    if (shouldStop) {
      const state = await api.request('/auto-reply/stop', { method: 'POST', body: { account: current }, retry: 0 })
      if (!owned(stamp, current)) return
      runtime.value = checkedStatus(state, current); stopped = true
    }
    if (dirty.value) {
      const value = await api.request('/auto-reply/config', { method: 'PUT', body: configPayload(form.value), retry: 0 })
      if (!owned(stamp, current)) return
      form.value = checkedConfig(value, current); saved.value = JSON.stringify(form.value)
    }
    if (shouldStart) {
      const state = await api.request('/auto-reply/start', { method: 'POST', body: { account: current, binding, confirmed: true }, retry: 0 })
      if (!owned(stamp, current)) return
      runtime.value = checkedStatus(state, current)
    }
    confirmed.value = false
    await refreshStatus(stamp, current)
    if (owned(stamp, current)) notice.value = `设置已应用；当前状态：${stateLabels[runtime.value.state]}。`
  } catch (failure) {
    if (!owned(stamp, current)) return
    error.value = `${stopped ? '回复已停止，但设置未能应用：' : '应用未完成：'}${failure.message}`
    confirmed.value = false
    // 配置可能已持久化，而后续基线或启动失败。读取事实，不回滚、不重试提交。
    try {
      const [configuration, state] = await Promise.all([api.request(query('config', current)), api.request(query('status', current))])
      if (!owned(stamp, current)) return
      const next = checkedConfig(configuration, current), nextState = checkedStatus(state, current)
      form.value = next; saved.value = JSON.stringify(next); runtime.value = nextState
    } catch (readFailure) {
      if (owned(stamp, current)) {
        error.value += `；无法核对当前状态：${readFailure.message}。请重新加载。`
        form.value = null; runtime.value = null; saved.value = ''
      }
    }
  } finally { if (owned(stamp, current)) busy.value = '' }
}
async function findWindows() {
  if (busy.value) return
  const stamp = epoch, current = account.value
  busy.value = 'windows'; error.value = ''; selectedWindow.value = ''; confirmed.value = false
  try {
    const value = await api.request('/auto-reply/windows', { retry: 0 })
    if (!owned(stamp, current)) return
    if (!Array.isArray(value?.windows)) throw new Error('微信窗口列表响应格式无效')
    windows.value = value.windows; windowsChecked.value = true
  } catch (failure) { if (owned(stamp, current)) error.value = failure.message }
  finally { if (owned(stamp, current)) busy.value = '' }
}
async function mutate(kind, body) {
  if (busy.value) return
  const stamp = epoch, current = account.value
  busy.value = kind; error.value = ''; notice.value = ''
  try {
    await api.request(`/auto-reply/${kind}`, { method: 'POST', body, retry: 0 })
    if (!owned(stamp, current)) return
    confirmed.value = false; resumeConfirmed.value = {}
    await Promise.all([refreshStatus(stamp, current), loadHistory()])
  } catch (failure) { if (owned(stamp, current)) error.value = failure.message }
  finally { if (owned(stamp, current)) busy.value = '' }
}
const review = (attemptId, conclusion) => mutate('review', { account: account.value, attempt_id: attemptId, conclusion })
async function deleteHistory(attemptId) {
  if (busy.value || historyLoading.value) return
  const all = attemptId === undefined
  const confirmation = all
    ? '一键删除当前账号所有会话、所有分页中可删除的回复记录？此操作不受当前筛选影响，正在处理和待核对的记录会保留。仅从本程序的回复历史中移除，不会删除或撤回微信消息。'
    : '删除这条回复记录？仅从本程序的回复历史中移除，不会删除或撤回微信消息。'
  if (!window.confirm(confirmation)) return
  const stamp = epoch, current = account.value
  busy.value = all ? 'delete-all-history' : 'delete-history'; refreshError.value = ''; historyNotice.value = ''
  try {
    const value = await api.request(query(all ? 'history' : `history/${encodeURIComponent(attemptId)}`, current), { method: 'DELETE', retry: 0 })
    if (!owned(stamp, current)) return
    if (all) {
      if (value?.account !== current || !Number.isInteger(value.deleted_count) || value.deleted_count < 0) throw new Error('批量删除回复记录的响应与当前账号不符或数量无效')
      historyNotice.value = value.deleted_count ? `已删除 ${value.deleted_count} 条回复记录。` : '当前账号没有可删除的回复记录。'
      page.value = 1
    } else if (value?.account !== current || value.id !== attemptId || value.deleted !== true) throw new Error('删除回复记录的响应与当前账号或记录不符')
    history.value = null
    await loadHistory()
  } catch (failure) { if (owned(stamp, current)) refreshError.value = failure.message }
  finally { if (owned(stamp, current)) busy.value = '' }
}
const resume = sessionId => { if (resumeConfirmed.value[sessionId] && !dirty.value) return mutate('resume', { account: account.value, session_id: sessionId, confirmed: true }) }
const changePage = next => { page.value = next; loadHistory() }
watch([historySession, historyStatus], () => { page.value = 1; loadHistory() })
watch(selectedWindow, () => { confirmed.value = false })
watch(account, load, { immediate: true, flush: 'sync' })
onMounted(() => { timer = setInterval(() => { if (!refreshError.value) refresh() }, 5000) })
onBeforeUnmount(() => { epoch++; clearInterval(timer) })
</script>

<style scoped>
.auto-reply-settings { display: grid; gap: 18px; color: var(--ais-text); }
.ar-content { display: grid; gap: 18px; }
.ar-heading, .ar-account, .ar-save, .ar-record-heading, .ar-session-top, .ar-pagination { display: flex; align-items: center; gap: 10px; }
.ar-heading > div:first-child, .ar-account > :first-child, .ar-save > p, .ar-session-top > :first-child { flex: 1; min-width: 0; }
.ar-heading h4 { font-size: 14px; font-weight: 650; }
.ar-heading p, .ar-note { color: var(--ais-muted); font-size: 11px; line-height: 1.6; }
.ar-heading p { margin-top: 4px; }
.ar-state { flex: none; border: 1px solid var(--ais-border); border-radius: 6px; padding: 3px 7px; font-size: 11px; white-space: nowrap; }
.auto-reply-settings fieldset { border: 0; padding: 0; margin: 0 0 18px; display: grid; gap: 12px; min-width: 0; }
.auto-reply-settings legend { font-size: 13px; font-weight: 650; padding: 0 0 10px; }
.auto-reply-settings textarea { resize: vertical; min-height: 86px; width: 100%; padding: 9px 10px; border: 1px solid var(--ais-border); border-radius: 6px; font: inherit; line-height: 1.6; color: var(--ais-text); background: var(--ais-bg); }
.auto-reply-settings textarea:focus-visible { outline: 2px solid var(--ais-green); outline-offset: 2px; }
.ar-model, .ar-add-row, .ar-filters { display: flex; align-items: center; gap: 10px; }
.ar-model { flex-wrap: wrap; }
.ar-model :deep(.agent-selection-popover) { left: 0; right: auto; top: calc(100% + 6px); bottom: auto; }
.ar-add-row > :first-child { flex: 1; min-width: 0; }
.ar-add-row > button { flex: none; }
.ar-filters > * { flex: 1; min-width: 0; }
.ar-session { padding: 12px 0; border-bottom: 1px solid var(--ais-border); display: grid; gap: 9px; }
.ar-session-options > summary { cursor: pointer; color: var(--ais-muted); font-size: 12px; }
.ar-session-options > .ui-select { margin-top: 12px; }
.auto-reply-settings .ar-check { display: flex; flex-direction: row; align-items: flex-start; gap: 8px; font-size: 12px; line-height: 1.6; }
.ar-check input { flex: none; margin-top: 3px; }
.ar-check strong { overflow-wrap: anywhere; }
.ar-session-fields { display: grid; grid-template-columns: minmax(0, 1fr) minmax(130px, .6fr); gap: 12px; }
.ar-session-fields > :only-child { grid-column: 1 / -1; }
.ar-resume { display: grid; gap: 8px; justify-items: start; }
.ar-runtime, .ar-history { padding-top: 18px; border-top: 1px solid var(--ais-border); display: grid; gap: 12px; }
.auto-reply-settings .ar-primary { background: var(--ais-green); color: var(--ais-bg); border-color: var(--ais-green); }
.ar-consent { padding: 10px 0; }
.ar-warning { color: var(--app-text-primary); background: var(--ais-soft); padding: 8px 10px; border: 1px solid var(--ais-border); border-radius: 6px; overflow-wrap: anywhere; }
.ar-empty { padding: 16px 0; color: var(--ais-muted); }
.ar-records { list-style: none; padding: 0; margin: 0; }
.ar-records > li { padding: 13px 0; border-bottom: 1px solid var(--ais-border); display: grid; gap: 8px; }
.ar-record-heading { flex-wrap: wrap; font-size: 11px; }
.ar-record-heading strong { font-size: 12px; }
.ar-record-heading time { margin-left: auto; color: var(--ais-muted); font-variant-numeric: tabular-nums; }
.ar-draft { white-space: pre-wrap; overflow-wrap: anywhere; }
.ar-records summary { cursor: pointer; color: var(--ais-muted); font-size: 11px; }
.ar-records pre, .ar-records dd { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11px; margin: 5px 0 10px; }
.ar-records dt { margin-top: 8px; font-weight: 600; font-size: 11px; }
.ar-review { display: grid; gap: 8px; background: var(--ais-soft); padding: 10px; border-radius: 6px; }
.ar-review > div { display: flex; gap: 6px; flex-wrap: wrap; }
.ar-pagination { justify-content: flex-end; font-size: 11px; font-variant-numeric: tabular-nums; }
@container (max-width: 520px) {
  .ar-session-fields { grid-template-columns: 1fr; }
  .ar-heading, .ar-save, .ar-account { flex-wrap: wrap; }
  .ar-heading > div:first-child, .ar-save > p { flex-basis: 100%; }
  .ar-session-top { flex-wrap: wrap; }
}
</style>
