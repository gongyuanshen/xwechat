import { flushPromises, mount } from '@vue/test-utils'
import { computed, defineComponent, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SidebarRail from '../components/SidebarRail.vue'
import App from '../app.vue'

let route, accounts, navigate, wrapper
vi.mock('pinia', () => ({ storeToRefs: store => store }))
vi.mock('~/stores/chatAccounts', () => ({ useChatAccountsStore: () => accounts }))
vi.mock('~/stores/privacy', () => ({ usePrivacyStore: () => ({ privacyMode: ref(false), init: vi.fn() }) }))
vi.mock('~/stores/theme', () => ({ useThemeStore: () => ({ isDark: false, init: vi.fn(), toggle: vi.fn() }) }))
const Guide = defineComponent({ name: 'GuideDialog', props: ['open'], template: '<div v-if="open" role="dialog">数据准备提示</div>' })
beforeEach(() => {
  process.client = true
  route = reactive({ path: '/agent' })
  accounts = { selectedAccount: ref(null), switchableAccounts: ref([]), accountInfoByName: ref({}), ensureLoaded: vi.fn(async () => {}) }
  navigate = vi.fn(async () => {})
  for (const [name, value] of Object.entries({ computed, ref, watch, onMounted, onBeforeUnmount, storeToRefs: store => store,
    useRoute: () => route, navigateTo: navigate, useApiBase: () => '/api',
    useApi: () => ({ getChatAccountInfo: vi.fn(), deleteChatAccount: vi.fn() }),
    useSettingsDialog: () => ({ open: ref(false), focusTarget: ref(''), openDialog: vi.fn(), closeDialog: vi.fn() }),
  })) vi.stubGlobal(name, value)
})
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.unstubAllGlobals(); delete process.client })

describe('独立 AI 助手路由入口', () => {
  it.each([['事项', '/matters'], ['聊天时光机', '/time-machine']])('%s 可从侧栏进入并标记当前页', async (label, path) => {
    route.path = path
    wrapper = mount(SidebarRail, { global: { stubs: { GlobalExportDialog: true, ErrorNotice: true } } })
    await flushPromises()
    const entry = wrapper.find(`button[aria-label="${label}"]`)
    expect(entry.attributes('aria-current')).toBe('page')
    await entry.trigger('click')
    expect(navigate).toHaveBeenCalledWith(path)
    route.path = '/chat'; await flushPromises()
    expect(entry.attributes('aria-current')).toBeUndefined()
  })
  it.each(['/time-machine'])('%s 没有账号时显示数据准备提示', async path => {
    route.path = path
    wrapper = mount(App, { global: { stubs: { SidebarRail: true, DesktopTitleBar: true, NuxtPage: true, SettingsDialog: true, GuideDialog: Guide } } })
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').text()).toBe('数据准备提示')
  })
  it('应用资料页面无需先准备聊天账号', async () => {
    route.path='/matters';
    wrapper=mount(App,{global:{stubs:{SidebarRail:true,DesktopTitleBar:true,NuxtPage:true,SettingsDialog:true,GuideDialog:Guide}}});
    await flushPromises(); expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  })
  it('侧栏图标定位到 /agent，并随路由切换正确显示当前页', async () => {
    wrapper = mount(SidebarRail, { global: { stubs: { GlobalExportDialog: true, ErrorNotice: true } } })
    await flushPromises()
    const entry = wrapper.find('button[aria-label="AI 助手"]')
    expect(entry.attributes('aria-current')).toBe('page')
    expect(entry.find('svg.lucide').exists()).toBe(true)
    expect(entry.find('.sidebar-rail-plate-active').exists()).toBe(true)
    await entry.trigger('click')
    expect(navigate).toHaveBeenCalledWith('/agent')
    route.path = '/chat'; await flushPromises()
    expect(entry.attributes('aria-current')).toBeUndefined()
    expect(entry.find('.sidebar-rail-plate-active').exists()).toBe(false)
  })
  it('专题问答页面无需先准备聊天账号', async () => {
    wrapper = mount(App, { global: { stubs: { SidebarRail: true, DesktopTitleBar: true, NuxtPage: true, SettingsDialog: true, GuideDialog: Guide } } })
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    accounts.selectedAccount.value = 'account-a'; await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })
})
