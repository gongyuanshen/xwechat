import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { defineComponent, h, KeepAlive, ref } from 'vue'
import AutoReplyControl from '../components/chat/AutoReplyControl.vue'

const config = (account = 'account-a', sessions = []) => ({ account, profile_id: 'local', model_id: 'model', system_prompt: '简短回复', version: 3, sessions })
const status = (account = 'account-a', state = 'off', sessions = []) => ({ account, state, run_id: null, error: null, sessions })
const session = (sessionId = 'friend', enabled = true) => ({ session_id: sessionId, target_name: '好友完整名称', enabled, group_trigger: 'mentions' })
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
let wrappers, openDialog

beforeEach(() => {
  wrappers = []
  openDialog = vi.fn()
  vi.useFakeTimers()
  vi.stubGlobal('useSettingsDialog', () => ({ openDialog }))
})
afterEach(() => {
  wrappers.forEach(wrapper => wrapper.unmount())
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
function setup(request, props = {}) {
  vi.stubGlobal('useAiApi', () => ({ request }))
  const wrapper = mount(AutoReplyControl, { props: { account: 'account-a', sessionId: 'friend', targetName: '好友完整名称', group: false, ...props } })
  wrappers.push(wrapper)
  return wrapper
}
function apiHandler(path, options = {}) {
  if (path === '/auto-reply/config' && options.method === 'PUT') return Promise.resolve({ ...options.body, version: options.body.version + 1 })
  if (path === '/auto-reply/stop' && options.method === 'POST') return Promise.resolve(status(options.body.account))
  const account = new URLSearchParams(path.split('?')[1]).get('account')
  if (path.startsWith('/auto-reply/config?')) return Promise.resolve(config(account))
  if (path.startsWith('/auto-reply/status?')) return Promise.resolve(status(account))
  throw new Error(`Unexpected API request: ${path}`)
}

it('默认未参与，选择群聊保存默认仅 @ 我并打开设置等待确认应用', async () => {
  const request = vi.fn(apiHandler)
  const wrapper = setup(request, { sessionId: 'group@chatroom', targetName: '测试群', group: true })
  await flushPromises()
  const toggle = wrapper.find('[role="switch"]')
  expect(toggle.attributes('aria-checked')).toBe('false')
  expect(toggle.text()).toBe('参与回复')
  expect(toggle.attributes('aria-label')).toBe('当前会话参与自动回复')
  expect(toggle.attributes('title')).toContain('将本群加入自动回复范围')
  expect(wrapper.find('[role="status"]').text()).toBe('未参与')
  await toggle.trigger('click'); await flushPromises()
  expect(toggle.attributes('aria-checked')).toBe('true')
  expect(wrapper.find('[role="status"]').text()).toBe('已加入·待确认应用')
  expect(request.mock.calls.filter(([, options]) => options?.method === 'PUT')).toEqual([
    ['/auto-reply/config', { method: 'PUT', retry: 0, body: { ...config(), sessions: [{ session_id: 'group@chatroom', target_name: '测试群', enabled: true, group_trigger: 'mentions' }] } }],
  ])
  expect(request.mock.calls.some(([path]) => path.includes('/start'))).toBe(false)
  expect(openDialog).toHaveBeenCalledWith('auto-reply')
  expect(toggle.attributes('title')).toContain('未运行时需在设置中确认应用')
})

it.each([['off', true], ['paused', true], ['running', false]])('服务%s时启用会话按实际运行状态决定是否引导确认', async (state, needsConfirmation) => {
  const request = vi.fn((path, options) => path.startsWith('/auto-reply/status?') ? Promise.resolve(status('account-a', state)) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(openDialog).toHaveBeenCalledTimes(needsConfirmation ? 1 : 0)
  expect(request.mock.calls.some(([path]) => path === '/auto-reply/start')).toBe(false)
})

it('取消最后一个参与会话时先停止同一账号再保存范围', async () => {
  const request = vi.fn((path, options) => path.startsWith('/auto-reply/config?') ? Promise.resolve(config('account-a', [session()])) : path.startsWith('/auto-reply/status?') ? Promise.resolve(status('account-a', 'running')) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  const writes = request.mock.calls.filter(([, options]) => options?.method)
  expect(writes.map(([path]) => path)).toEqual(['/auto-reply/stop', '/auto-reply/config'])
  expect(writes[0][1]).toEqual({ method: 'POST', body: { account: 'account-a' }, retry: 0 })
  expect(writes[1][1].body.sessions[0].enabled).toBe(false)
  expect(wrapper.find('[role="status"]').text()).toBe('未参与')
  expect(openDialog).not.toHaveBeenCalled()
})

it('还有其他参与会话时只保存范围，不停止服务或打开设置', async () => {
  const request = vi.fn((path, options) => path.startsWith('/auto-reply/config?') ? Promise.resolve(config('account-a', [session(), session('other')])) : path.startsWith('/auto-reply/status?') ? Promise.resolve(status('account-a', 'running')) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(request.mock.calls.filter(([, options]) => options?.method).map(([path]) => path)).toEqual(['/auto-reply/config'])
  expect(request.mock.calls.find(([, options]) => options?.method === 'PUT')[1].body.sessions[1].enabled).toBe(true)
  expect(openDialog).not.toHaveBeenCalled()
})

it('停止失败时展示原始错误，不继续保存或自动重试', async () => {
  const request = vi.fn((path, options) => path.startsWith('/auto-reply/config?') ? Promise.resolve(config('account-a', [session()])) : path.startsWith('/auto-reply/status?') ? Promise.resolve(status('account-a', 'running')) : path === '/auto-reply/stop' ? Promise.reject(new Error('停止失败：账号已变化')) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('停止失败：账号已变化')
  await vi.advanceTimersByTimeAsync(5000); await flushPromises()
  expect(request.mock.calls.filter(([, options]) => options?.method).map(([path]) => path)).toEqual(['/auto-reply/stop'])
  expect(wrapper.find('[role="switch"]').attributes('aria-checked')).toBe('true')
})

it('停止成功但保存失败时明确告知已停止且设置未保存，不重试', async () => {
  let stopped = false
  const request = vi.fn((path, options) => {
    if (path.startsWith('/auto-reply/config?')) return Promise.resolve(config('account-a', [session()]))
    if (path.startsWith('/auto-reply/status?')) return Promise.resolve(status('account-a', stopped ? 'off' : 'running'))
    if (path === '/auto-reply/stop') { stopped = true; return apiHandler(path, options) }
    if (options?.method === 'PUT') return Promise.reject(new Error('配置版本冲突'))
    return apiHandler(path, options)
  })
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('已停止，但设置未保存：配置版本冲突')
  expect(wrapper.find('[role="status"]').text()).toBe('已加入·待确认应用')
  await vi.advanceTimersByTimeAsync(5000); await flushPromises()
  expect(request.mock.calls.filter(([, options]) => options?.method).map(([path]) => path)).toEqual(['/auto-reply/stop', '/auto-reply/config'])
})

it.each([{ account: 'account-b' }, { sessionId: 'another', targetName: '其他好友' }])('停止期间切换账号或会话后旧响应不能继续发出配置保存：%j', async next => {
  const stop = deferred()
  const request = vi.fn((path, options) => path === '/auto-reply/config?account=account-a' ? Promise.resolve(config('account-a', [session()])) : path === '/auto-reply/status?account=account-a' ? Promise.resolve(status('account-a', 'running')) : path === '/auto-reply/stop' ? stop.promise : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click')
  await wrapper.setProps(next); await flushPromises()
  stop.resolve(status('account-a')); await flushPromises()
  expect(request.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false)
  expect(wrapper.find('[role="switch"]').attributes('aria-checked')).toBe('false')
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(openDialog).not.toHaveBeenCalled()
})

it('停止响应账号不一致时显示错误且不继续配置保存', async () => {
  const request = vi.fn((path, options) => path.startsWith('/auto-reply/config?') ? Promise.resolve(config('account-a', [session()])) : path.startsWith('/auto-reply/status?') ? Promise.resolve(status('account-a', 'running')) : path === '/auto-reply/stop' ? Promise.resolve(status('wrong-account')) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('响应账号不一致')
  expect(request.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false)
})

it('新增会话保存后立即读取状态，按实际基线暂停结果展示并打开设置', async () => {
  let saved = false
  const request = vi.fn((path, options) => {
    if (options?.method === 'PUT') { saved = true; return apiHandler(path, options) }
    if (path.startsWith('/auto-reply/status?')) return Promise.resolve(status('account-a', 'running', saved ? [{ session_id: 'friend', status: 'paused', error: { code: 'BASELINE_FAILED', detail: '基线读取失败' } }] : []))
    return apiHandler(path, options)
  })
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(request.mock.calls.filter(([path]) => path.startsWith('/auto-reply/status?'))).toHaveLength(2)
  expect(wrapper.find('[role="status"]').text()).toBe('已加入·已暂停')
  expect(wrapper.find('[role="alert"]').text()).toContain('BASELINE_FAILED：基线读取失败')
  expect(openDialog).toHaveBeenCalledWith('auto-reply')
})

it('运行服务缺少当前会话状态时显示等待状态，不默认运行中', async () => {
  const request = vi.fn((path, options) => path.startsWith('/auto-reply/config?') ? Promise.resolve(config('account-a', [session()])) : path.startsWith('/auto-reply/status?') ? Promise.resolve(status('account-a', 'running')) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  expect(wrapper.find('[role="status"]').text()).toBe('已加入·等待状态')
})

it('保存后状态读取失败明确显示已保存且读取失败，轮询恢复后清除读取错误', async () => {
  let saved = false, failed = true
  const request = vi.fn((path, options) => {
    if (options?.method === 'PUT') { saved = true; return apiHandler(path, options) }
    if (path.startsWith('/auto-reply/status?') && saved && failed) return Promise.reject(new Error('状态不可用'))
    return apiHandler(path, options)
  })
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('设置已保存，但状态读取失败：状态不可用')
  expect(wrapper.find('[role="status"]').text()).toBe('读取失败')
  expect(openDialog).not.toHaveBeenCalled()
  failed = false
  await vi.advanceTimersByTimeAsync(5000); await flushPromises()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})

it('保存后的状态读取迟到时不覆盖新账号或弹出旧会话设置', async () => {
  const pendingStatus = deferred()
  let saved = false
  const request = vi.fn((path, options) => {
    if (options?.method === 'PUT') { saved = true; return apiHandler(path, options) }
    if (path === '/auto-reply/status?account=account-a' && saved) return pendingStatus.promise
    return apiHandler(path, options)
  })
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  await wrapper.setProps({ account: 'account-b' }); await flushPromises()
  pendingStatus.resolve(status('account-a')); await flushPromises()
  expect(wrapper.find('[role="switch"]').attributes('aria-checked')).toBe('false')
  expect(openDialog).not.toHaveBeenCalled()
})

it('保存完整配置且保留其他会话与当前会话的明确定位名称和触发方式', async () => {
  const existing = { ...config('account-a', [{ ...session(), target_name: '已确认名称', group_trigger: 'all' }, session('other')]), reasoning_effort: 'high' }
  const returned = { ...existing, updated_at: 100, sessions: existing.sessions.map(item => ({ ...item, cursor: { timestamp: 42 } })) }
  const request = vi.fn((path, options) => path.startsWith('/auto-reply/config?') ? Promise.resolve(returned) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(request.mock.calls.find(([, options]) => options?.method === 'PUT')[1].body).toEqual({ ...existing, sessions: [{ ...existing.sessions[0], enabled: false }, existing.sessions[1]] })
})

it.each([
  [{ account: 'account-b' }, 'account-b'],
  [{ sessionId: 'another', targetName: '另一个好友' }, 'account-a'],
])('切换账号或会话后旧 PUT 响应不能覆盖当前开关：%j', async (next, currentAccount) => {
  const save = deferred()
  const request = vi.fn((path, options) => options?.method === 'PUT' ? save.promise : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click')
  await wrapper.setProps(next); await flushPromises()
  expect(wrapper.find('[role="switch"]').attributes('aria-checked')).toBe('false')
  save.resolve(config('account-a', [session(), session('another')])); await flushPromises()
  expect(wrapper.find('[role="switch"]').attributes('aria-checked')).toBe('false')
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(request.mock.calls.some(([path]) => path === `/auto-reply/config?account=${currentAccount}`)).toBe(true)
  expect(openDialog).not.toHaveBeenCalled()
})

it('保存失败后保留错误，即使后台轮询成功也不掩盖或重试保存', async () => {
  const request = vi.fn((path, options) => options?.method === 'PUT' ? Promise.reject(new Error('配置版本冲突，请在设置中检查')) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('配置版本冲突')
  expect(wrapper.find('[role="switch"]').attributes('aria-checked')).toBe('false')
  await vi.advanceTimersByTimeAsync(5000); await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('配置版本冲突')
  expect(request.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(1)
})

it('读取恢复后清除读取错误，不继续把可用配置显示为读取失败', async () => {
  let failing = true
  const request = vi.fn((path, options) => failing ? Promise.reject(new Error('状态读取失败')) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('状态读取失败')
  failing = false
  await vi.advanceTimersByTimeAsync(5000); await flushPromises()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.find('[role="switch"]').attributes('disabled')).toBeUndefined()
})

it('服务停止后仍突出当前会话的待核对状态', async () => {
  const request = vi.fn((path, options) => path.startsWith('/auto-reply/config?') ? Promise.resolve(config('account-a', [session()])) : path.startsWith('/auto-reply/status?') ? Promise.resolve(status('account-a', 'off', [{ session_id: 'friend', status: 'needs_review', error: null }])) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  expect(wrapper.find('[role="status"]').text()).toContain('待核对')
})

it.each([
  ['paused', 'running', true, '已加入·已暂停'],
  ['starting', 'running', true, '已加入·启动中'],
  ['off', 'running', true, '已加入·待确认应用'],
  ['running', 'paused', true, '已加入·已暂停'],
  ['paused', 'needs_review', true, '待核对'],
  ['starting', 'needs_review', true, '待核对'],
  ['paused', 'running', false, '未参与'],
  ['off', 'needs_review', false, '待核对'],
])('总服务状态和会话状态共同决定显示：%s / %s / %s', async (globalState, sessionState, enabled, expected) => {
  const request = vi.fn((path, options) => path.startsWith('/auto-reply/config?') ? Promise.resolve(config('account-a', [session('friend', enabled)])) : path.startsWith('/auto-reply/status?') ? Promise.resolve(status('account-a', globalState, [{ session_id: 'friend', status: sessionState, error: null }])) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  expect(wrapper.find('[role="status"]').text()).toBe(expected)
})

it('首次添加缺少定位名称时显示设置提示，不把会话 ID 当成名称', async () => {
  const request = vi.fn(apiHandler)
  const wrapper = setup(request, { targetName: '' })
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('定位名称')
  expect(request.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false)
  await wrapper.find('[aria-label="自动回复设置"]').trigger('click')
  expect(openDialog).toHaveBeenCalledWith('auto-reply')
})

it.each(['config', 'status'])('响应账号不一致时显式显示错误并阻止保存：%s', async endpoint => {
  const request = vi.fn((path, options) => path.startsWith(`/auto-reply/${endpoint}?`) ? Promise.resolve(endpoint === 'config' ? config('wrong-account') : status('wrong-account')) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('账号不一致')
  expect(wrapper.find('[role="switch"]').attributes('disabled')).toBeDefined()
})

it('保存期间阻止重复点击及轮询并发，卸载停止状态轮询', async () => {
  const save = deferred()
  const request = vi.fn((path, options) => options?.method === 'PUT' ? save.promise : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click')
  expect(wrapper.find('[role="switch"]').attributes('disabled')).toBeDefined()
  await wrapper.find('[role="switch"]').trigger('click')
  const count = request.mock.calls.length
  await vi.advanceTimersByTimeAsync(5000)
  expect(request).toHaveBeenCalledTimes(count)
  wrapper.unmount(); wrappers = []
  save.resolve(config('account-a', [session()])); await flushPromises()
  await vi.advanceTimersByTimeAsync(10000)
  expect(request).toHaveBeenCalledTimes(count)
})

it('每五秒读取真实状态，展示当前会话待核对和原始错误', async () => {
  let current = status('account-a', 'running', [{ session_id: 'friend', status: 'running', error: null }])
  const request = vi.fn((path, options) => path.startsWith('/auto-reply/status?') ? Promise.resolve(current) : path.startsWith('/auto-reply/config?') ? Promise.resolve(config('account-a', [session()])) : apiHandler(path, options))
  const wrapper = setup(request, { api: { request } })
  await flushPromises()
  expect(wrapper.find('[role="status"]').text()).toContain('运行中')
  current = status('account-a', 'running', [{ session_id: 'friend', status: 'needs_review', error: { code: 'SUBMISSION_UNKNOWN', detail: '客户端确认超时，请核对微信' } }])
  await vi.advanceTimersByTimeAsync(4999)
  expect(wrapper.find('[role="status"]').text()).toContain('运行中')
  await vi.advanceTimersByTimeAsync(1); await flushPromises()
  expect(wrapper.find('[role="status"]').text()).toContain('待核对')
  expect(wrapper.find('[role="alert"]').text()).toContain('SUBMISSION_UNKNOWN：客户端确认超时，请核对微信')
})

it('旧账号 GET 迟到不能覆盖新账号状态，旧请求失败也不能污染新会话错误', async () => {
  const oldConfig = deferred(), oldStatus = deferred()
  const request = vi.fn((path, options) => path === '/auto-reply/config?account=account-a' ? oldConfig.promise : path === '/auto-reply/status?account=account-a' ? oldStatus.promise : apiHandler(path, options))
  const wrapper = setup(request)
  await wrapper.setProps({ account: 'account-b' }); await flushPromises()
  oldConfig.resolve(config('account-a', [session()]))
  oldStatus.reject(new Error('旧账号断连')); await flushPromises()
  expect(wrapper.find('[role="switch"]').attributes('aria-checked')).toBe('false')
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(request.mock.calls.find(([, options]) => options?.method === 'PUT')[1].body.account).toBe('account-b')
})

it('PUT 返回其他账号配置时展示错误且不采用返回的开关状态', async () => {
  const request = vi.fn((path, options) => options?.method === 'PUT' ? Promise.resolve(config('wrong-account', [session()])) : apiHandler(path, options))
  const wrapper = setup(request)
  await flushPromises()
  await wrapper.find('[role="switch"]').trigger('click'); await flushPromises()
  expect(wrapper.find('[role="alert"]').text()).toContain('账号不一致')
  expect(wrapper.find('[role="switch"]').attributes('aria-checked')).toBe('false')
})

it('离开被缓存的聊天页时停止轮询，返回后只恢复一轮计时', async () => {
  const request = vi.fn(apiHandler), active = ref(true)
  vi.stubGlobal('useAiApi', () => ({ request }))
  const Host = defineComponent({ setup: () => () => h(KeepAlive, null, { default: () => active.value ? h(AutoReplyControl, { account: 'account-a', sessionId: 'friend', targetName: '好友' }) : null }) })
  const wrapper = mount(Host); wrappers.push(wrapper)
  await flushPromises()
  expect(request).toHaveBeenCalledTimes(2)
  active.value = false; await flushPromises()
  await vi.advanceTimersByTimeAsync(10000)
  expect(request).toHaveBeenCalledTimes(2)
  active.value = true; await flushPromises()
  expect(request).toHaveBeenCalledTimes(4)
  await vi.advanceTimersByTimeAsync(5000); await flushPromises()
  expect(request).toHaveBeenCalledTimes(6)
})
