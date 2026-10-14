import { createPinia, storeToRefs } from 'pinia'
import { computed, createSSRApp, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../app.vue'
import AgentPage from '../pages/agent.vue'

vi.mock('~/stores/theme', () => ({ useThemeStore: () => ({ init: vi.fn() }) }))
vi.mock('~/stores/privacy', () => ({ usePrivacyStore: () => ({ init: vi.fn() }) }))
vi.mock('~/composables/useApi', () => ({ useApi: () => ({}) }))
vi.mock('~/components/chat/ChatAgentPanel.vue', () => ({ default: defineComponent({
  props: ['account'], setup: props => () => h('section', { 'data-account': props.account }, '助手已就绪'),
}) }))

let app, container, route, resolveAccounts, rejectAccounts, fetchAccounts
const Empty = defineComponent({ render: () => null })
const Guide = defineComponent({ props: ['open'], setup: props => () => props.open ? h('div', { role: 'dialog' }, '数据准备提示') : null })
const createApp = () => {
  const instance = createSSRApp(App)
  instance.use(createPinia())
  for (const name of ['SidebarRail', 'DesktopTitleBar', 'SettingsDialog']) instance.component(name, Empty)
  instance.component('GuideDialog', Guide)
  instance.component('NuxtPage', defineComponent({ inheritAttrs: false, render: () => h(AgentPage) }))
  instance.component('ErrorNotice', defineComponent({ props: ['message'], setup: props => () => h('p', { role: 'alert' }, props.message) }))
  return instance
}

beforeEach(() => {
  process.client = false
  localStorage.clear()
  route = reactive({ path: '/agent' })
  const response = new Promise((resolve, reject) => { resolveAccounts = resolve; rejectAccounts = reject })
  fetchAccounts = vi.fn(() => response)
  for (const [name, value] of Object.entries({
    computed, ref, watch, onMounted, onBeforeUnmount, storeToRefs,
    useRoute: () => route, useApiBase: () => '/api', $fetch: fetchAccounts,
    useSettingsDialog: () => ({ open: ref(false), focusTarget: ref(''), closeDialog: vi.fn() }),
    useHead: vi.fn(), useState: () => ref(null), navigateTo: vi.fn(),
  })) vi.stubGlobal(name, value)
})
afterEach(() => {
  app?.unmount(); app = null
  container?.remove(); container = null
  vi.restoreAllMocks(); vi.unstubAllGlobals(); delete process.client
  localStorage.clear()
})

const hydrate = async () => {
  const html = await renderToString(createApp())
  expect(fetchAccounts).not.toHaveBeenCalled()
  expect(html).toContain('请导入微信账号，或在设置的“资料备份”中恢复应用资料。')
  container = document.createElement('div')
  container.innerHTML = html
  document.body.append(container)
  process.client = true
  const warnings = vi.spyOn(console, 'warn').mockImplementation(() => {})
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
  app = createApp()
  app.mount(container)
  await flushPromises()
  expect([...warnings.mock.calls, ...errors.mock.calls].flat().filter(value => String(value).toLowerCase().includes('hydration'))).toEqual([])
  expect(fetchAccounts.mock.calls.map(([path]) => path).sort()).toEqual(['/api/library/accounts', '/chat/accounts'])
}

describe('初次进入助手页的账号初始化', () => {
  it('完成 hydration 后才显示加载状态，空资料账号展示恢复入口说明', async () => {
    await hydrate()
    expect(container.textContent).toContain('正在加载账号…')
    resolveAccounts({ accounts: [], chat_accounts: [] })
    await flushPromises()
    expect(container.textContent).toContain('请导入微信账号，或在设置的“资料备份”中恢复应用资料。')
    expect(container.querySelector('[role="dialog"]')).toBeNull()
    route.path = '/'
    await flushPromises()
    expect(container.querySelector('[role="dialog"]')).toBeNull()
  })

  it('资料账号不会提前改变预渲染内容，加载后选中并显示助手', async () => {
    localStorage.setItem('ui.selected_account', 'account-a')
    await hydrate()
    resolveAccounts({ accounts: ['account-a'], chat_accounts: ['account-a'] })
    await flushPromises()
    expect(container.querySelector('[data-account="account-a"]')?.textContent).toBe('助手已就绪')
    expect(container.querySelector('[role="dialog"]')).toBeNull()
  })

  it('加载失败仍暴露真实错误，没有用空账号或成功状态掩盖异常', async () => {
    await hydrate()
    rejectAccounts(new Error('账号服务连接失败'))
    await flushPromises()
    expect(container.querySelector('[role="alert"]')?.textContent).toBe('账号服务连接失败')
    expect(container.querySelector('[data-account]')).toBeNull()
  })
})
