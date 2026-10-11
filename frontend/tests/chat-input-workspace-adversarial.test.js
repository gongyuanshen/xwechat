import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref, computed, reactive, nextTick } from 'vue'
import MessageInputWorkspace from '../components/chat/MessageInputWorkspace.vue'

vi.mock('../components/chat/AutoReplyControl.vue', () => ({ default: { template: '<span />' } }))

const createDeferred = () => {
  let resolve, reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('MessageInputWorkspace Adversarial Stress Suite', () => {
  let mockApi

  beforeEach(() => {
    mockApi = {
      sendChatMessage: vi.fn().mockResolvedValue({
        success: true,
        session: 'wxid_adversary',
        content_length: 10,
        timestamp: Date.now()
      }),
      getAiSuggestedReply: vi.fn().mockResolvedValue({
        suggestion: 'AI智能生成的候选草稿',
        context_count: 5
      })
    }
    vi.stubGlobal('ref', ref)
    vi.stubGlobal('computed', computed)
    vi.stubGlobal('useApi', () => mockApi)
    const aiView = ref({ selected: {}, drafts: {}, pinned: {} })
    vi.stubGlobal('useState', () => aiView)
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  // -------------------------------------------------------------
  // 1. IME Composition Adversarial Testing
  // -------------------------------------------------------------
  describe('1. IME Composition Attack Vectors', () => {
    it('1.1 During composition, Enter key events with isComposing=true MUST NOT trigger send', async () => {
      const state = reactive({
        selectedAccount: 'wx_user_test',
        selectedContact: { username: 'wxid_friend', name: '朋友' },
        messages: []
      })

      const wrapper = mount(MessageInputWorkspace, {
        props: { state, api: mockApi }
      })

      const textarea = wrapper.find('textarea')
      await textarea.setValue('ceshi')
      await textarea.trigger('compositionstart')

      // Simulate rapid Enter key presses while Pinyin candidate selection is active
      for (let i = 0; i < 5; i++) {
        await textarea.trigger('keydown.enter.exact', { isComposing: true })
      }
      await flushPromises()

      expect(mockApi.sendChatMessage).not.toHaveBeenCalled()
      expect(wrapper.vm.draftText).toBe('ceshi')
    })

    it('1.2 If component isComposing is true but event.isComposing is false, IME guard still blocks send', async () => {
      const state = reactive({
        selectedAccount: 'wx_user_test',
        selectedContact: { username: 'wxid_friend', name: '朋友' },
        messages: []
      })

      const wrapper = mount(MessageInputWorkspace, {
        props: { state, api: mockApi }
      })

      const textarea = wrapper.find('textarea')
      await textarea.setValue('pinyin')
      await textarea.trigger('compositionstart')

      // Synthetic event where event.isComposing is false but composition is in flight
      await textarea.trigger('keydown.enter.exact', { isComposing: false })
      await flushPromises()

      expect(mockApi.sendChatMessage).not.toHaveBeenCalled()
      expect(wrapper.vm.draftText).toBe('pinyin')
    })

    it('1.3 Enter only triggers send AFTER compositionend is explicitly fired', async () => {
      const state = reactive({
        selectedAccount: 'wx_user_test',
        selectedContact: { username: 'wxid_friend', name: '朋友' },
        messages: []
      })

      const wrapper = mount(MessageInputWorkspace, {
        props: { state, api: mockApi }
      })

      const textarea = wrapper.find('textarea')
      await textarea.setValue('pinyin')
      await textarea.trigger('compositionstart')
      await textarea.trigger('keydown.enter.exact', { isComposing: true })
      expect(mockApi.sendChatMessage).not.toHaveBeenCalled()

      // User selects Chinese character candidate
      await textarea.setValue('测试')
      await textarea.trigger('compositionend')

      // Normal Enter after compositionend
      await textarea.trigger('keydown.enter.exact', { isComposing: false })
      await flushPromises()

      expect(mockApi.sendChatMessage).toHaveBeenCalledTimes(1)
      expect(mockApi.sendChatMessage).toHaveBeenCalledWith(expect.objectContaining({
        content: '测试'
      }))
      expect(wrapper.vm.draftText).toBe('')
    })

    it('1.4 Aborted composition (clearing or blur without compositionend) does not trigger send on empty', async () => {
      const state = reactive({
        selectedAccount: 'wx_user_test',
        selectedContact: { username: 'wxid_friend', name: '朋友' },
        messages: []
      })

      const wrapper = mount(MessageInputWorkspace, {
        props: { state, api: mockApi }
      })

      const textarea = wrapper.find('textarea')
      await textarea.trigger('compositionstart')
      await textarea.setValue('')
      await textarea.trigger('compositionend')
      await textarea.trigger('keydown.enter.exact', { isComposing: false })
      await flushPromises()

      expect(mockApi.sendChatMessage).not.toHaveBeenCalled()
    })
  })

  // -------------------------------------------------------------
  // 2. Concurrency & Rapid Burst Submissions
  // -------------------------------------------------------------
  describe('2. Concurrency & Rapid Clicking Attack Vectors', () => {
    it('2.1 Massive parallel click burst (50 concurrent clicks) sends EXACTLY 1 request', async () => {
      const deferred = createDeferred()
      mockApi.sendChatMessage.mockReturnValue(deferred.promise)

      const state = reactive({
        selectedAccount: 'wx_user_test',
        selectedContact: { username: 'wxid_friend', name: '朋友' },
        messages: []
      })

      const wrapper = mount(MessageInputWorkspace, {
        props: { state, api: mockApi }
      })

      const textarea = wrapper.find('textarea')
      await textarea.setValue('防重并发洪峰测试')

      const sendBtn = wrapper.find('.chat-input-btn-send')

      // Fire 50 simultaneous clicks concurrently
      const clickPromises = Array.from({ length: 50 }, () => sendBtn.trigger('click'))
      await Promise.all(clickPromises)

      expect(mockApi.sendChatMessage).toHaveBeenCalledTimes(1)
      expect(wrapper.vm.isSending).toBe(true)
      expect(sendBtn.attributes('disabled')).toBeDefined()
      expect(sendBtn.attributes('aria-busy')).toBe('true')
      expect(textarea.attributes('disabled')).toBeDefined()

      // Resolve the deferred request
      deferred.resolve({ success: true })
      await flushPromises()

      expect(wrapper.vm.isSending).toBe(false)
      expect(wrapper.vm.draftText).toBe('')
      expect(sendBtn.attributes('disabled')).toBeDefined() // disabled because draftText is now empty!
    })

    it('2.2 Interleaved attack: clicking Send button and pressing Enter repeatedly while in-flight', async () => {
      const deferred = createDeferred()
      mockApi.sendChatMessage.mockReturnValue(deferred.promise)

      const state = reactive({
        selectedAccount: 'wx_user_test',
        selectedContact: { username: 'wxid_friend', name: '朋友' },
        messages: []
      })

      const wrapper = mount(MessageInputWorkspace, {
        props: { state, api: mockApi }
      })

      const textarea = wrapper.find('textarea')
      await textarea.setValue('交替按键并发攻击')

      const sendBtn = wrapper.find('.chat-input-btn-send')

      // 1. Initial click
      await sendBtn.trigger('click')
      expect(mockApi.sendChatMessage).toHaveBeenCalledTimes(1)

      // 2. Rapid interleaved triggers
      await textarea.trigger('keydown.enter.exact')
      await sendBtn.trigger('click')
      await textarea.trigger('keydown.enter.exact')
      await sendBtn.trigger('click')
      await wrapper.vm.handleSend()
      await wrapper.vm.handleSend()

      expect(mockApi.sendChatMessage).toHaveBeenCalledTimes(1)

      deferred.resolve({ success: true })
      await flushPromises()
      expect(mockApi.sendChatMessage).toHaveBeenCalledTimes(1)
    })

    it('2.3 Clear draft button is locked and ignored while request is in-flight', async () => {
      const deferred = createDeferred()
      mockApi.sendChatMessage.mockReturnValue(deferred.promise)

      const state = reactive({
        selectedAccount: 'wx_user_test',
        selectedContact: { username: 'wxid_friend', name: '朋友' },
        messages: []
      })

      const wrapper = mount(MessageInputWorkspace, {
        props: { state, api: mockApi }
      })

      const textarea = wrapper.find('textarea')
      await textarea.setValue('发送中不可清空')

      const sendBtn = wrapper.find('.chat-input-btn-send')
      await sendBtn.trigger('click')
      expect(wrapper.vm.isSending).toBe(true)

      const clearBtn = wrapper.find('.chat-input-btn-clear')
      expect(clearBtn.attributes('disabled')).toBeDefined()

      // Attempt to invoke clearDraft
      wrapper.vm.clearDraft()
      expect(wrapper.vm.draftText).toBe('发送中不可清空')

      deferred.resolve({ success: true })
      await flushPromises()
      expect(wrapper.vm.draftText).toBe('')
    })
  })

  // -------------------------------------------------------------
  // 3. Failure Path & Strict Draft Preservation (Let-It-Fail)
  // -------------------------------------------------------------
  describe('3. Failure Path & Draft Preservation (Let-It-Fail Policy)', () => {
    const errorScenarios = [
      {
        scenario: 'WECHAT_NOT_RUNNING',
        error: { code: 'WECHAT_NOT_RUNNING', message: '微信进程未运行' },
        expectedCode: 'WECHAT_NOT_RUNNING',
        expectedMsg: '微信进程未运行'
      },
      {
        scenario: 'WECHAT_WINDOW_NOT_FOUND',
        error: { code: 'WECHAT_WINDOW_NOT_FOUND', message: '未找到微信主窗口' },
        expectedCode: 'WECHAT_WINDOW_NOT_FOUND',
        expectedMsg: '未找到微信主窗口'
      },
      {
        scenario: 'WECHAT_LOCKED',
        error: { code: 'WECHAT_LOCKED', message: '微信当前处于锁定状态' },
        expectedCode: 'WECHAT_LOCKED',
        expectedMsg: '微信当前处于锁定状态'
      },
      {
        scenario: 'WECHAT_RATE_LIMIT_EXCEEDED (FastAPI detail object)',
        error: { detail: { code: 'WECHAT_RATE_LIMIT_EXCEEDED', message: '发送频率超出限制' } },
        expectedCode: 'WECHAT_RATE_LIMIT_EXCEEDED',
        expectedMsg: '发送频率超出限制'
      },
      {
        scenario: 'Raw Error Object without code',
        error: new Error('网络连接超时或网关拒绝'),
        expectedCode: 'SEND_ERROR',
        expectedMsg: '网络连接超时或网关拒绝'
      }
    ]

    for (const { scenario, error, expectedCode, expectedMsg } of errorScenarios) {
      it(`3.X [${scenario}] draft text MUST be preserved and error banner shown verbatim`, async () => {
        mockApi.sendChatMessage.mockRejectedValueOnce(error)

        const state = reactive({
          selectedAccount: 'wx_user_test',
          selectedContact: { username: 'wxid_friend', name: '朋友' },
          messages: []
        })

        const wrapper = mount(MessageInputWorkspace, {
          props: { state, api: mockApi }
        })

        const testDraft = `不可丢失的极重要消息内容 - ${scenario}`
        const textarea = wrapper.find('textarea')
        await textarea.setValue(testDraft)

        const sendBtn = wrapper.find('.chat-input-btn-send')
        await sendBtn.trigger('click')
        await flushPromises()

        // 1. Critical Assertion: Draft text is 100% PRESERVED
        expect(wrapper.vm.draftText).toBe(testDraft)
        expect(textarea.element.value).toBe(testDraft)

        // 2. Concurrency lock is released
        expect(wrapper.vm.isSending).toBe(false)
        expect(textarea.attributes('disabled')).toBeUndefined()

        // 3. Verbatim error banner displayed
        const errorBanner = wrapper.find('.chat-input-error-banner')
        expect(errorBanner.exists()).toBe(true)
        expect(errorBanner.text()).toContain(expectedCode)
        expect(errorBanner.text()).toContain(expectedMsg)

        // 4. User can modify the draft after error and retry
        await textarea.setValue(`${testDraft} (已补充信息)`)
        mockApi.sendChatMessage.mockResolvedValueOnce({ success: true })

        await sendBtn.trigger('click')
        await flushPromises()

        expect(mockApi.sendChatMessage).toHaveBeenCalledWith(expect.objectContaining({
          content: `${testDraft} (已补充信息)`
        }))
        expect(wrapper.vm.draftText).toBe('')
        expect(wrapper.find('.chat-input-error-banner').exists()).toBe(false)
      })
    }
  })

  // -------------------------------------------------------------
  // 4. AI Suggestion Filling & Secondary Editing
  // -------------------------------------------------------------
  describe('4. AI Suggestion Filling & User Secondary Editing', () => {
    it('4.1 AI suggestion populates draft, user can freely edit/refine draft before sending', async () => {
      mockApi.getAiSuggestedReply.mockResolvedValueOnce({
        suggestion: '收到，请于今天下午3点在第2会议室沟通。'
      })

      const state = reactive({
        selectedAccount: 'wx_user_test',
        selectedContact: { username: 'wxid_friend', name: '王总' },
        messages: [{ sender: '王总', text: '下午有时间开会吗？' }]
      })

      const wrapper = mount(MessageInputWorkspace, {
        props: { state, api: mockApi }
      })

      const aiBtn = wrapper.find('.chat-input-btn-ai')
      await aiBtn.trigger('click')
      await flushPromises()

      // Suggestion filled
      expect(wrapper.vm.draftText).toBe('收到，请于今天下午3点在第2会议室沟通。')

      // User performs secondary editing (refinement)
      const textarea = wrapper.find('textarea')
      const refinedText = '收到，请于今天下午3点在第2会议室沟通。（请携带打印材料）'
      await textarea.setValue(refinedText)
      expect(wrapper.vm.draftText).toBe(refinedText)

      // Send the refined text
      const sendBtn = wrapper.find('.chat-input-btn-send')
      await sendBtn.trigger('click')
      await flushPromises()

      // Verify the EDITED text was sent, NOT the raw suggestion
      expect(mockApi.sendChatMessage).toHaveBeenCalledWith({
        account: 'wx_user_test',
        username: 'wxid_friend',
        display_name: '王总',
        content: refinedText
      })
      expect(wrapper.vm.draftText).toBe('')
    })

    it('4.2 When AI suggestion fails, existing user draft is NOT wiped out', async () => {
      mockApi.getAiSuggestedReply.mockRejectedValueOnce({
        code: 'AI_MODEL_NOT_CONFIGURED',
        message: '未配置大模型 API Key'
      })

      const state = reactive({
        selectedAccount: 'wx_user_test',
        selectedContact: { username: 'wxid_friend', name: '王总' },
        messages: []
      })

      const wrapper = mount(MessageInputWorkspace, {
        props: { state, api: mockApi }
      })

      const existingDraft = '这是用户自己写了一半的内容'
      const textarea = wrapper.find('textarea')
      await textarea.setValue(existingDraft)

      const aiBtn = wrapper.find('.chat-input-btn-ai')
      await aiBtn.trigger('click')
      await flushPromises()

      // Existing draft MUST NOT be cleared or overwritten
      expect(wrapper.vm.draftText).toBe(existingDraft)
      expect(textarea.element.value).toBe(existingDraft)

      // Error banner is displayed
      const errorBanner = wrapper.find('.chat-input-error-banner')
      expect(errorBanner.exists()).toBe(true)
      expect(errorBanner.text()).toContain('AI_MODEL_NOT_CONFIGURED')
      expect(errorBanner.text()).toContain('未配置大模型 API Key')
    })
  })

  // -------------------------------------------------------------
  // 5. Boundary & Extreme Input Handling
  // -------------------------------------------------------------
  describe('5. Boundary & Extreme Input Scenarios', () => {
    it('5.1 Whitespace-only content cannot be sent via click or Enter', async () => {
      const state = reactive({
        selectedAccount: 'wx_user_test',
        selectedContact: { username: 'wxid_friend', name: '朋友' },
        messages: []
      })

      const wrapper = mount(MessageInputWorkspace, {
        props: { state, api: mockApi }
      })

      const textarea = wrapper.find('textarea')
      await textarea.setValue('   \n\t  \n  ')

      const sendBtn = wrapper.find('.chat-input-btn-send')
      expect(sendBtn.attributes('disabled')).toBeDefined()

      await sendBtn.trigger('click')
      await textarea.trigger('keydown.enter.exact')
      await wrapper.vm.handleSend()
      await flushPromises()

      expect(mockApi.sendChatMessage).not.toHaveBeenCalled()
    })

    it('5.2 Selected account as an object or string behaves identically', async () => {
      const state = reactive({
        selectedAccount: { account: 'wx_object_acc', nickname: '我的账号' },
        selectedContact: { username: 'wxid_friend', name: '朋友' },
        messages: []
      })

      const wrapper = mount(MessageInputWorkspace, {
        props: { state, api: mockApi }
      })

      const textarea = wrapper.find('textarea')
      await textarea.setValue('对象账号测试')

      const sendBtn = wrapper.find('.chat-input-btn-send')
      await sendBtn.trigger('click')
      await flushPromises()

      expect(mockApi.sendChatMessage).toHaveBeenCalledWith(expect.objectContaining({
        account: 'wx_object_acc'
      }))
    })
  })
})
