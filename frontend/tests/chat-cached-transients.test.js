import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, KeepAlive, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SessionListPanel from '../components/chat/SessionListPanel.vue'
import AgentAnswer from '../components/chat/AgentAnswer.vue'
import AgentThreadList from '../components/chat/AgentThreadList.vue'
import AgentModelPicker from '../components/chat/AgentModelPicker.vue'
import AgentContextRing from '../components/chat/AgentContextRing.vue'
import MessageInputWorkspace from '../components/chat/MessageInputWorkspace.vue'

vi.mock('../components/chat/AutoReplyControl.vue', () => ({ default: { template: '<span />' } }))
import { useAgentPanelResize } from '../composables/useAgentPanelResize'
import ErrorNotice from '../components/ErrorNotice.vue'

vi.mock('~/composables/useApi', () => ({
  useApi: () => ({ listGeneralSearchRecords: async () => ({ items: [] }) }),
}))

let wrapper
const cached = (component, props = {}, options = {}) => {
  const visible = ref(true)
  const Host = defineComponent({
    setup: () => () => h('div', [h(KeepAlive, null, () => visible.value ? h(component, props) : null)]),
  })
  wrapper = mount(Host, { attachTo: document.body, global: { components: { ErrorNotice } }, ...options })
  const child = wrapper.findComponent(component)
  return {
    child,
    async leave() { visible.value = false; await nextTick(); await flushPromises() },
    async returnToChat() { visible.value = true; await nextTick(); await flushPromises() },
  }
}
const escape = () => {
  const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
  document.dispatchEvent(event)
  return event
}

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('innerWidth', 1400)
  vi.stubGlobal('useState', () => ref({ selected: {}, drafts: {}, pinned: {} }))
  vi.stubGlobal('useAiApi', () => ({ request: vi.fn() }))
})
afterEach(() => {
  wrapper?.unmount(); wrapper = null
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('cached chat transient UI', () => {
  it('closes the teleported search popup while preserving the session search text', async () => {
    const state = {
      selectedAccount: ref('account'),
      selectedContact: ref(null),
      contacts: ref([]), filteredContacts: ref([]), availableAccounts: ref([]),
      searchQuery: ref('保留查询'), sessionListWidth: ref(320),
      sessionListResizing: ref(false), privacyMode: ref(false),
      showSearchAccountSwitcher: ref(false), isLoadingContacts: ref(false),
      contactsError: ref(''), onSessionListResizerPointerDown: vi.fn(), resetSessionListWidth: vi.fn(),
    }
    const view = cached(SessionListPanel, { state })
    await view.child.find('input').trigger('focus')
    await flushPromises()
    expect(document.body.querySelector('.general-search-panel')).not.toBeNull()
    await view.leave()
    expect(document.body.querySelector('.general-search-panel')).toBeNull()
    await view.returnToChat()
    expect(view.child.find('input').element.value).toBe('保留查询')
    await view.child.find('input').trigger('focus')
    await flushPromises()
    expect(document.body.querySelector('.general-search-panel')).not.toBeNull()
  })

  it('closes citation previews and detaches their global dismissal listeners', async () => {
    const source = 'a'.repeat(24)
    const view = cached(AgentAnswer, { text: `引用 [[${source}]]`, citations: [{ source, text: '原消息', sender: '好友' }] })
    const button = view.child.find('.agent-ref')
    button.element.getBoundingClientRect = () => ({ top: 100, bottom: 124, left: 100, right: 124 })
    await button.trigger('click')
    await flushPromises()
    expect(view.child.find('.agent-citation-preview').exists()).toBe(true)
    const remove = vi.spyOn(window, 'removeEventListener')
    await view.leave()
    expect(view.child.find('.agent-citation-preview').exists()).toBe(false)
    expect(remove.mock.calls.some(([name]) => name === 'keydown')).toBe(true)
    await view.returnToChat()
    await view.child.find('.agent-ref').trigger('click')
    await flushPromises()
    expect(view.child.find('.agent-citation-preview').exists()).toBe(true)
  })

  it('closes the teleported answer image viewer when chat is cached', async () => {
    const id = 'b'.repeat(24)
    const view = cached(AgentAnswer, {
      text: `图片 [[image:${id}]]`,
      references: [{ id, kind: 'image', path: '/api/test-image.png', label: '图片证据' }],
    })
    await view.child.find('[data-image]').trigger('click')
    await flushPromises()
    expect(document.body.querySelector('.agent-image-viewer')).not.toBeNull()
    await view.leave()
    expect(document.body.querySelector('.agent-image-viewer')).toBeNull()
  })

  it('closes an AI thread management popover without losing the thread search', async () => {
    const view = cached(AgentThreadList, { items: [{ id: 'first', title: '测试会话', username: 'friend' }] })
    await view.child.find('input').setValue('测试')
    await view.child.find('.agent-thread-more').trigger('click')
    await flushPromises()
    expect(view.child.find('[role=menu]').exists()).toBe(true)
    await view.leave()
    expect(view.child.find('[role=menu]').exists()).toBe(false)
    await view.returnToChat()
    expect(view.child.find('input').element.value).toBe('测试')
  })

  it('does not consume another page Escape key from an inactive model menu', async () => {
    const view = cached(AgentModelPicker)
    view.child.find('details').element.open = true
    await view.leave()
    expect(view.child.find('details').element.open).toBe(false)
    expect(escape().defaultPrevented).toBe(false)
    await view.returnToChat()
    view.child.find('details').element.open = true
    expect(escape().defaultPrevented).toBe(true)
    expect(view.child.find('details').element.open).toBe(false)
  })

  it('releases the context tooltip Escape handler and remains usable on return', async () => {
    const view = cached(AgentContextRing, { budget: { percent: 20, used: 200, input_capacity: 1000 } })
    await view.child.find('.agent-context-ring').trigger('mouseenter')
    expect(view.child.find('[role=tooltip]').isVisible()).toBe(true)
    await view.leave()
    expect(escape().defaultPrevented).toBe(false)
    await view.returnToChat()
    expect(view.child.find('[role=tooltip]').isVisible()).toBe(false)
    await view.child.find('.agent-context-ring').trigger('mouseenter')
    expect(escape().defaultPrevented).toBe(true)
  })

  it('ends a sidebar drag and restores document cursor and selection on deactivation', async () => {
    const Panel = defineComponent({
      setup() {
        const panel = ref(null), expanded = ref(false)
        return { panel, ...useAgentPanelResize(panel, expanded) }
      },
      template: '<aside ref="panel"><div role="separator" @pointerdown="start" /></aside>',
    })
    const view = cached(Panel)
    await view.child.find('[role=separator]').trigger('pointerdown', { button: 0, clientX: 900, pointerId: 1 })
    expect(document.body.style.userSelect).toBe('none')
    await view.leave()
    expect(document.body.style.userSelect).toBe('')
    expect(document.body.style.cursor).toBe('')
    const width = view.child.vm.width
    window.dispatchEvent(new PointerEvent('pointermove', { clientX: 400, pointerId: 1 }))
    expect(view.child.vm.width).toBe(width)
    await view.returnToChat()
    await view.child.find('[role=separator]').trigger('pointerdown', { button: 0, clientX: 900, pointerId: 2 })
    expect(document.body.style.userSelect).toBe('none')
  })

  it('stops composer resize on leave while retaining text and unsent attachments', async () => {
    const api = { sendChatMessage: vi.fn(), sendChatFile: vi.fn() }
    vi.stubGlobal('wechatDesktop', undefined)
    window.wechatDesktop = {
      platform: 'win32',
      chooseFile: async () => ({ canceled: false, attachments: [{ path: 'D:\\output\\note.txt', name: 'note.txt', kind: 'file', sizeBytes: 10 }] }),
    }
    const view = cached(MessageInputWorkspace, {
      state: { selectedAccount: ref('account'), selectedContact: ref({ username: 'friend', name: '好友' }) }, api,
    })
    await view.child.find('textarea').setValue('还没发送的草稿')
    await view.child.find('.chat-input-btn-file').trigger('click')
    await flushPromises()
    expect(view.child.find('.chat-input-attachments').text()).toContain('note.txt')
    await view.child.find('[role=separator]').trigger('pointerdown', { clientY: 400 })
    await view.leave()
    const height = view.child.vm.inputHeight
    window.dispatchEvent(new PointerEvent('pointermove', { clientY: 200 }))
    expect(view.child.vm.inputHeight).toBe(height)
    await view.returnToChat()
    expect(view.child.find('textarea').element.value).toBe('还没发送的草稿')
    expect(view.child.find('.chat-input-attachments').text()).toContain('note.txt')
    expect(api.sendChatMessage).not.toHaveBeenCalled()
    expect(api.sendChatFile).not.toHaveBeenCalled()
    delete window.wechatDesktop
  })

  it('pauses the remaining attachment batch on leave while accepting the in-flight receipt', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    let confirmFirst
    const firstReceipt = new Promise(resolve => { confirmFirst = resolve })
    const api = {
      sendChatMessage: vi.fn(),
      sendChatFile: vi.fn().mockResolvedValue({ success: true }).mockReturnValueOnce(firstReceipt),
    }
    vi.stubGlobal('wechatDesktop', {
      platform: 'win32',
      chooseFile: async () => ({ canceled: false, attachments: [
        { path: 'D:\\output\\first.txt', name: 'first.txt', kind: 'file', sizeBytes: 10 },
        { path: 'D:\\output\\second.txt', name: 'second.txt', kind: 'file', sizeBytes: 20 },
      ] }),
    })
    const view = cached(MessageInputWorkspace, {
      state: { selectedAccount: ref('account'), selectedContact: ref({ username: 'friend', name: '好友' }) }, api,
    })
    await view.child.find('textarea').setValue('保留未发送文字')
    await view.child.find('.chat-input-btn-file').trigger('click')
    await flushPromises()
    await view.child.find('.chat-input-btn-send-attachments').trigger('click')
    expect(api.sendChatFile).toHaveBeenCalledTimes(1)
    expect(api.sendChatFile).toHaveBeenLastCalledWith({ account: 'account', username: 'friend', display_name: '好友', file_path: 'D:\\output\\first.txt' })
    await view.leave()
    confirmFirst({ success: true })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1000)
    expect(api.sendChatFile).toHaveBeenCalledTimes(1)
    await view.returnToChat()
    expect(view.child.findAll('.chat-input-attachment-name').map(item => item.text())).toEqual(['second.txt'])
    expect(view.child.find('textarea').element.value).toBe('保留未发送文字')
    expect(view.child.vm.isSending).toBe(false)
    expect(view.child.vm.errorInfo.code).toBe('ATTACHMENT_TARGET_CHANGED')
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.sendChatFile).toHaveBeenCalledTimes(1)
    expect(api.sendChatMessage).not.toHaveBeenCalled()
  })
})
