import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ChatAgentPanel from '../components/chat/ChatAgentPanel.vue'
import AgentAnswer from '../components/chat/AgentAnswer.vue'
import AgentSourceInspector from '../components/chat/AgentSourceInspector.vue'
import AgentRun from '../components/chat/AgentRun.vue'
import { usePrivacyStore } from '../stores/privacy'

const fixture = vi.hoisted(() => ({ library: null }))
vi.mock('~/composables/useLibraryApi', () => ({ useLibraryApi: () => fixture.library }))
const scope = { folder_id: 'folder', folder_name: '投稿专题', version_id: 'v2', version_name: '核对稿', version_number: 2, saved_at: 100, item_count: 2 }
let wrapper, request, threads, state, sequence
beforeEach(() => {
  threads = {}; sequence = 0; state = ref({ selected: {}, drafts: {}, pinned: {} })
  fixture.library = {
    listFolders: vi.fn(async () => [{ id: 'folder', name: '投稿专题' }, { id: 'empty', name: '未保存专题' }]),
    getDossier: vi.fn(async (_, folder) => ({ folder_name: folder === 'folder' ? '投稿专题' : '未保存专题', versions: folder === 'folder' ? [{ id: 'v2', number: 2, name: '核对稿', created_at: 100, item_count: 2 }, { id: 'v1', number: 1, name: '初稿', created_at: 50, item_count: 1 }] : [] })),
  }
  request = vi.fn(async (path, options = {}) => {
    if (path === '/settings') return { profiles: [{ id: 'local', name: '本地', model: 'model' }], selected_model: { profile_id: 'local', model_id: 'model' } }
    if (path.includes('/model-capabilities')) return { metadata: {} }
    if (path === '/conversations') return [{ username: 'friend', name: '朋友' }, { username: 'team', name: '项目群' }]
    if (path === '/agent/threads') {
      if (options.method === 'POST') {
        const id = `t${++sequence}`
        return threads[id] = { id, ...options.body, library_scope: options.body.library_scope ? { ...scope, ...options.body.library_scope } : null, messages: [], title: '新的对话' }
      }
      return Object.values(threads).filter(thread => thread.account === options.query.account)
    }
    if (path.endsWith('/messages')) {
      const thread = threads[path.split('/')[3]], id = `r${++sequence}`
      thread.messages.push({ role: 'user', text: options.body.text, run_id: id })
      return { id, thread_id: thread.id, account: thread.account, status: 'completed', answer: '已读取保存资料', library_scope: thread.library_scope, citations: [], timeline: [] }
    }
    if (path.startsWith('/agent/threads/')) {
      const id = path.split('/')[3]
      if (options.method === 'DELETE') { delete threads[id]; return { status: 'success' } }
      return threads[id]
    }
    throw new Error(`Unexpected request: ${path}`)
  })
  vi.stubGlobal('useState', () => state)
  vi.stubGlobal('useSettingsDialog', () => ({ openDialog: vi.fn() }))
  vi.stubGlobal('useAiApi', () => ({ request, agentEvents: () => () => {} }))
})
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.unstubAllGlobals() })
const open = async props => { wrapper = mount(ChatAgentPanel, { props: { account: 'a', presentation: 'page', ...props }, global: { stubs: { AiSidebar: true } } }); await flushPromises(); return wrapper }
const chooseTopic = async () => {
  await wrapper.find('[aria-label="资料来源"]').setValue('library')
  await wrapper.find('[aria-label="选择专题版本"]').trigger('click'); await flushPromises()
  await wrapper.find('[aria-label="应用专题版本"]').trigger('click'); await flushPromises()
}
const send = async text => { await wrapper.find('textarea').setValue(text); await wrapper.find('textarea').trigger('keydown', { key: 'Enter' }); await flushPromises() }

it('creates a topic thread from an explicitly displayed saved version, then restores immutable labels from history', async () => {
  await open(); await chooseTopic(); await send('核对投稿安排')
  expect(request.mock.calls.find(([path, options]) => path === '/agent/threads' && options.method === 'POST')[1].body).toEqual({ account: 'a', username: '', origin: 'agent', library_scope: { folder_id: 'folder', version_id: 'v2' } })
  expect(wrapper.text()).toContain('投稿专题'); expect(wrapper.text()).toContain('第 2 版'); expect(wrapper.text()).toContain('核对稿')
  await wrapper.find('[aria-label="新建 AI 对话"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[aria-label="资料来源"]').element.value).toBe('chat')
  await wrapper.find('[aria-label="AI 对话历史"]').trigger('click'); await flushPromises()
  const history = wrapper.find('.agent-thread-select')
  expect(history.text()).toContain('投稿专题'); expect(history.text()).toContain('第 2 版')
  await history.trigger('click'); await flushPromises()
  expect(wrapper.find('[aria-label="资料来源"]').element.value).toBe('library')
  expect(wrapper.find('[aria-label="选择专题版本"]').text()).toContain('核对稿')
})

it('restored-only accounts require a saved selection and never request chat data', async () => {
  await open({ canReadChats: false })
  expect(wrapper.find('[aria-label="资料来源"]').element.value).toBe('library')
  expect(wrapper.find('[aria-label="资料来源"] option[value="chat"]').attributes('disabled')).toBeDefined()
  await send('尚未选择')
  expect(request.mock.calls.some(([path, options]) => path === '/agent/threads' && options.method === 'POST')).toBe(false)
  await wrapper.find('[aria-label="选择专题版本"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[aria-label="专题保存版本"]').element.value).toBe('v2')
  await wrapper.find('[aria-label="专题"]').setValue('empty'); await flushPromises()
  expect(wrapper.text()).toContain('请先在资料夹中保存版本')
  expect(wrapper.find('[aria-label="应用专题版本"]').attributes('disabled')).toBeDefined()
  expect(request.mock.calls.some(([path]) => path === '/conversations')).toBe(false)
})

it('a late version catalog cannot cross accounts and its read error is explicit', async () => {
  let release
  fixture.library.getDossier.mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  await open({ canReadChats: false }); await wrapper.find('[aria-label="选择专题版本"]').trigger('click'); await flushPromises()
  await wrapper.setProps({ account: 'b' }); await flushPromises()
  release({ folder_name: 'A 私人专题', versions: [{ id: 'private', number: 9, name: 'A 私人版本', created_at: 100 }] }); await flushPromises()
  expect(wrapper.text()).not.toContain('A 私人')
  fixture.library.getDossier.mockRejectedValueOnce(new Error('保存版本读取失败'))
  await wrapper.find('[aria-label="选择专题版本"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('保存版本读取失败')
  expect(wrapper.find('[aria-label="应用专题版本"]').attributes('disabled')).toBeDefined()
})

it('a direct-entry target replaces the previous thread and switching back to chat removes topic scope', async () => {
  await open({ libraryTarget: { account: 'a', ...scope } })
  expect(wrapper.find('[aria-label="选择专题版本"]').text()).toContain('核对稿')
  await send('直接提问')
  await wrapper.find('textarea').setValue('保留草稿')
  await wrapper.find('[aria-label="资料来源"]').setValue('chat')
  expect(wrapper.find('textarea').element.value).toBe('保留草稿')
  await send('聊天问题')
  const bodies = request.mock.calls.filter(([path, options]) => path === '/agent/threads' && options.method === 'POST').map(([, options]) => options.body)
  expect(bodies[1]).toEqual({ account: 'a', username: '', origin: 'agent', chat_scope: null })
})

it('running topic turns lock their source and changing accounts discards an unfinished version selection', async () => {
  await open({ libraryTarget: { account: 'a', ...scope } })
  const original = request.getMockImplementation()
  request.mockImplementation(async (path, options) => {
    const result = await original(path, options)
    return path.endsWith('/messages') ? { ...result, status: 'running' } : result
  })
  await send('处理材料')
  expect(wrapper.find('[aria-label="资料来源"]').attributes('disabled')).toBeDefined()
  expect(wrapper.find('[aria-label="选择专题版本"]').attributes('disabled')).toBeDefined()
  await wrapper.setProps({ account: 'b', libraryTarget: null, canReadChats: false }); await flushPromises()
  expect(wrapper.text()).not.toContain('核对稿')
  expect(wrapper.find('[aria-label="选择专题版本"]').text()).toBe('选择专题版本')
})

it('privacy mode masks scope names in the active selection and historical conversation', async () => {
  usePrivacyStore().set(true)
  await open(); await chooseTopic(); await send('提问')
  expect(wrapper.find('[aria-label="选择专题版本"] span').classes()).toContain('privacy-blur')
  expect(wrapper.find('.agent-library-notice p').classes()).toContain('privacy-blur')
  await wrapper.find('[aria-label="AI 对话历史"]').trigger('click'); await flushPromises()
  expect(wrapper.find('.agent-thread-select small span').classes()).toContain('privacy-blur')
})

const openChatHistoryAfterTopic = async () => {
  await open(); await chooseTopic(); await send('专题问题')
  threads.chatHistory = { id: 'chatHistory', account: 'a', origin: 'agent', username: '', title: '聊天历史', chat_scope: ['friend'], library_scope: null, messages: [], latest_run: '' }
  await wrapper.find('[aria-label="AI 对话历史"]').trigger('click'); await flushPromises()
  await wrapper.findAll('.agent-thread-select').find(button => button.text().includes('聊天历史')).trigger('click'); await flushPromises()
}
it('applying a chat range after topic history clears the old topic from the next request', async () => {
  await openChatHistoryAfterTopic()
  await wrapper.find('[aria-label="选择聊天范围"]').trigger('click'); await flushPromises()
  await wrapper.find('input[value="friend"]').setValue(false)
  await wrapper.find('input[value="team"]').setValue(true)
  await wrapper.find('[aria-label="应用聊天范围"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[aria-label="资料来源"]').element.value).toBe('chat')
  expect(wrapper.find('[aria-label="选择专题版本"]').exists()).toBe(false)
  await send('只查项目群')
  const body = request.mock.calls.filter(([path, options]) => path === '/agent/threads' && options.method === 'POST').at(-1)[1].body
  expect(body).toEqual({ account: 'a', username: '', origin: 'agent', chat_scope: ['team'] })
})
it('deleting a current history thread creates an unscoped draft instead of reviving an older topic', async () => {
  await openChatHistoryAfterTopic()
  if (!wrapper.find('[aria-label="管理会话：聊天历史"]').exists()) await wrapper.find('[aria-label="AI 对话历史"]').trigger('click')
  await wrapper.find('[aria-label="管理会话：聊天历史"]').trigger('click'); await flushPromises()
  await wrapper.findAll('[role="menuitem"]').find(button => button.text().includes('删除对话')).trigger('click')
  await wrapper.findAll('button').find(button => button.text() === '确认删除对话').trigger('click'); await flushPromises()
  expect(wrapper.find('[aria-label="资料来源"]').element.value).toBe('chat')
  expect(wrapper.find('[aria-label="选择专题版本"]').exists()).toBe(false)
  expect(wrapper.find('[aria-label="选择聊天范围"]').text()).toContain('所有聊天')
})

const librarySource = { kind: 'library', source: 'a'.repeat(24), title: '保存的结论', name: '保存的结论', text: '已保存的材料正文', library_scope: scope, library_item_id: 'item', material_kind: 'report', verified: false }
it.each([
  ['notes', 'message', '人工笔记'],
  ['arrangements', 'version_note', '人工安排记录'],
  ['conclusions', 'version_note', '人工结论记录'],
  ['original_report', 'report', '原始 AI 回答'],
  ['evidence:original-id', 'report', '报告保存的引用资料'],
  ['source_message', 'attachment', '附件对应的原消息正文'],
])('labels saved %s by its actual component on both citation surfaces', async (component, material_kind, label) => {
  const source = { ...librarySource, component, material_kind }
  wrapper = mount(AgentSourceInspector, { props: { source } })
  expect(wrapper.text()).toContain(label)
  if (component === 'notes') expect(wrapper.text()).not.toContain('聊天原文')
  wrapper.unmount()
  wrapper = mount(AgentAnswer, { attachTo: document.body, props: { text: `[[${source.source}]]`, citations: [source] } })
  wrapper.find('.agent-ref').element.getBoundingClientRect = () => ({ top: 100, bottom: 124, left: 100, right: 124 })
  await wrapper.find('.agent-ref').trigger('click'); await flushPromises()
  expect(wrapper.find('.agent-citation-preview').text()).toContain(label)
})
it('saved citations show material provenance without preparing or locating live chats', async () => {
  const prepare = vi.fn(), locate = vi.fn()
  wrapper = mount(AgentAnswer, { attachTo: document.body, props: { text: `结论 [[${librarySource.source}]]`, citations: [librarySource] }, global: { provide: { agentSourceNavigation: { prepare, locate } } } })
  wrapper.find('.agent-ref').element.getBoundingClientRect = () => ({ top: 100, bottom: 124, left: 100, right: 124 })
  await wrapper.find('.agent-ref').trigger('click'); await flushPromises()
  expect(wrapper.find('.agent-citation-preview').text()).toContain('保存的结论')
  expect(wrapper.find('.agent-citation-preview').text()).toContain('第 2 版')
  expect(wrapper.find('.agent-citation-preview').text()).not.toContain('未知时间')
  expect(wrapper.find('.agent-citation-locate').exists()).toBe(false)
  expect(prepare).not.toHaveBeenCalled(); expect(locate).not.toHaveBeenCalled()
  wrapper.unmount()
  wrapper = mount(AgentSourceInspector, { props: { source: librarySource, number: 1, prepare, locate } })
  await flushPromises()
  expect(wrapper.text()).toContain('已保存的材料正文'); expect(wrapper.text()).toContain('核对稿')
  expect(wrapper.find('.agent-source-context').exists()).toBe(false)
  expect(wrapper.find('.agent-source-locate').exists()).toBe(false)
  expect(prepare).not.toHaveBeenCalled()
})

it('a saved citation reads and pages its own run material, preserves preview on failure, and ignores late pages after switching sources', async () => {
  const read = vi.fn().mockRejectedValueOnce(new Error('保存材料读取失败')).mockResolvedValueOnce({ text: '第一段保存正文', offset: 0, next_offset: 6000 })
  wrapper = mount(AgentSourceInspector, { props: { source: { ...librarySource, run_id: 'old-run' }, number: 1, readMaterial: read } })
  await flushPromises()
  expect(wrapper.text()).toContain('保存材料读取失败'); expect(wrapper.text()).toContain('已保存的材料正文')
  await wrapper.find('[aria-label="重试读取保存材料"]').trigger('click'); await flushPromises()
  expect(read).toHaveBeenLastCalledWith(expect.objectContaining({ run_id: 'old-run' }), 0)
  expect(wrapper.text()).toContain('第一段保存正文')
  let release
  read.mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  await wrapper.find('[aria-label="继续读取保存材料"]').trigger('click'); await flushPromises()
  expect(read).toHaveBeenLastCalledWith(expect.objectContaining({ run_id: 'old-run' }), 6000)
  await wrapper.setProps({ source: { ...librarySource, source: 'b'.repeat(24), text: '另一个来源', run_id: 'other-run' } })
  release({ text: '旧来源的下一段', offset: 6000, next_offset: null }); await flushPromises()
  expect(wrapper.text()).not.toContain('旧来源的下一段')
})

it('historical AgentRun citations retain their own run and version when opening the material inspector', async () => {
  const inspect = vi.fn(() => true)
  wrapper = mount(AgentRun, { props: { viewState: {}, run: { id: 'historical-run', account: 'a', version: 4, status: 'completed', answer: `[[${librarySource.source}]]`, citations: [librarySource] } }, global: { provide: { agentSourceNavigation: { inspect } } } })
  await wrapper.find('.agent-ref').trigger('click'); await flushPromises()
  expect(inspect.mock.calls[0][0]).toMatchObject({ source: librarySource.source, run_id: 'historical-run', run_version: 4 })
})

it('hovering a saved citation uses only its preview and clicking reads the saved run material', async () => {
  const readMaterial = vi.fn(async () => ({ text: '已读取完整保存段落', offset: 0, next_offset: null }))
  wrapper = mount(AgentAnswer, { attachTo: document.body, props: { text: `[[${librarySource.source}]]`, citations: [{ ...librarySource, run_id: 'r' }] }, global: { provide: { agentSourceNavigation: { readMaterial } } } })
  const button = wrapper.find('.agent-ref')
  button.element.getBoundingClientRect = () => ({ top: 100, bottom: 124, left: 100, right: 124 })
  await button.trigger('pointerover'); await flushPromises()
  expect(readMaterial).not.toHaveBeenCalled()
  await button.trigger('click'); await flushPromises()
  expect(readMaterial).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ run_id: 'r' }), 0)
  expect(wrapper.text()).toContain('已读取完整保存段落')
})

it('results expose incomplete topic coverage separately from chat reading counts', async () => {
  wrapper = mount(AgentRun, { props: { viewState: {}, run: { id: 'r', status: 'completed', answer: '回答', library_scope: scope, topic_coverage: { total_items: 2, read_items: 1, read_characters: 30, total_characters: 100, complete: false, attachment_metadata_only: 1 } } } })
  expect(wrapper.text()).toContain('投稿专题'); expect(wrapper.text()).toContain('1 / 2 份')
  expect(wrapper.text()).toContain('30 / 100'); expect(wrapper.text()).toContain('尚未完整读取')
  expect(wrapper.text()).toContain('1 份附件仅含元数据')
})
