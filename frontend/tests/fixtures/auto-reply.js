import { createApp, computed, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useChatAccountsStore } from '../../stores/chatAccounts'
import Preview from './AutoReplyPreview.vue'
import '../../assets/css/tailwind.css'
import '../../assets/css/chat.css'
import '../../assets/css/ai.css'

const account = '演示数据账号'
const states = new Map()
const focusTarget = ref('auto-reply')
Object.assign(globalThis, { ref, computed, process: { client: false }, useApiBase: () => '/unused',
  useState: (key, init) => { if (!states.has(key)) states.set(key, ref(init())); return states.get(key) },
  useSettingsDialog: () => ({ focusTarget, openDialog: target => { focusTarget.value = target } }),
})
const configuration = { account, version: 3, profile_id: 'fixture-local', model_id: 'fixture-text', system_prompt: '用一两句话回复，保持自然。没有把握的信息请明确说明。',
  sessions: [{ session_id: 'friend', target_name: '林悦', enabled: true, group_trigger: 'mentions' }, { session_id: 'group@chatroom', target_name: '产品测试群', enabled: true, group_trigger: 'mentions' }] }
if (new URLSearchParams(location.search).has('unselected')) {
  configuration.profile_id = ''; configuration.model_id = ''
}
const status = { account, state: 'off', run_id: '', error: null, sessions: [{ session_id: 'friend', status: 'off', error: null }, { session_id: 'group@chatroom', status: 'needs_review', error: { code: 'WECHAT_SEND_UNCONFIRMED', detail: '客户端确认超时，请核对实际消息。' } }] }
const items = [{ id: 'fixture-attempt', account, session_id: 'group@chatroom', target_name: '产品测试群', draft: '收到，明天会前我会把材料准备好。', status: 'needs_review', model: { profile_id: 'fixture-local', model_id: 'fixture-text' }, messages: [{ content: '明天会前请准备材料。' }], created_at: 1791511200, submission_count: 1, evidence: null, error: { code: 'WECHAT_SEND_UNCONFIRMED', detail: '已进入提交阶段，但未取得明确客户端证据。' } }]
if (new URLSearchParams(location.search).has('running')) {
  status.state = 'running'
  status.sessions = configuration.sessions.map(session => ({ session_id: session.session_id, status: 'running', error: null }))
  items.length = 0
}
if (new URLSearchParams(location.search).has('history')) {
  for (let index = 1; index <= 5; index++) items.push({ id: `fixture-completed-${index}`, account, session_id: 'friend', target_name: `演示联系人 ${index}`, draft: `这是第 ${index} 条虚构回复记录。`, status: 'completed', messages: [], created_at: 1791511200 - index, submission_count: 1, submission_phase: 'client_displayed', evidence: { status: 'client_displayed' } })
}
const requests = []
const conversations = [{ username: 'friend', name: '林悦' }, { username: 'group@chatroom', name: '产品测试群' }, { username: 'other', name: '周宁' }, { username: 'new@chatroom', name: '项目协作群' }]
if (new URLSearchParams(location.search).has('many')) {
  for (let i = 1; i <= 45; i++) conversations.push({ username: `fixture${i}@chatroom`, name: `演示群聊 ${i}` })
  for (let i = 1; i <= 8; i++) conversations.push({ username: `fixture-peer${i}`, name: `演示联系人 ${i}` })
}
globalThis.useAiApi = () => ({ request: async (path, options = {}) => {
  requests.push({ path, method: options.method || 'GET', retry: options.retry })
  // Only fixtures can mutate here; all real transports are replaced at the boundary.
  if (path === '/settings') return { profiles: [{ id: 'fixture-local', name: '本地模型服务', provider: 'ollama', model: 'fixture-text', model_metadata: { name: '示例文字模型', reasoning_controls: { efforts: [], toggle: false } } }], presets: [], defaults: {} }
  if (path === '/usage') return { calls: 0 }
  if (path.startsWith('/usage/records')) return { items: [], total: 0 }
  if (path.includes('/model-capabilities')) return { metadata: { reasoning_controls: { efforts: [], toggle: false } } }
  if (path.startsWith('/conversations?')) return structuredClone(conversations)
  if (path.startsWith('/auto-reply/config?')) return structuredClone(configuration)
  if (path.startsWith('/auto-reply/status?')) return structuredClone(status)
  if (path.startsWith('/auto-reply/history?') && options.method === 'DELETE' && new URLSearchParams(location.search).has('history')) {
    const query = new URL(path, 'http://fixture').searchParams
    if (query.get('account') !== account || [...query.keys()].length !== 1 || options.retry !== 0) throw new Error('Fixture bulk deletion requires only the current account and no retry')
    let deleted_count = 0
    for (let index = items.length - 1; index >= 0; index--) {
      if (items[index].account === account && ['completed', 'cancelled', 'failed', 'interrupted'].includes(items[index].status)) {
        items.splice(index, 1); deleted_count++
      }
    }
    return { account, deleted_count }
  }
  if (path.startsWith('/auto-reply/history?') && (!options.method || options.method === 'GET')) {
    const query = new URL(path, 'http://fixture').searchParams, page = Number(query.get('page')), size = Number(query.get('page_size'))
    const filtered = items.filter(item => item.account === query.get('account') && (!query.get('session_id') || item.session_id === query.get('session_id')) && (!query.get('status') || item.status === query.get('status')))
    return { items: structuredClone(filtered.slice((page - 1) * size, page * size)), total: filtered.length, page, page_size: size }
  }
  if (path.startsWith('/auto-reply/history/') && options.method === 'DELETE' && new URLSearchParams(location.search).has('history')) {
    const url = new URL(path, 'http://fixture'), id = decodeURIComponent(url.pathname.slice('/auto-reply/history/'.length))
    const index = items.findIndex(item => item.id === id && item.account === url.searchParams.get('account') && item.status === 'completed')
    if (index < 0) throw new Error('Fixture record cannot be deleted')
    items.splice(index, 1)
    return { id, account, deleted: true }
  }
  if (path === '/auto-reply/windows') return { windows: [{ pid: 1234, process_created_at: 100, hwnd: 5678 }] }
  throw new Error(`Fixture does not authorize ${options.method || 'GET'} ${path}`)
} })
window.autoReplyAcceptance = { requests, synthetic_only: true, model_calls: 0, wechat_sends: 0 }
const pinia = createPinia(); setActivePinia(pinia)
const accounts = useChatAccountsStore(); accounts.accounts = [account]; accounts.accountInfos = [{ account, displayName: '演示数据账号' }]; accounts.selectedAccount = account
createApp(Preview).use(pinia).mount('#app')
