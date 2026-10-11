import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import { reactive, ref } from 'vue'
import MessageInputWorkspace from '../components/chat/MessageInputWorkspace.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals() })
it('输入区将当前账号、群ID和展示名称交给真实自动回复控件，设置只打开专用标签', async () => {
  const account = 'fixture', openDialog = vi.fn()
  const configuration = { account, version: 0, profile_id: '', model_id: '', system_prompt: '', sessions: [] }
  const request = vi.fn(async (path, options) => {
    if (options?.method === 'PUT') return { ...options.body, version: 1 }
    if (path.includes('/config?')) return configuration
    if (path.includes('/status?')) return { account, state: 'off', sessions: [], error: null }
    throw new Error(path)
  })
  vi.stubGlobal('useState', () => ref({}))
  vi.stubGlobal('useAiApi', () => ({ request }))
  vi.stubGlobal('useSettingsDialog', () => ({ openDialog }))
  const state = reactive({ selectedAccount: account, selectedContact: { username: 'project@chatroom', name: '产品测试群' } })
  wrapper = mount(MessageInputWorkspace, { props: { state, api: {} } })
  await flushPromises()
  await wrapper.find('[aria-label="当前会话参与自动回复"]').trigger('click'); await flushPromises()
  expect(request.mock.calls.find(([, options]) => options?.method === 'PUT')[1].body.sessions).toEqual([
    { session_id: 'project@chatroom', target_name: '产品测试群', enabled: true, group_trigger: 'mentions' },
  ])
  await wrapper.find('[aria-label="自动回复设置"]').trigger('click')
  expect(openDialog).toHaveBeenCalledWith('auto-reply')
  expect(request.mock.calls.some(([path]) => path.includes('/start'))).toBe(false)
})
