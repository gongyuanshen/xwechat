import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AiSettings from '../components/AiSettings.vue'

const request = vi.fn()
const focusTarget = ref('')
let wrapper
beforeEach(() => {
  focusTarget.value = ''
  vi.stubGlobal('useSettingsDialog', () => ({ focusTarget, openDialog: vi.fn() }))
  vi.stubGlobal('useAiApi', () => ({ request }))
  request.mockReset().mockImplementation(async path => {
    if (path === '/settings') return { profiles: [], presets: [], defaults: {} }
    if (path === '/usage') return { calls: 0 }
    return []
  })
})
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals() })

describe('自动回复设置入口', () => {
  it('从工具栏设置目标打开自动回复标签，不启用服务', async () => {
    focusTarget.value = 'auto-reply'
    wrapper = mount(AiSettings, { global: { stubs: { AutoReplySettings: true } } })
    await flushPromises()
    const tab = wrapper.find('#ais-auto-reply-tab')
    expect(tab.exists()).toBe(true)
    expect(tab.attributes('aria-selected')).toBe('true')
    expect(request.mock.calls.some(([path]) => path.endsWith('/start'))).toBe(false)
  })
})
