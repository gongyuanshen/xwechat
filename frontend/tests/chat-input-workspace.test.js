import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref, computed, reactive, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import MessageInputWorkspace from '../components/chat/MessageInputWorkspace.vue'
import ConversationPane from '../components/chat/ConversationPane.vue'
import { useApi } from '../composables/useApi'
import { agentModelSelection } from '../lib/agent-model-selection'

vi.mock('../components/chat/AutoReplyControl.vue', () => ({ default: { template: '<span />' } }))

const createDeferred = () => {
  let resolve, reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('MessageInputWorkspace & useApi Chat Suite', () => {
  let mockApi
  let aiView

  beforeEach(() => {
    aiView = ref({ selected: {}, drafts: {}, pinned: {} })
    vi.stubGlobal('useState', () => aiView)
    mockApi = {
      sendChatMessage: vi.fn().mockResolvedValue({
        success: true,
        session: '测试联系人',
        content_length: 5,
        duration_ms: 120,
        timestamp: Date.now()
      }),
      getAiSuggestedReply: vi.fn().mockResolvedValue({
        suggestion: '这是AI生成的回复草稿',
        context_count: 5,
        model_used: 'gpt-4o'
      }),
      getChatSendStatus: vi.fn().mockResolvedValue({
        running: true,
        pid: 1234,
        window_found: true,
        hwnd: 5678,
        locked: false
      })
    }
    vi.stubGlobal('ref', ref)
    vi.stubGlobal('computed', computed)
    vi.stubGlobal('useApi', () => mockApi)
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('1. 当未选择账号 (selectedAccount 为 null) 时，输入框与所有按钮均禁用且展示引导提示', async () => {
    const state = reactive({
      selectedAccount: null,
      selectedContact: { username: 'wxid_test', name: '测试好友' },
      messages: []
    })

    const wrapper = mount(MessageInputWorkspace, {
      props: { state, api: mockApi }
    })

    const textarea = wrapper.find('textarea')
    expect(textarea.attributes('disabled')).toBeDefined()
    expect(textarea.attributes('placeholder')).toBe('未选择解密账号，请先在左侧选择账号')

    const aiBtn = wrapper.find('.chat-input-btn-ai')
    expect(aiBtn.attributes('disabled')).toBeDefined()

    const clearBtn = wrapper.find('.chat-input-btn-clear')
    expect(clearBtn.attributes('disabled')).toBeDefined()

    const sendBtn = wrapper.find('.chat-input-btn-send')
    expect(sendBtn.attributes('disabled')).toBeDefined()
  })

  it('2. 当已选账号但未选具体会话 (selectedContact 为 null) 时，输入框与按钮禁用且展示选会话提示', async () => {
    const state = reactive({
      selectedAccount: 'wx_user_001',
      selectedContact: null,
      messages: []
    })

    const wrapper = mount(MessageInputWorkspace, {
      props: { state, api: mockApi }
    })

    const textarea = wrapper.find('textarea')
    expect(textarea.attributes('disabled')).toBeDefined()
    expect(textarea.attributes('placeholder')).toBe('未选定会话，请从左侧选择联系人或群聊')

    const aiBtn = wrapper.find('.chat-input-btn-ai')
    expect(aiBtn.attributes('disabled')).toBeDefined()

    const sendBtn = wrapper.find('.chat-input-btn-send')
    expect(sendBtn.attributes('disabled')).toBeDefined()
  })

  it('3. 账号与会话均选定时处于就绪状态，AI 按钮可用，发送按钮在内容为空时禁用', async () => {
    const state = reactive({
      selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '张三' },
      messages: []
    })

    const wrapper = mount(MessageInputWorkspace, {
      props: { state, api: mockApi }
    })

    const textarea = wrapper.find('textarea')
    expect(textarea.attributes('disabled')).toBeUndefined()
    expect(textarea.attributes('placeholder')).toContain('Enter 发送')

    const aiBtn = wrapper.find('.chat-input-btn-ai')
    expect(aiBtn.attributes('disabled')).toBeUndefined()

    const sendBtn = wrapper.find('.chat-input-btn-send')
    expect(sendBtn.attributes('disabled')).toBeDefined()
  })

  it('4. 输入文本后发送按钮激活，触发输入自动计算文本框高度', async () => {
    const state = reactive({
      selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '张三' },
      messages: []
    })

    const wrapper = mount(MessageInputWorkspace, {
      props: { state, api: mockApi }
    })

    const textarea = wrapper.find('textarea')
    await textarea.setValue('你好，这是一条测试消息')
    await textarea.trigger('input')
    await flushPromises()

    const sendBtn = wrapper.find('.chat-input-btn-send')
    expect(sendBtn.attributes('disabled')).toBeUndefined()
    expect(wrapper.vm.draftText).toBe('你好，这是一条测试消息')
  })

  it('5. 敲击 Enter 键触发消息发送，调用 API 并在成功后清空草稿', async () => {
    const state = reactive({
      selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '张三' },
      messages: [],
      refreshSelectedMessages: vi.fn()
    })

    const wrapper = mount(MessageInputWorkspace, {
      props: { state, api: mockApi }
    })

    const textarea = wrapper.find('textarea')
    await textarea.setValue('测试Enter发送')

    await textarea.trigger('keydown.enter.exact')
    await flushPromises()

    expect(mockApi.sendChatMessage).toHaveBeenCalledTimes(1)
    expect(mockApi.sendChatMessage).toHaveBeenCalledWith({
      account: 'wx_user_001',
      username: 'wxid_test',
      display_name: '张三',
      content: '测试Enter发送'
    })

    expect(wrapper.vm.draftText).toBe('')
    expect(state.refreshSelectedMessages).toHaveBeenCalledTimes(1)
  })

  it('6. Shift + Enter 插入换行符，绝不触发消息发送', async () => {
    const state = reactive({
      selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '张三' },
      messages: []
    })

    const wrapper = mount(MessageInputWorkspace, {
      props: { state, api: mockApi }
    })

    const textarea = wrapper.find('textarea')
    await textarea.setValue('第一行内容')

    await textarea.trigger('keydown', { key: 'Enter', shiftKey: true })
    await flushPromises()

    expect(mockApi.sendChatMessage).not.toHaveBeenCalled()
    expect(wrapper.vm.draftText).toBe('第一行内容')
  })

  it('7. 输入法 IME 拼音选词过程中按下 Enter 绝不触发发送', async () => {
    const state = reactive({
      selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '张三' },
      messages: []
    })

    const wrapper = mount(MessageInputWorkspace, {
      props: { state, api: mockApi }
    })

    const textarea = wrapper.find('textarea')
    await textarea.setValue('nihao')

    // 触发拼音输入合成开始
    await textarea.trigger('compositionstart')

    // 拼音候选词中回车
    await textarea.trigger('keydown.enter.exact', { isComposing: true })
    await flushPromises()
    expect(mockApi.sendChatMessage).not.toHaveBeenCalled()

    // 拼音合成结束，选定中文
    await textarea.setValue('你好')
    await textarea.trigger('compositionend')

    // 正式回车发送
    await textarea.trigger('keydown.enter.exact', { isComposing: false })
    await flushPromises()
    expect(mockApi.sendChatMessage).toHaveBeenCalledTimes(1)
    expect(mockApi.sendChatMessage).toHaveBeenCalledWith(expect.objectContaining({
      content: '你好'
    }))
  })

  it('8. 防连续重复提交 (并发锁)：请求在途期间禁用发送按钮并显示 Loading，重复点击无效', async () => {
    const deferred = createDeferred()
    mockApi.sendChatMessage.mockReturnValueOnce(deferred.promise)

    const state = reactive({
      selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '张三' },
      messages: []
    })

    const wrapper = mount(MessageInputWorkspace, {
      props: { state, api: mockApi }
    })

    const textarea = wrapper.find('textarea')
    await textarea.setValue('并发防重测试')

    const sendBtn = wrapper.find('.chat-input-btn-send')

    // 第一次点击
    await sendBtn.trigger('click')
    expect(mockApi.sendChatMessage).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.isSending).toBe(true)

    // 此时按钮应处于 loading 状态且 textarea 禁用
    expect(sendBtn.attributes('aria-busy')).toBe('true')
    expect(sendBtn.attributes('disabled')).toBeDefined()
    expect(textarea.attributes('disabled')).toBeDefined()

    // 立即进行第二次和第三次连续快速点击
    await sendBtn.trigger('click')
    await textarea.trigger('keydown.enter.exact')
    expect(mockApi.sendChatMessage).toHaveBeenCalledTimes(1)

    // 请求结束
    deferred.resolve({ success: true, session: '张三', content_length: 6, duration_ms: 50, timestamp: Date.now() })
    await flushPromises()

    expect(wrapper.vm.isSending).toBe(false)
    expect(wrapper.vm.draftText).toBe('')
    expect(textarea.attributes('disabled')).toBeUndefined()
  })

  it('9. 点击 AI 建议按钮调用后端并自动填入草稿箱供二次审阅', async () => {
    const state = reactive({
      selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '李四' },
      messages: [{ sender: '李四', text: '下午两点开会如何？' }]
    })

    mockApi.getAiSuggestedReply.mockResolvedValueOnce({
      suggestion: '好的，下午两点准时参加。',
      context_count: 1,
      model_used: 'chat-model'
    })

    const wrapper = mount(MessageInputWorkspace, {
      props: { state, api: mockApi }
    })

    const aiBtn = wrapper.find('.chat-input-btn-ai')
    await aiBtn.trigger('click')
    await flushPromises()

    expect(mockApi.getAiSuggestedReply).toHaveBeenCalledTimes(1)
    expect(mockApi.getAiSuggestedReply).toHaveBeenCalledWith({
      account: 'wx_user_001',
      username: 'wxid_test',
      display_name: '李四',
      count: 10
    })

    expect(wrapper.vm.draftText).toBe('好的，下午两点准时参加。')
    const textarea = wrapper.find('textarea')
    expect(textarea.element.value).toBe('好的，下午两点准时参加。')
  })

  it.each(['pending', 'failed'])('AI 建议使用顶部当前模型，即使保存状态为 %s', async status => {
    const save = createDeferred()
    const request = vi.fn(() => save.promise)
    const selection = agentModelSelection(aiView.value, request)
    const oldChoice = { profile_id: 'old-service', model_id: 'old-model' }
    selection.loaded({ profiles: [{ id: 'old-service' }], selected_model: oldChoice }, selection.beginLoad())
    const choice = { profile_id: 'new-service', model_id: 'new-model', reasoning_effort: 'high', thinking_budget: null }
    const saving = selection.choose(choice)
    await flushPromises()
    if (status === 'failed') {
      save.reject(new Error('save failed'))
      await saving
      expect(selection.state.notice).toContain('未保存')
    } else expect(selection.state.pending).toBe(1)
    const state = reactive({ selectedAccount: 'test-account', selectedContact: { username: 'friend', name: '好友' } })
    const wrapper = mount(MessageInputWorkspace, { props: { state, api: mockApi } })
    await wrapper.find('.chat-input-btn-ai').trigger('click')
    await flushPromises()
    expect(mockApi.getAiSuggestedReply).toHaveBeenCalledWith(expect.objectContaining({
      selected_model: { profile_id: 'new-service', model_id: 'new-model', reasoning_effort: 'high' },
    }))
    if (status === 'pending') { save.resolve(choice); await saving }
    wrapper.unmount()
  })

  it.each([undefined, {}, { success: false }])('发送返回未确认结果时保留草稿并显示真实状态：%j', async (receipt) => {
    mockApi.sendChatMessage.mockResolvedValueOnce(receipt)
    const state = reactive({ selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '张三' }, messages: [], refreshSelectedMessages: vi.fn() })
    const wrapper = mount(MessageInputWorkspace, { props: { state, api: mockApi } })
    await wrapper.find('textarea').setValue('请保留这条草稿')
    await wrapper.vm.handleSend()
    expect(wrapper.vm.draftText).toBe('请保留这条草稿')
    expect(wrapper.vm.errorInfo.code).toBe('WECHAT_SEND_UNCONFIRMED')
    expect(state.refreshSelectedMessages).not.toHaveBeenCalled()
    expect(mockApi.sendChatMessage).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it.each([undefined, {}, { suggestion: '' }, { suggestion: '  ' }])('AI 建议空响应保留草稿并显示失败：%j', async (response) => {
    mockApi.getAiSuggestedReply.mockResolvedValueOnce(response)
    const state = reactive({ selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '张三' }, messages: [] })
    const wrapper = mount(MessageInputWorkspace, { props: { state, api: mockApi } })
    await wrapper.find('textarea').setValue('请保留这条草稿')
    await wrapper.vm.handleAiSuggest()
    expect(wrapper.vm.draftText).toBe('请保留这条草稿')
    expect(wrapper.vm.errorInfo.message).toBe('AI 建议未返回有效的回复草稿')
    expect(mockApi.sendChatMessage).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('10. Debug-First / Let-It-Fail：发送异常时严格保留草稿绝不丢失，并展示真实后端错误码和提示', async () => {
    mockApi.sendChatMessage.mockRejectedValueOnce({
      code: 'WECHAT_NOT_RUNNING',
      message: '微信客户端未运行，请先登录并启动微信'
    })

    const state = reactive({
      selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '王五' },
      messages: []
    })

    const wrapper = mount(MessageInputWorkspace, {
      props: { state, api: mockApi }
    })

    const textarea = wrapper.find('textarea')
    await textarea.setValue('这是一条非常重要的紧急草稿！')

    const sendBtn = wrapper.find('.chat-input-btn-send')
    await sendBtn.trigger('click')
    await flushPromises()

    // 核心断言：Let-it-Fail 规范，失败绝对不清除草稿！
    expect(wrapper.vm.draftText).toBe('这是一条非常重要的紧急草稿！')
    expect(textarea.element.value).toBe('这是一条非常重要的紧急草稿！')

    // 错误横幅显示 verbatim code 和 message
    const errorBanner = wrapper.find('.chat-input-error-banner')
    expect(errorBanner.exists()).toBe(true)
    expect(errorBanner.text()).toContain('WECHAT_NOT_RUNNING')
    expect(errorBanner.text()).toContain('微信客户端未运行，请先登录并启动微信')

    // 点击错误条右上角关闭
    const dismissBtn = wrapper.find('.chat-input-error-dismiss')
    await dismissBtn.trigger('click')
    await flushPromises()
    expect(wrapper.find('.chat-input-error-banner').exists()).toBe(false)
    expect(wrapper.vm.draftText).toBe('这是一条非常重要的紧急草稿！')

    // 点击清空按钮清除草稿与错误
    const clearBtn = wrapper.find('.chat-input-btn-clear')
    await clearBtn.trigger('click')
    await flushPromises()
    expect(wrapper.vm.draftText).toBe('')
  })

  it('11. 顶部拖拽调整高度，范围约束在 100-400px 并持久化到 localStorage', async () => {
    const state = reactive({
      selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '张三' },
      messages: []
    })

    const wrapper = mount(MessageInputWorkspace, {
      props: { state, api: mockApi }
    })

    expect(wrapper.vm.inputHeight).toBe(150)

    const resizer = wrapper.find('.chat-input-resizer')
    expect(resizer.exists()).toBe(true)

    // 模拟拖拽放大：pointerdown 位于 clientY=500，pointermove 到 clientY=450 (向上拖动50px)
    await resizer.trigger('pointerdown', { clientY: 500 })
    const moveEvent = new PointerEvent('pointermove', { clientY: 450 })
    window.dispatchEvent(moveEvent)
    expect(wrapper.vm.inputHeight).toBe(200)

    // 向上拖动超过最大值 400px
    const moveMaxEvent = new PointerEvent('pointermove', { clientY: 100 })
    window.dispatchEvent(moveMaxEvent)
    expect(wrapper.vm.inputHeight).toBe(400)

    // 向下拖动低于最小值 100px
    const moveMinEvent = new PointerEvent('pointermove', { clientY: 700 })
    window.dispatchEvent(moveMinEvent)
    expect(wrapper.vm.inputHeight).toBe(100)

    // 放开指针完成拖拽并持久化
    const upEvent = new PointerEvent('pointerup')
    window.dispatchEvent(upEvent)
    expect(localStorage.getItem('ui.chat.input_height')).toBe('100')

    // 双击恢复默认 150px
    await resizer.trigger('dblclick')
    expect(wrapper.vm.inputHeight).toBe(150)
    expect(localStorage.getItem('ui.chat.input_height')).toBe('150')
  })

  it('12. useApi 组合式函数正确定义 sendChatMessage 与 getAiSuggestedReply 接口并正确调用', async () => {
    setActivePinia(createPinia())
    vi.stubGlobal('useApiBase', () => 'http://127.0.0.1:10392/api')
    const mockFetch = vi.fn().mockResolvedValue({ success: true })
    vi.stubGlobal('$fetch', mockFetch)

    const apiInstance = useApi()
    expect(typeof apiInstance.sendChatMessage).toBe('function')
    expect(typeof apiInstance.getAiSuggestedReply).toBe('function')
    expect(typeof apiInstance.getChatSendStatus).toBe('function')

    await apiInstance.sendChatMessage({
      account: 'acc1',
      username: 'user1',
      display_name: 'Name1',
      content: 'Hello'
    })
    expect(mockFetch).toHaveBeenCalledWith('/chat/send', expect.objectContaining({
      baseURL: 'http://127.0.0.1:10392/api',
      method: 'POST',
      body: {
        account: 'acc1',
        username: 'user1',
        display_name: 'Name1',
        content: 'Hello'
      }
    }))

    await apiInstance.getAiSuggestedReply({
      account: 'acc1',
      username: 'user1',
      display_name: 'Name1',
      count: 10,
      selected_model: { profile_id: 'service', model_id: 'model', thinking_mode: 'disabled' }
    })
    expect(mockFetch).toHaveBeenCalledWith('/chat/suggest_reply', expect.objectContaining({
      baseURL: 'http://127.0.0.1:10392/api',
      method: 'POST',
      body: {
        account: 'acc1',
        username: 'user1',
        display_name: 'Name1',
        count: 10,
        selected_model: { profile_id: 'service', model_id: 'model', thinking_mode: 'disabled' }
      }
    }))
  })

  it('13. ConversationPane 挂载后包含 MessageInputWorkspace 并透传会话状态', async () => {
    const state = {
      selectedAccount: 'wx_user_001',
      selectedContact: { username: 'wxid_test', name: '会话对象' },
      messages: [],
      searchContext: ref({ active: false }),
      messageTypeFilterOptions: [],
      privacyMode: false,
      aiSidebarOpen: false,
      isLoadingMessages: ref(false),
      isRefreshingMessages: ref(false),
      isJumpingToFirst: false,
      isExportCreating: false,
      voiceSidebarOpen: false,
      resourceSidebarOpen: false,
      messageSearchOpen: false,
      timeSidebarOpen: false,
      messageTypeFilter: '',
      showJumpToBottom: ref(false),
      groupAnnouncement: '',
      groupAnnouncementOpen: false,
      openGroupAnnouncement: vi.fn(),
      closeGroupAnnouncement: vi.fn(),
      toggleAiSidebar: vi.fn(),
      jumpToConversationFirst: vi.fn(),
      refreshSelectedMessages: vi.fn(),
      refreshChatFromWechat: vi.fn(),
      openExportModal: vi.fn(),
      toggleVoiceSidebar: vi.fn(),
      toggleResourceSidebar: vi.fn(),
      toggleMessageSearch: vi.fn(),
      toggleTimeSidebar: vi.fn(),
      scrollToBottom: vi.fn(),
      exitSearchContext: vi.fn()
    }

    const wrapper = mount(ConversationPane, {
      props: { state },
      global: {
        stubs: {
          MessageList: true,
          GuideDialog: true
        }
      }
    })

    expect(wrapper.findComponent(MessageInputWorkspace).exists()).toBe(true)
    const textarea = wrapper.find('.chat-input-textarea')
    expect(textarea.exists()).toBe(true)
    expect(textarea.attributes('placeholder')).toContain('Enter 发送')
    const refresh = wrapper.get('[data-testid="chat-refresh"]')
    await refresh.trigger('click')
    expect(state.refreshChatFromWechat).toHaveBeenCalledOnce()
    expect(state.refreshSelectedMessages).not.toHaveBeenCalled()

    state.isRefreshingMessages.value = true
    await nextTick()
    expect(refresh.attributes('disabled')).toBeDefined()
    expect(refresh.attributes('aria-busy')).toBe('true')
    expect(refresh.get('svg').classes()).toContain('motion-safe:animate-spin')
    await refresh.trigger('click')
    expect(state.refreshChatFromWechat).toHaveBeenCalledOnce()

    state.isRefreshingMessages.value = false
    state.isLoadingMessages.value = true
    await nextTick()
    expect(refresh.attributes('disabled')).toBeUndefined()
    expect(refresh.attributes('aria-busy')).toBe('false')
    expect(refresh.get('svg').classes()).not.toContain('motion-safe:animate-spin')
    await refresh.trigger('click')
    expect(state.refreshChatFromWechat).toHaveBeenCalledTimes(2)

    expect(wrapper.find('.jump-to-bottom-btn').exists()).toBe(false)
    state.searchContext.value = { active: true, kind: 'date' }
    await nextTick()
    expect(wrapper.find('.chat-context-banner').exists()).toBe(false)
    await wrapper.get('.jump-to-bottom-btn').trigger('click')
    expect(state.refreshSelectedMessages).toHaveBeenCalledOnce()
    expect(state.scrollToBottom).not.toHaveBeenCalled()

    state.searchContext.value = { active: false }
    await nextTick()
    expect(wrapper.find('.jump-to-bottom-btn').exists()).toBe(false)
    state.showJumpToBottom.value = true
    await nextTick()
    await wrapper.get('.jump-to-bottom-btn').trigger('click')
    expect(state.scrollToBottom).toHaveBeenCalledOnce()
    expect(state.refreshSelectedMessages).toHaveBeenCalledOnce()
    wrapper.unmount()
  })
})
