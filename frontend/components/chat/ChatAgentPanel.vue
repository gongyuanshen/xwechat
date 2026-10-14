<template>
  <aside ref="panelView" class="agent-panel" :class="{ 'is-page': isPage, 'is-page-empty': pageEmpty, 'is-expanded': expanded && !isPage, 'is-resizing': resizing }" :style="isPage ? undefined : { '--agent-panel-width': `${panelWidth}px` }" :role="expanded && !isPage ? 'dialog' : undefined" :aria-modal="expanded && !isPage ? true : undefined" aria-label="AI 助手" @keydown.esc="onEscape" @keydown.tab="!isPage && expanded && trapFocus($event, panelView)">
    <div v-if="!isPage && !expanded" class="agent-resizer" role="separator" tabindex="0" aria-label="调整 AI 助手宽度" aria-orientation="vertical" :aria-valuemin="minWidth" :aria-valuemax="maxWidth" :aria-valuenow="panelWidth" :aria-valuetext="`${panelWidth} 像素`" title="拖动调整宽度，双击恢复默认；方向键微调" @pointerdown="startResize" @lostpointercapture="finishResize" @keydown="resizeKeyboard" @dblclick="resetWidth" />
    <div class="agent-shell" :class="{ 'has-navigation': navigationOpen }">
    <AgentThreadList v-if="navigationOpen" :key="selectionKey" :heading="isPage ? 'AI 对话历史' : undefined" :description-for="isPage ? describeThreadScope : undefined" :items="history" :current="thread?.id" :running-ids="runningThreadIds" :loading="historyLoading" :busy="historyBusy" :error="historyError" :name-for="nameFor" :avatar-for="avatarFor" @new="newThread" @select="selectHistory" @refresh="loadHistory" @rename="renameHistory" @delete="deleteHistory" @settings="settings.openDialog('ai')" />
    <div class="agent-main">
    <header class="agent-header">
      <button type="button" aria-label="AI 对话历史" :title="navigationOpen ? '收起侧边栏' : '展开侧边栏'" :aria-expanded="navigationOpen" @click="openHistory"><PanelLeft :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
      <AgentAvatar v-if="contactUsername" class="agent-owner-avatar" :path="avatarFor(contactUsername)" :name="contactName" />
      <span v-if="!isPage" class="agent-owner" :title="contactName">{{ contactName }}</span><strong class="agent-thread-title" :title="thread?.title || (isPage ? 'AI 助手' : '新对话')">{{ thread?.title || (isPage ? 'AI 助手' : '新对话') }}</strong>
      <button type="button" aria-label="新建 AI 对话" title="新建对话" @click="mode = 'agent'; newThread()"><SquarePen :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
      <button v-if="!isPage" type="button" :aria-label="expanded ? '收起大视图' : '展开大视图'" :title="expanded ? '收起大视图' : '展开大视图'" @click="expanded = !expanded"><component :is="expanded ? Minimize2 : Maximize2" :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
      <div class="agent-menu-anchor" ref="menuAnchor">
        <button ref="menuTrigger" type="button" aria-label="更多 AI 功能" :aria-expanded="menuOpen" aria-controls="agent-more-menu" @click="menuOpen = !menuOpen"><Ellipsis :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
        <div v-if="menuOpen" id="agent-more-menu" class="agent-menu">
          <button type="button" aria-label="对话历史" @click="menuOpen = false; showHistory()"><History :size="16" :stroke-width="1.8" aria-hidden="true" />对话历史</button>
          <button type="button" @click="mode = 'agent'; menuOpen = false"><MessageCircleMore :size="16" :stroke-width="1.8" aria-hidden="true" />对话<Check v-if="mode === 'agent'" :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
          <button v-if="!isPage" type="button" @click="mode = 'tools'; menuOpen = false"><Toolbox :size="16" :stroke-width="1.8" aria-hidden="true" />工具与任务<Check v-if="mode === 'tools'" :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
          <button type="button" @click="menuOpen = false; settings.openDialog('ai')"><SlidersHorizontal :size="16" :stroke-width="1.8" aria-hidden="true" />AI 服务设置</button>
        </div>
      </div>
      <button v-if="!isPage" type="button" aria-label="关闭 AI 助手" title="关闭 AI 助手" @click="$emit('close')"><X :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
    </header>
    <div v-if="mode === 'tools'" class="agent-tools-heading"><button type="button" @click="mode = 'agent'"><ArrowLeft :size="14" :stroke-width="1.8" aria-hidden="true" />返回对话</button><strong>工具与任务</strong></div>
    <AiSidebar v-if="!isPage" v-show="mode === 'tools'" class="agent-tools" :account="account" :contact="contact" :contacts="contacts" :focus-task-id="focusTaskId" @locate="locate" />
    <template v-if="mode === 'agent'">
      <div class="agent-workspace" :class="{ 'has-source': inspectedSource }">
      <div class="agent-reading">
      <p v-if="connectionNotice" class="agent-error" role="status">{{ connectionNotice }}</p>
      <p v-if="error" class="agent-error" role="alert">{{ error }}</p>
      <p v-if="threadLoading" class="agent-loading" role="status">正在打开对话…</p>
      <div v-if="pageEmpty" class="agent-page-welcome"><h1>{{ sourceMode === 'library' ? '围绕保存的专题材料提问' : '你的本地聊天记忆，随时可查' }}</h1><p>{{ sourceMode === 'library' ? '选择专题与保存版本，回答引用该版本的材料。' : '查找往事、梳理进展，回答附上聊天原文。' }}</p></div>
      <AssistantThread v-show="!pageEmpty" :key="thread?.id || selectionKey" :messages="assistantMessages" :running="running" :expanded="expanded || isPage" @scroll="onScroll" @ready="onThreadReady">
        <template #welcome>
        <div v-if="!isPage && !thread?.messages?.length && !threadLoading" class="agent-welcome"><span class="agent-welcome-symbol"><MessageCircleMore :size="16" :stroke-width="1.8" aria-hidden="true" /></span><h3>想从聊天里了解什么？</h3><p>查找消息、梳理进展，或继续追问。<br>从当前聊天开始，可按需查找其他聊天，回答附上原文出处。</p><button v-for="q in suggestions" :key="q" type="button" :disabled="sending || running || !account || !contact?.username" @click="sendSuggestion(q)">{{ q }}<ArrowUp :size="16" :stroke-width="1.8" aria-hidden="true" /></button></div>
        </template>
        <template #message="{ message }">
          <div v-if="message.role === 'user'" class="agent-user"><p>{{ message.text }}</p></div>
          <template v-else-if="message.turn.detail">
            <AgentRun :run="message.turn.detail" :now="now" :near-bottom="nearBottom" :latest="message.turn.id === run?.id" :name-for="nameFor" :view-state="processView" @locate="locate" @choose="chooseContact" @continue="runAction('continue')" @restart="restartRun(message.turn.id)" @settings="settings.openDialog('ai')" />
            <div v-if="message.turn.detail.status === 'completed' && message.turn.detail.answer" class="agent-result-actions"><button type="button" @click="reportToSave = {kind: 'report', run_id: message.turn.id}">保存报告</button></div>
          </template>
          <section v-else class="agent-reply">
            <button v-if="pastRunLoads[pastRunKey(message.turn.id)] === 'failed'" type="button" class="agent-process-toggle" @click="loadPastRun(message.turn.id)">处理过程加载失败，点击重试</button>
            <p v-else class="agent-loading" role="status">正在加载处理过程…</p>
            <AgentAnswer v-if="message.turn.answer" :text="message.turn.answer.text" :citations="(message.turn.answer.citations || []).map(source => source.kind === 'library' ? { ...source, run_id: message.turn.id } : source)" :references="message.turn.answer.references" @locate="locate" />
            <div v-if="message.turn.answer?.text" class="agent-result-actions"><AgentCopyAction :text="message.turn.answer.text" :citations="message.turn.answer.citations" :references="message.turn.answer.references" /></div>
          </section>
        </template>
      </AssistantThread>
      <button v-if="newContent" type="button" class="agent-new-content" @click="toBottom">有新内容 <ArrowDown :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
      <footer class="agent-composer">
        <AgentLibraryScopeNotice v-if="isPage && libraryScope" :scope="libraryScope" />
        <p v-if="isPage && sourceMode === 'library'" class="agent-scope-lock">仅查阅所选保存版本；不读取当前资料夹、其他版本或聊天记录。</p>
        <p v-if="isPage && !canReadChats" class="agent-scope-lock">当前账号仅有恢复的应用资料；导入聊天数据后可查阅聊天记录。</p>
        <div class="agent-input-box">
          <textarea ref="draftInput" v-model="draft" aria-label="给 AI 助手的消息" :placeholder="running ? '可以补充要求，例如：只看上周的…' : sourceMode === 'library' ? (libraryScope ? '向此版本的材料提问…' : '请先选择专题保存版本') : isPage ? '想从聊天里了解什么？' : (contact?.username ? '向当前聊天提问…' : '请先选择一个聊天')" rows="1" @input="resizeDraft" @keydown.enter.exact="sendOnEnter" @compositionstart="composing = true" @compositionend="composing = false" />
          <div class="agent-input-actions">
            <select v-if="isPage" class="agent-source-mode" aria-label="资料来源" :value="sourceMode" :disabled="running || sending || threadLoading" @change="changeSourceMode($event.target.value)"><option value="chat" :disabled="!canReadChats">{{ canReadChats ? '聊天记录' : '聊天记录（未导入）' }}</option><option value="library">专题版本</option></select>
            <AgentChatScopePicker v-if="isPage && sourceMode === 'chat' && canReadChats" :account="account" :model-value="chatScope" :disabled="running || sending || threadLoading" @update:model-value="changeChatScope" @loaded="directory = $event" />
            <AgentLibraryScopePicker v-if="isPage && sourceMode === 'library'" :key="`${selectionKey}:${thread?.id || saved.newKeys[selectionKey]}`" :account="account" :model-value="libraryScope" :disabled="running || sending || threadLoading" @update:model-value="changeLibraryScope" />
            <AgentContextRing :budget="run?.context_budget" />
            <AgentModelPicker v-model="modelChoice" :profiles="profiles" :profiles-loading="profilesLoading" :profiles-error="profilesError" @refresh="loadProfiles" />
            <button type="button" class="agent-send" :disabled="running ? stopping : (threadLoading || sending || !draft.trim() || !account || !sourceReady || (!isPage && !contact?.username))" :aria-label="running ? '停止处理' : '发送问题'" @click="primaryAction"><span v-if="running" class="agent-stop-icon" aria-hidden="true" /><ArrowUp v-else :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
          </div>
        </div>
        <small v-if="isPage && running" class="agent-scope-lock" role="status">{{ sourceMode === 'library' ? '停止处理后可更改专题版本' : '停止处理后可更改聊天范围' }}</small>
        <small v-if="modelSelection.state.notice" class="agent-error" role="status">{{ modelSelection.state.notice }} <button v-if="modelSelection.state.dirty" type="button" @click="modelSelection.choose(modelChoice)">重试保存</button></small>
      </footer>
      </div>
      <AgentSourceInspector v-if="inspectedSource" :source="inspectedSource.source" :number="inspectedSource.number" :prepare="prepareSource" :locate="locateSource" :read-material="readMaterial" @close="closeInspector(true)" />
      </div>
    </template>
    </div>
    </div>

    <LibrarySaveDialog v-if="reportToSave" :open="!!reportToSave" :account="account" :item="reportToSave" @close="reportToSave = null" @saved="reportToSave = null" />
  </aside>
</template>

<script setup>
import { computed, ref, watch, onMounted, onUnmounted, nextTick, provide } from 'vue'
import LibrarySaveDialog from '~/components/library/LibrarySaveDialog.vue'
import AiSidebar from './AiSidebar.vue'
import AgentAnswer from './AgentAnswer.vue'
import AgentCopyAction from './AgentCopyAction.vue'
import AgentRun from './AgentRun.vue'
import AgentSourceInspector from './AgentSourceInspector.vue'
import AssistantThread from './AssistantThread.vue'
import AgentContextRing from './AgentContextRing.vue'
import AgentModelPicker from './AgentModelPicker.vue'
import AgentThreadList from './AgentThreadList.vue'
import AgentAvatar from './AgentAvatar.vue'
import AgentChatScopePicker from './AgentChatScopePicker.vue'
import AgentLibraryScopePicker from './AgentLibraryScopePicker.vue'
import AgentLibraryScopeNotice from './AgentLibraryScopeNotice.vue'
import { useAgentPanelResize } from '~/composables/useAgentPanelResize'
import { mergeTimeline, mergeReferenceData, mergeRunEvent } from '~/utils/agentTimeline'
import { agentModelSelection } from '~/lib/agent-model-selection'
import { ArrowDown, ArrowLeft, ArrowUp, Check, Ellipsis, History, Maximize2, MessageCircleMore, Minimize2, PanelLeft, SlidersHorizontal, SquarePen, Toolbox, X } from '@lucide/vue'
import '~/assets/css/agent.css'
const props = defineProps({ account: String, contact: Object, contacts: Array, focusTaskId: String, locateSource: Function, prepareSource: Function, profileState: Object, presentation: { type: String, default: 'panel' }, canReadChats: { type: Boolean, default: true }, libraryTarget: Object })
const isPage = computed(() => props.presentation === 'page')
const emit = defineEmits(['close', 'locate', 'expanded'])
provide('chatContactProfileState', props.profileState)
const api = useAiApi(), settings = useSettingsDialog()
const saved = useState('chat-agent-ui', () => ({ selected: {}, drafts: {}, pinned: {} }))
const mode = ref(!isPage.value && props.focusTaskId ? 'tools' : 'agent'), expanded = ref(false), thread = ref(null), run = ref(null)
const pastRuns = ref({})
const reportToSave = ref(null)
watch(() => props.account, () => { reportToSave.value = null }, { flush: 'sync' })
saved.value.process ||= {}
saved.value.views ||= {}
saved.value.chatScopes ||= {}
saved.value.libraryScopes ||= {}
saved.value.sourceModes ||= {}
saved.value.newKeys ||= {}
const pageEmpty = computed(() => isPage.value && !thread.value?.messages?.length && !threadLoading.value)
const stopping = ref(false)
const modelSelection = agentModelSelection(saved.value, api.request)
const modelChoice = computed({ get: () => modelSelection.state.choice, set: value => { void modelSelection.choose(value) } })
const processView = computed(() => saved.value.process)
const error = ref(''), streamWarning = ref(''), syncWarning = ref(''), sending = ref(false), now = ref(Date.now()), composing = ref(false)
const streamConnected = ref(false)
const connectionNotice = computed(() => streamWarning.value || syncWarning.value)
const profiles = ref([])
const profilesLoading = ref(false), profilesError = ref('')
let profilesRequest = null
const history = ref([]), directory = ref([]), dialog = ref('')
const navigationOpen = ref(false), historyLoading = ref(false), historyBusy = ref(false), historyError = ref(''), threadLoading = ref(false), pendingThreadId = ref('')
let historyVersion = 0, historyPending = 0, lastHistorySync = 0, statusRevision = 0
const runStatuses = ref({})
// 状态按任务保存，切换当前会话不会清除其他会话的运行指示。
const rememberStatus = (id, status) => { if (id && typeof status === 'string') runStatuses.value[id] = {status,revision:++statusRevision} }
const runningThreadIds = computed(() => history.value.filter(item => ['queued','running'].includes(runStatuses.value[item.latest_run]?.status ?? item.latest_run_status)).map(item => item.id))
const menuOpen = ref(false), menuAnchor = ref(null), menuTrigger = ref(null), composerSettings = ref(false), draftInput = ref(null)
const inspectedSource = ref(null), canInspect = ref(false)
let panelObserver, sourceTrigger = null
const closeInspector = (restoreFocus = false) => {
  sourceTrigger?.setAttribute('aria-expanded', 'false')
  sourceTrigger?.removeAttribute('aria-controls')
  if (restoreFocus && sourceTrigger?.isConnected) sourceTrigger.focus({ preventScroll: true })
  sourceTrigger = null; inspectedSource.value = null
}
const inspectSource = (source, number, button) => {
  if ((!isPage.value && !expanded.value) || !canInspect.value) return false
  if (sourceTrigger === button) { closeInspector(true); return true }
  closeInspector()
  sourceTrigger = button
  button.setAttribute('aria-expanded', 'true'); button.setAttribute('aria-controls', 'agent-source-inspector')
  inspectedSource.value = { source, number }
  return true
}
const resizeDraft = () => {
  const input = draftInput.value
  if (!input) return
  input.style.height = 'auto'
  input.style.height = `${Math.min(144, Math.max(28, input.scrollHeight))}px`
}
const onOutside = event => { if (!menuAnchor.value?.contains(event.target)) menuOpen.value = false }
const panelView = ref(null), scrollView = ref(null), nearBottom = ref(true), newContent = ref(false)
const { width: panelWidth, minimum: minWidth, maximum: maxWidth, resizing, start: startResize, finish: finishResize, reset: resetWidth, keyboard: resizeKeyboard } = useAgentPanelResize(panelView, expanded)
const onThreadReady = element => { scrollView.value = element; if (nearBottom.value) void toBottom() }
const trapFocus = (event, root) => {
  if (!root) return
  const elements = [...root.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]')].filter(el => el.getClientRects().length)
  const first = elements[0], last = elements.at(-1)
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
const pinned = computed(() => !!saved.value.pinned[props.account])
const selectionKey = computed(() => isPage.value ? `page:${props.account}:agent` : `${props.account}:${props.contact?.username || 'none'}`)
const draftKey = computed(() => `${selectionKey.value}:${thread.value?.id || (isPage.value ? saved.value.newKeys[selectionKey.value] ||= crypto.randomUUID() : 'new')}`)
const draft = computed({ get: () => saved.value.drafts[draftKey.value] || '', set: value => { saved.value.drafts[draftKey.value] = value } })
const chatScope = computed(() => thread.value ? thread.value.chat_scope : saved.value.chatScopes[selectionKey.value] ?? null)
const libraryScope = computed(() => thread.value ? thread.value.library_scope : saved.value.libraryScopes[selectionKey.value])
const sourceMode = computed(() => thread.value ? (thread.value.library_scope ? 'library' : 'chat') : saved.value.sourceModes[selectionKey.value] || (props.canReadChats ? 'chat' : 'library'))
const sourceReady = computed(() => !isPage.value || (sourceMode.value === 'library' ? !!libraryScope.value : props.canReadChats))
const nameFor = id => [props.contact, ...(props.contacts || []), ...directory.value].find(c => c?.username === id)?.name || id
const describeThreadScope = item => item.library_scope ? `${item.library_scope.folder_name} · 第 ${item.library_scope.version_number} 版 · ${item.library_scope.version_name}` : item.chat_scope === null ? '所有聊天' : item.chat_scope.length === 1 ? nameFor(item.chat_scope[0]) : `${item.chat_scope.length} 个聊天`
const avatarFor = username => username && props.account ? `/chat/avatar?${new URLSearchParams({account:props.account,username})}` : ''
const contactUsername = computed(() => isPage.value ? '' : thread.value?.username || props.contact?.username || '')
const contactName = computed(() => nameFor(contactUsername.value) || '当前聊天')
const running = computed(() => ['queued', 'running'].includes(run.value?.status))
const suggestions = ['最近讨论了哪些重要的事？', '帮我找一下之前提过的报价', '有哪些事情还没确认？']
const turns = computed(() => (thread.value?.messages || []).filter(m => m.role === 'user' && !m.supplement).map(m => ({id:m.run_id,question:m.text,detail:m.run_id === run.value?.id ? run.value : pastRuns.value[m.run_id],answer:thread.value.messages.find(a=>a.role==='assistant' && a.run_id===m.run_id)})))
const assistantMessages = computed(() => turns.value.flatMap(turn => [
  { id: `${turn.id}:user`, role: 'user', text: turn.question, turn },
  { id: `${turn.id}:assistant`, role: 'assistant', text: turn.detail?.answer || turn.answer?.text || '', status: turn.detail?.status, running: ['queued', 'running'].includes(turn.detail?.status), turn },
]))
const pastRunLoads = ref({})
const pastRunKey = id => JSON.stringify([props.account, thread.value?.id, id])
// 历史过程自动读取；同一请求去重，切换会话后丢弃迟到的结果。
const loadPastRun = async id => {
  const key = pastRunKey(id)
  if (!id || pastRuns.value[id] || pastRunLoads.value[key] === 'loading') return
  const account = props.account, threadId = thread.value?.id, current = version
  const isCurrent = () => !disposed && account === props.account && threadId === thread.value?.id && current === version
  pastRunLoads.value[key] = 'loading'
  try {
    const result = await api.request(`/agent/runs/${id}`, { query: { account }, timeout: 12000 })
    if (!result?.id) throw new Error('处理过程返回格式异常')
    if (isCurrent()) pastRuns.value[id] = result
  } catch {
    if (isCurrent()) pastRunLoads.value[key] = 'failed'
  } finally {
    if (pastRunLoads.value[key] === 'loading') delete pastRunLoads.value[key]
  }
}

let disposed = false, version = 0, timer, events, refreshing = false, refreshRetryTimer, refreshRetryAttempt = 0
const cancelRefreshRetry = (reset = true) => {
  clearTimeout(refreshRetryTimer)
  refreshRetryTimer = undefined
  if (reset) refreshRetryAttempt = 0
}
const scheduleRefreshRetry = () => {
  if (refreshRetryTimer || disposed || !thread.value) return
  const account = props.account, id = thread.value.id, current = version
  const delay = Math.min(6000, 750 * (2 ** refreshRetryAttempt++))
  refreshRetryTimer = setTimeout(() => {
    refreshRetryTimer = undefined
    if (!disposed && account === props.account && id === thread.value?.id && current === version) void refresh()
  }, delay)
}
// 首次无缓存也主动读取；挂载、打开下拉框和设置关闭时共用正在进行的请求。
const loadProfiles = () => {
  if (disposed) return Promise.resolve()
  if (profilesRequest) return profilesRequest
  profilesLoading.value = true; profilesError.value = ''
  const modelTicket = modelSelection.beginLoad()
  profilesRequest = Promise.resolve().then(() => api.request('/settings', { timeout: 12000 })).then(data => {
    if (disposed) return
    if (!Array.isArray(data?.profiles)) throw new Error('模型配置返回格式异常')
    profiles.value = data.profiles
    modelSelection.loaded(data, modelTicket)
  }).catch(() => {
    if (!disposed) profilesError.value = '模型列表加载失败'
  }).finally(() => {
    profilesRequest = null
    if (!disposed) profilesLoading.value = false
  })
  return profilesRequest
}
const params = () => ({ account: props.account })
const guardAction = async (fn, current = null) => { const account = props.account, key = selectionKey.value; error.value = ''; try { await fn() } catch (e) { if (!disposed && account === props.account && key === selectionKey.value && (current === null || current === version)) error.value = e.message } }
const rememberView = (account = thread.value?.account || props.account) => {
  if (thread.value?.id) saved.value.views[`${account}:${thread.value.id}`] = { thread: thread.value, run: run.value, pastRuns: pastRuns.value, top: scrollView.value?.scrollTop || 0, nearBottom: nearBottom.value }
}
const restoreScroll = async top => { await nextTick(); if (scrollView.value) scrollView.value.scrollTop = top }
const readThread = async id => {
  if (thread.value?.id === id) { await refresh(); return true }
  rememberView(); closeInspector(); cancelRefreshRetry(); syncWarning.value = ''
  const account = props.account, current = ++version
  // 操作标志属于当前打开的对话；旧请求继续执行，但不能占用新对话的控件。
  sending.value = false; stopping.value = false
  const cache = saved.value.views[`${account}:${id}`]
  thread.value = cache?.thread || null; run.value = cache?.run || null; pastRuns.value = cache?.pastRuns || {}
  nearBottom.value = cache?.nearBottom ?? true
  threadLoading.value = !cache; pendingThreadId.value = id
  if (cache) void restoreScroll(cache.top)
  try {
    const detail = await api.request(`/agent/threads/${id}`, { query: { account } })
    const task = detail.latest_run ? await api.request(`/agent/runs/${detail.latest_run}`, { query: { account } }) : null
    if (disposed || account !== props.account || current !== version) return false
    thread.value = detail; run.value = task
    if (cache) await restoreScroll(cache.top); else await toBottom()
    return true
  } finally { if (current === version) { threadLoading.value = false; pendingThreadId.value = '' } }
}
const loadSelection = async () => {
  if (!props.account || (!isPage.value && !props.contact?.username)) { thread.value = null; run.value = null; return }
  const account = props.account, key = selectionKey.value
  const chosen = saved.value.selected[key]
  if (chosen) { await readThread(chosen); return }
  // 独立入口的新对话只在发送时创建；历史列表不替用户选择范围。
  if (isPage.value) return
  const list = await api.request('/agent/threads', { query: { account, username:props.contact.username } })
  if (disposed || props.account !== account || key !== selectionKey.value) return
  if (list[0]) { saved.value.selected[key] = list[0].id; await readThread(list[0].id) }
}
const refresh = async () => {
  if (!thread.value || refreshing || disposed) return
  const account = props.account, id = thread.value.id, current = version
  refreshing = true
  try {
    const detail = await api.request(`/agent/threads/${id}`, { query:{account}, timeout:12000 })
    const task = detail.latest_run ? await api.request(`/agent/runs/${detail.latest_run}`, { query:{account}, timeout:12000 }) : null
    if (!disposed && account === props.account && id === thread.value?.id && current === version && (!task || !run.value || task.id !== run.value.id || ((task.version || 0) >= (run.value.version || 0) && (task.updated_at || 0) >= (run.value.updated_at || 0)))) {
      if (task?.id === run.value?.id) {
        task.timeline = mergeTimeline(run.value.timeline, task.timeline)
        // 较晚返回的快照可能早于已显示的 SSE 正文；同轮身份映射合并保留。
        if (task.version === run.value.version) {
          task.citations = mergeReferenceData(task.citations, run.value.citations)
          task.references = mergeReferenceData(task.references, run.value.references, 'id')
        }
      }
      const streamed = task?.timeline?.find(item => item.id === `answer:${task.id}`)
      if (streamed && streamed.status !== 'superseded') task.answer = streamed.text
      thread.value = detail; run.value = task; error.value = ''; syncWarning.value = ''; cancelRefreshRetry()
    } else api.diagnostic?.('response.stale', { thread_id: id, run_id: task?.id, component: 'agent' })
  } catch {
    if (!disposed && id === thread.value?.id && account === props.account && current === version) {
      syncWarning.value = '状态同步较慢，正在重试。后台任务不受影响，请勿重复提交。'
      scheduleRefreshRetry()
    }
  }
  finally { refreshing = false }
}
const ensureThread = async () => {
  if (thread.value) return thread.value
  const account = props.account, key = selectionKey.value, oldDraft = draft.value, current = version
  const body = isPage.value ? { account, username: '', origin: 'agent', ...(sourceMode.value === 'library' ? { library_scope: { folder_id: libraryScope.value.folder_id, version_id: libraryScope.value.version_id } } : { chat_scope: chatScope.value }) } : { account, username: props.contact.username }
  const created = await api.request('/agent/threads', {method:'POST',body})
  if (disposed || account !== props.account || key !== selectionKey.value || current !== version) throw new Error('聊天已切换，请在当前对话重新发送')
  saved.value.selected[key] = created.id; thread.value = created; saved.value.drafts[draftKey.value] = oldDraft; void loadHistory(); return created
}
const send = async () => {
  if (threadLoading.value || sending.value || !props.account || !draft.value.trim() || !sourceReady.value || (!isPage.value && !props.contact?.username)) return
  if (new TextEncoder().encode(draft.value.trim()).length > 1048576) { error.value = '输入超过 1 MiB，请分次发送。'; return }
  const text = draft.value.trim(), oldKey = draftKey.value, sendKey = selectionKey.value, current = version, sendAccount = props.account
  sending.value = true
  await guardAction(async () => {
    if (profilesLoading.value) await loadProfiles()
    if (disposed || sendAccount !== props.account || sendKey !== selectionKey.value || current !== version) return
    if (!modelChoice.value.profile_id || !profiles.value.some(p => p.id === modelChoice.value.profile_id)) throw new Error('请先在输入框下方选择模型，或在 AI 服务中添加服务配置。')
    // 提交期间切换模型只影响下一次发送。
    const choice = { ...modelChoice.value }
    const t = await ensureThread()
    if (disposed || sendAccount !== props.account || sendKey !== selectionKey.value || current !== version) return
    const account = sendAccount
    // 同一份草稿失败重试时沿用请求 ID，避免网络超时造成重复调用。
    const pendingKey = `pending:${account}:${t.id}:${text}`
    const requestId = saved.value.drafts[pendingKey] ||= crypto.randomUUID()
    const previousRunId = run.value?.id || ''
    const result = await api.request(`/agent/threads/${t.id}/messages`, {method:'POST',query:{account},body:{text,request_id:requestId,...choice}})
    delete saved.value.drafts[pendingKey]; if ((saved.value.drafts[oldKey] || '').trim() === text) saved.value.drafts[oldKey] = ''
    if (!disposed && account === props.account && thread.value?.id === t.id && current === version) {
      if (draft.value.trim() === text) draft.value = ''
      const supplement = !!previousRunId && result.id === previousRunId
      const messages = [...(thread.value.messages || [])]
      if (!messages.some(message => message.request_id === requestId)) messages.push({id:requestId,role:'user',text,run_id:result.id,request_id:requestId,supplement})
      thread.value = {...thread.value,latest_run:result.id,messages,title:thread.value.title === '新的对话' ? text.slice(0,30) : thread.value.title}
      run.value = result
      await toBottom()
    }
    if (account === props.account) void loadHistory()
  }, current)
  if (sendKey === selectionKey.value && current === version) sending.value = false
}
const sendSuggestion = async question => {
  if (threadLoading.value || sending.value || running.value || !props.account || (!isPage.value && !props.contact?.username)) return
  draft.value = question
  await send()
}
const primaryAction = async () => {
  if (!running.value) return send()
  if (stopping.value) return
  stopping.value = true
  const current = version
  try { await runAction('stop') } finally { if (current === version) stopping.value = false }
}
const sendOnEnter = event => { if (event.isComposing || composing.value) return; event.preventDefault(); void send() }
const newThread = () => guardAction(async () => {
  rememberView(); mode.value = 'agent'; closeInspector(); cancelRefreshRetry()
  if (!isPage.value) delete saved.value.pinned[props.account]
  delete saved.value.selected[selectionKey.value]
  ++version; threadLoading.value = false; thread.value = null; run.value = null
  sending.value = false; stopping.value = false; pastRuns.value = {}; error.value = ''; syncWarning.value = ''
  if (isPage.value) { saved.value.selected[selectionKey.value] = null; saved.value.chatScopes[selectionKey.value] = null; delete saved.value.libraryScopes[selectionKey.value]; delete saved.value.sourceModes[selectionKey.value]; saved.value.newKeys[selectionKey.value] = crypto.randomUUID() }
  showHistory()
  if (!expanded.value) navigationOpen.value = false
  await toBottom(); draftInput.value?.focus()
})
const changeChatScope = scope => {
  if (!isPage.value || running.value || sending.value || threadLoading.value) return
  if (JSON.stringify(scope) === JSON.stringify(chatScope.value)) return
  const text = draft.value
  rememberView(); closeInspector(); cancelRefreshRetry(); ++version
  thread.value = null; run.value = null; pastRuns.value = {}; error.value = ''; syncWarning.value = ''; newContent.value = false
  saved.value.selected[selectionKey.value] = null
  saved.value.newKeys[selectionKey.value] = crypto.randomUUID()
  saved.value.chatScopes[selectionKey.value] = scope
  saved.value.sourceModes[selectionKey.value] = 'chat'
  delete saved.value.libraryScopes[selectionKey.value]
  draft.value = text
  void nextTick(() => draftInput.value?.focus())
}
const changeLibraryScope = scope => {
  if (!isPage.value || running.value || sending.value || threadLoading.value) return
  if (sourceMode.value === 'library' && libraryScope.value?.folder_id === scope.folder_id && libraryScope.value?.version_id === scope.version_id) return
  startSource('library', scope)
}
const startSource = (source, scope = null) => {
  const text = draft.value
  rememberView(); closeInspector(); cancelRefreshRetry(); ++version
  thread.value = null; run.value = null; pastRuns.value = {}; error.value = ''; syncWarning.value = ''; newContent.value = false
  sending.value = false; stopping.value = false; threadLoading.value = false; pendingThreadId.value = ''
  saved.value.selected[selectionKey.value] = null
  saved.value.newKeys[selectionKey.value] = crypto.randomUUID()
  saved.value.sourceModes[selectionKey.value] = source
  saved.value.libraryScopes[selectionKey.value] = scope
  saved.value.chatScopes[selectionKey.value] = null
  draft.value = text
  void nextTick(() => draftInput.value?.focus())
}
const changeSourceMode = source => {
  if (running.value || sending.value || threadLoading.value || source === sourceMode.value || (source === 'chat' && !props.canReadChats)) return
  startSource(source)
}
const togglePin = () => guardAction(async () => { if (pinned.value) { delete saved.value.pinned[props.account]; await loadSelection() } else { const t = await ensureThread(); saved.value.pinned[props.account] = t.id } })
const locateSource = async source => {
  rememberView()
  const result = props.locateSource ? await props.locateSource(source) : emit('locate', source)
  if (result === false) return false
  closeInspector()
  expanded.value = false
  return result
}
// 提供可等待的定位回调，让每条引用能准确显示进行中、成功与失败状态。
const readMaterial = (source, offset) => api.request(`/agent/runs/${encodeURIComponent(source.run_id)}/materials/${encodeURIComponent(source.source)}`, { query: { account: props.account, version: source.run_version, offset } })
provide('agentSourceNavigation', { locate: locateSource, prepare: source => props.prepareSource?.(source), inspect: inspectSource, readMaterial })
const locate = source => guardAction(() => locateSource(source))
const runAction = action => {
  const id = run.value.id, account = props.account, current = version
  return guardAction(async () => {
    const result = await api.request(`/agent/runs/${id}/${action}`, {method:'POST',query:{account}})
    if (!disposed && run.value?.id === id && props.account === account && current === version) run.value = result
  }, current)
}
const restartRequests = new Map()
const restartRun = id => guardAction(async () => {
  const account = props.account, selectedThread = thread.value?.id
  const key = `${account}:${id}`
  if (!restartRequests.has(key)) restartRequests.set(key, crypto.randomUUID())
  const result = await api.request(`/agent/runs/${id}/restart`, {method:'POST', query:{account}, body:{request_id:restartRequests.get(key)}})
  if (props.account === account && thread.value?.id === selectedThread) {
    run.value = result
    await refresh()
    await toBottom()
  }
})
const loadHistory = async ({silent = false} = {}) => {
  const account = props.account, username = props.contact?.username, key = selectionKey.value, current = ++historyVersion, revision = statusRevision
  const query = isPage.value ? { account, origin: 'agent' } : { account, username }
  historyPending++; lastHistorySync = Date.now()
  if (!silent) { historyLoading.value = true; historyError.value = '' }
  try {
    const items = account && (isPage.value || username) ? await api.request('/agent/threads', {query,timeout:12000}) : []
    if (!disposed && account === props.account && key === selectionKey.value && current === historyVersion) {
      for (const item of items) {
        // 请求发出后收到的新事件优先，避免旧列表把已完成任务重新显示成运行中。
        if (item.latest_run_status != null && (runStatuses.value[item.latest_run]?.revision ?? 0) <= revision) rememberStatus(item.latest_run, item.latest_run_status)
      }
      // 静默刷新只更新内容，保留已有行的顺序，避免鼠标下的会话突然换位。
      if (silent) {
        const incoming = new Map(items.map(item => [item.id,item]))
        const existing = new Set(history.value.map(item => item.id))
        history.value = [...history.value.map(item => incoming.get(item.id)).filter(Boolean), ...items.filter(item => !existing.has(item.id))]
      } else history.value = items
    }
  } catch (e) { if (!silent && !disposed && account === props.account && key === selectionKey.value && current === historyVersion) historyError.value = `会话列表加载失败：${e.message}` }
  finally { historyPending--; if (current === historyVersion) historyLoading.value = false }
}
const showHistory = () => {
  navigationOpen.value = true
  void loadHistory()
}
const openHistory = () => {
  if (navigationOpen.value) navigationOpen.value = false
  else showHistory()
}
const selectHistory = item => guardAction(async () => {
  const account = props.account
  const key = selectionKey.value
  if (!await readThread(item.id) || account !== props.account) return
  saved.value.selected[key] = item.id
  mode.value = 'agent'
  if (!expanded.value) navigationOpen.value = false
})
const renameHistory = async (item, title) => {
  if (historyBusy.value || !title) return
  const account = props.account, key = selectionKey.value; historyBusy.value = true; historyError.value = ''
  try {
    await api.request(`/agent/threads/${item.id}`, {method:'PATCH',query:{account},body:{title}})
    if (disposed || account !== props.account || key !== selectionKey.value) return
    ++historyVersion; historyLoading.value = false
    history.value = history.value.map(entry => entry.id === item.id ? {...entry,title} : entry)
    if (thread.value?.id === item.id) thread.value = {...thread.value,title}
  } catch (e) { if (account === props.account && key === selectionKey.value) historyError.value = e.message }
  finally { if (account === props.account && key === selectionKey.value) historyBusy.value = false }
}
const deleteHistory = async item => {
  if (historyBusy.value) return
  const account = props.account, key = selectionKey.value; historyBusy.value = true; historyError.value = ''
  try {
    await api.request(`/agent/threads/${item.id}`, {method:'DELETE',query:{account}})
    if (disposed || account !== props.account || key !== selectionKey.value) return
    ++historyVersion; historyLoading.value = false
    history.value = history.value.filter(t => t.id !== item.id)
    for (const [key,id] of Object.entries(saved.value.selected)) if ((key.startsWith(`${account}:`) || key === `page:${account}:agent`) && id === item.id) delete saved.value.selected[key]
    if (saved.value.pinned[account] === item.id) delete saved.value.pinned[account]
    if (pendingThreadId.value === item.id) { ++version; threadLoading.value = false; pendingThreadId.value = '' }
    if (thread.value?.id === item.id) {
      ++version; threadLoading.value = false; thread.value = null; run.value = null
      if (isPage.value) { saved.value.selected[selectionKey.value] = null; saved.value.chatScopes[selectionKey.value] = null; delete saved.value.libraryScopes[selectionKey.value]; delete saved.value.sourceModes[selectionKey.value]; saved.value.newKeys[selectionKey.value] = crypto.randomUUID() }
    }
  } catch(e) { if (account === props.account && key === selectionKey.value) historyError.value = e.message }
  finally { if (account === props.account && key === selectionKey.value) historyBusy.value = false }
}
const chooseContact = choice => guardAction(async () => { draft.value = `选择会话 ${choice.username}，请继续刚才的问题`; await send() })
const onScroll = () => { const el = scrollView.value; if (el) { nearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 70; if (nearBottom.value) newContent.value = false } }
const toBottom = async () => { await nextTick(); const el = scrollView.value; if (el) el.scrollTop = el.scrollHeight; nearBottom.value = true; newContent.value = false }
const onEscape = () => { if (dialog.value) dialog.value = ''; else if (menuOpen.value) { menuOpen.value = false; menuTrigger.value?.focus() } else if (composerSettings.value) composerSettings.value = false; else if (inspectedSource.value) closeInspector(true); else if (!expanded.value && navigationOpen.value) navigationOpen.value = false; else if (expanded.value) expanded.value = false }
const connect = () => {
  events?.()
  streamConnected.value = false
  const account=props.account
  if (account && api.agentEvents) events=api.agentEvents(account, event=>{
    if (disposed || account!==props.account) return
    if (event?.run_id === run.value?.id && event.version != null && event.version < run.value.version) return
    const versionGap = event?.run_id === run.value?.id && event.version != null && event.version > (run.value.version || 0) + 1
    const newerRun = event?.thread_id === thread.value?.id && event.run_id && event.run_id !== run.value?.id
    streamConnected.value = true
    run.value = mergeRunEvent(run.value, event)
    rememberStatus(event?.run_id, event?.run_id === run.value?.id ? run.value.status : event?.status)
    // 正常事件直接合并；只有版本跳跃或同一对话出现另一运行时才读取一次权威快照。
    if (versionGap || newerRun) void refresh()
  }, ({reconnected = true} = {}) => {
    if (disposed || account !== props.account) return
    streamConnected.value = true
    streamWarning.value = ''
    cancelRefreshRetry()
    // 首次页面加载已有独立快照；只有自动重连才补一次断线期间的最终状态。
    if (reconnected) { void refresh(); void loadHistory({silent:true}) }
  }, (status = {}) => {
    if (disposed || account !== props.account) return
    streamConnected.value = false
    if (status?.permanent) {
      streamWarning.value = '实时连接多次失败，已转入后台轮询同步。后台任务不受影响，请勿重复提交。'
    } else if ((status?.attempt || 0) > 2) {
      streamWarning.value = `实时连接已中断，正在尝试第 ${status.attempt} 次重连... 后台任务不受影响。`
    } else {
      streamWarning.value = '实时进度连接已中断，正在自动重连。后台任务不受影响，请勿重复提交。'
    }
    if (running.value) void refresh()
  })
}
// 分别监听账号和聊天标识，避免新消息替换联系人对象时误触发切换、收起大视图。
watch([() => props.account, selectionKey], ([account], [oldAccount]) => {
  rememberView(oldAccount); sending.value = false; stopping.value = false; ++version; ++historyVersion; closeInspector(); cancelRefreshRetry()
  thread.value = null; run.value = null; pastRuns.value = {}; threadLoading.value = false; pendingThreadId.value = ''; error.value = ''; streamWarning.value = ''; syncWarning.value = ''; historyError.value = ''; historyBusy.value = false; newContent.value = false; streamConnected.value = false
  runStatuses.value = {}; history.value = []; directory.value = []; expanded.value = false
  if (isPage.value) mode.value = 'agent'
  connect(); void loadHistory(); void guardAction(loadSelection)
})
watch(() => props.libraryTarget, target => {
  if (!isPage.value || !target || target.account !== props.account) return
  startSource('library', target)
}, { immediate: true, flush: 'post' })
// 新一轮开始后保留上一轮过程，避免退回加载占位。
watch(run, (next, previous) => {
  if (previous?.id && previous.id !== next?.id && previous.thread_id === thread.value?.id && (!previous.account || previous.account === props.account)) pastRuns.value[previous.id] = previous
}, { flush: 'sync' })
watch(() => [props.account, thread.value?.id, run.value?.id, ...turns.value.map(turn => `${turn.id}:${turn.detail ? 'ready' : pastRunLoads.value[pastRunKey(turn.id)] || ''}`)], () => {
  for (const turn of turns.value) {
    if (!turn.detail && !pastRunLoads.value[pastRunKey(turn.id)]) void loadPastRun(turn.id)
  }
})
watch(() => [props.account, run.value?.id, run.value?.status], () => { if (run.value && (!run.value.account || run.value.account === props.account)) rememberStatus(run.value.id, run.value.status) })
watch(() => props.focusTaskId, id => { if (id && !isPage.value) mode.value = 'tools' })
watch(() => settings.open?.value, (open, previous) => { if (previous && !open) void loadProfiles() })
watch(expanded, value => { closeInspector(); finishResize(); navigationOpen.value = value; if (value) void loadHistory(); emit('expanded', value) })
// 切换对话或调整授权范围后，不保留上一范围的出处内容。
watch([() => thread.value?.id, () => thread.value?.scope?.join('\0'), () => thread.value?.chat_scope?.join('\0')], () => closeInspector())
watch(mode, () => { closeInspector(); composerSettings.value = false; nextTick(resizeDraft) })
watch(draft, () => nextTick(resizeDraft))
watch(() => [run.value?.answer, run.value?.stage, run.value?.timeline, thread.value?.messages?.length], () => { if (!nearBottom.value) newContent.value = true })
onMounted(() => {
  document.addEventListener('pointerdown', onOutside)
  panelObserver = new ResizeObserver(entries => {
    canInspect.value = entries[0].contentRect.width >= 860
    if (!canInspect.value) closeInspector()
    resizeDraft()
  })
  panelObserver.observe(panelView.value)
  resizeDraft()
  connect(); void guardAction(loadSelection); if (isPage.value) void loadHistory(); void loadProfiles(); timer = setInterval(() => {
    now.value = Date.now()
    // 当 SSE 断开且任务仍在执行时，启动 1s 保底 HTTP 轮询同步权威快照。
    // 遵从 RULE[user_global]：显式记录 Warn 日志与诊断事件，绝不静默回退。
    if (running.value && !streamConnected.value && !refreshRetryTimer) {
      api.diagnostic?.('sse.fallback_polling', { run_id: run.value?.id, component: 'agent' })
      void refresh()
    }
    if (!streamConnected.value && !historyPending && Date.now() - lastHistorySync >= 5000 && (navigationOpen.value || runningThreadIds.value.length)) void loadHistory({silent:true})
  }, 1000) })
onUnmounted(() => { rememberView(); panelObserver?.disconnect(); document.removeEventListener('pointerdown', onOutside); closeInspector(); disposed = true; ++version; clearInterval(timer); cancelRefreshRetry(); events?.(); emit('expanded', false) })
</script>
