import { mount } from '@vue/test-utils'
import { ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import MessageRecognitionControl from '../components/chat/MessageRecognitionControl.vue'
import ConversationPane from '../components/chat/ConversationPane.vue'

vi.mock('../components/chat/AutoReplyControl.vue', () => ({ default: { template: '<span />' } }))

const state = () => ({ enabled: ref(false), loading: ref(false), saving: ref(false), error: ref(''), pending: ref(0), validScope: ref(true), batch: ref(null), setEnabled: vi.fn(), retry: vi.fn() })
afterEach(() => vi.unstubAllGlobals())
describe('意图识别入口', () => {
  it('开关位于输入区文件按钮旁，模型入口打开画像设置，切换方式及时更新', async () => {
    vi.stubGlobal('useState', () => ref({ selected: {}, drafts: {}, pinned: {} }))
    vi.stubGlobal('fetch', () => { throw new Error('本地组件测试禁止联网') })
    const recognition = state(), insightsPanelOpen = ref(false), recognitionEngine = ref('laya')
    recognition.setEnabled = value => { recognition.enabled.value = value }
    const noop = () => {}
    const pageState = {
      selectedAccount: 'account', selectedContact: { username: 'friend', name: '好友' },
      recognitionState: recognition, recognitionEngine, recognitionHeader: null, insightsPanelOpen,
      toggleInsightsPanel: showSettings => { expect(showSettings).toBe(true); insightsPanelOpen.value = !insightsPanelOpen.value },
      searchContext: { active: false }, messageTypeFilter: '', messageTypeFilterOptions: [],
      privacyMode: false, aiSidebarOpen: false, isLoadingMessages: false, isJumpingToFirst: false,
      isExportCreating: false, voiceSidebarOpen: false, resourceSidebarOpen: false,
      messageSearchOpen: false, timeSidebarOpen: false, showJumpToBottom: false,
      groupAnnouncement: '', groupAnnouncementOpen: false, openGroupAnnouncement: noop,
      closeGroupAnnouncement: noop, toggleAiSidebar: noop, jumpToConversationFirst: noop,
      refreshSelectedMessages: noop, openExportModal: noop, toggleVoiceSidebar: noop,
      toggleResourceSidebar: noop, toggleMessageSearch: noop, toggleTimeSidebar: noop,
      scrollToBottom: noop, exitSearchContext: noop,
    }
    const wrapper = mount(ConversationPane, { props: { state: pageState }, global: { stubs: { MessageList: true, GuideDialog: true } } })
    expect(wrapper.find('.chat-header [role=switch]').exists()).toBe(false)
    const toolbar = wrapper.find('.chat-input-toolbar')
    expect(toolbar.find('[role=switch]').exists()).toBe(true)
    expect(toolbar.find('.chat-input-btn-file').element.nextElementSibling.classList.contains('recognition-control')).toBe(true)
    expect(toolbar.find('[role=switch]').attributes('aria-checked')).toBe('false')
    expect(toolbar.find('[aria-label="配置意图识别模型"]').text()).toBe('本地')
    await toolbar.find('[aria-label="配置意图识别模型"]').trigger('click')
    expect(wrapper.find('[aria-label="聊天画像"]').attributes('aria-pressed')).toBe('true')
    recognitionEngine.value = 'api'; await wrapper.vm.$nextTick()
    expect(toolbar.text()).toContain('API')
    expect(toolbar.find('[role=switch]').attributes('aria-checked')).toBe('false')
    await toolbar.find('[role=switch]').trigger('click')
    expect(toolbar.find('[role=switch]').attributes('aria-checked')).toBe('true')
    wrapper.unmount()
  })
  it('默认关闭且展示API费用，点击明确启用；加载状态期间已启用仍可关闭', async () => {
    const s = state(), w = mount(MessageRecognitionControl, { props: { state: s, engine: 'api', compact: true } })
    expect(w.find('[role=switch]').attributes('aria-checked')).toBe('false')
    expect(w.text()).toContain('可能产生费用')
    expect(s.setEnabled).not.toHaveBeenCalled()
    await w.find('[role=switch]').trigger('click'); expect(s.setEnabled).toHaveBeenCalledWith(true)
    s.enabled.value = true; s.loading.value = true; await w.vm.$nextTick()
    expect(w.find('[role=switch]').attributes('disabled')).toBeUndefined()
    await w.find('[role=switch]').trigger('click'); expect(s.setEnabled).toHaveBeenCalledWith(false)
    w.unmount()
  })
  it('本地无API费用提示；错误明确展示并只由重试按钮触发', async () => {
    const s = state(); s.enabled.value = true; s.error.value = 'MODEL：上游429 · 诊断 abc'
    const w = mount(MessageRecognitionControl, { props: { state: s, engine: 'laya' } })
    expect(w.text()).not.toContain('可能产生费用'); expect(w.find('[role=alert]').text()).toContain('诊断 abc')
    expect(s.retry).not.toHaveBeenCalled()
    await w.find('[role=alert] button').trigger('click'); expect(s.retry).toHaveBeenCalledOnce()
    w.unmount()
  })
  it('紧凑入口保留进度、完整错误和显式重试，模型说明留在详细面板', async () => {
    const s = state(); s.enabled.value = true; s.pending.value = 3
    s.batch.value = { status: 'running', progress: { analyzed: 2, total: 5 }, delivery_mode: 'stream' }
    const w = mount(MessageRecognitionControl, { props: { state: s, engine: 'laya', compact: true } })
    expect(w.text()).not.toContain('请先在画像面板准备')
    expect(w.find('[role=status]').text()).toContain('2/5')
    expect(w.find('[role=status]').text()).toContain('待处理 3')
    s.error.value = 'MODEL：上游429 · 诊断 abc'; await w.vm.$nextTick()
    expect(w.find('[role=alert]').text()).toContain(s.error.value)
    expect(s.retry).not.toHaveBeenCalled()
    await w.find('[role=alert] button').trigger('click')
    expect(s.retry).toHaveBeenCalledOnce()
    w.unmount()
  })
})
