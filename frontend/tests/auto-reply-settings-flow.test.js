import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AutoReplySettings from '../components/AutoReplySettings.vue'

const request = vi.fn()
let wrapper, configs, statuses, items, chats
const config = account => ({ account, version: 0, profile_id: 'local', model_id: 'fixed-model', system_prompt: '',
  sessions: [{ session_id: 'peer', target_name: '测试好友', enabled: true, group_trigger: 'mentions' }] })
const status = account => ({ account, state: 'off', run_id: '', error: null, sessions: [] })
const windows = [{ pid: 12, process_created_at: 100, hwnd: 34 }, { pid: 56, process_created_at: 200, hwnd: 78 }]
const SelectStub = { props: { modelValue: [String, Array], options: Array, label: String, disabled: Boolean, multiple: Boolean, summary: String }, emits: ['update:modelValue', 'change'],
  template: `<select :aria-label="label" :summary="summary" :disabled="disabled" :multiple="multiple" :value="modelValue" @change="$emit('update:modelValue',multiple ? [...$event.target.selectedOptions].map(o=>o.value) : $event.target.value)"><option v-if="!multiple" value="">请选择</option><option v-for="o in options" :key="o.value" :value="o.value">{{o.label}}</option></select>` }
const mountSettings = async (account = 'a') => {
  wrapper = mount(AutoReplySettings, { props: { account, api: { request } }, global: { stubs: { UiSelect: SelectStub, AgentModelPicker: true } } })
  await flushPromises()
  return wrapper
}
const button = text => wrapper.findAll('button').find(node => node.text() === text)
beforeEach(() => {
  configs = { a: config('a'), b: config('b') }
  statuses = { a: status('a'), b: status('b') }
  items = []
  chats = [{ username: 'peer', name: '测试好友' }, { username: 'group@chatroom', name: '测试群' }]
  request.mockReset().mockImplementation(async (path, options = {}) => {
    const url = new URL(path, 'http://test')
    const account = url.searchParams.get('account') || options.body?.account
    if (url.pathname === '/settings') return { profiles: [{ id: 'local', name: '本地服务', model: 'fixed-model' }] }
    if (url.pathname === '/conversations') return structuredClone(chats)
    if (url.pathname === '/auto-reply/config') {
      if (options.method === 'PUT') configs[account] = { ...options.body, version: options.body.version + 1 }
      return structuredClone(configs[account])
    }
    if (url.pathname === '/auto-reply/status') return structuredClone(statuses[account])
    if (url.pathname === '/auto-reply/history') return { items: structuredClone(items), total: 21, page: Number(url.searchParams.get('page')), page_size: 20 }
    if (url.pathname === '/auto-reply/windows') return { windows }
    if (url.pathname === '/auto-reply/start') return (statuses[account] = { ...status(account), state: 'running' })
    if (url.pathname === '/auto-reply/stop') return (statuses[account] = status(account))
    if (url.pathname === '/auto-reply/review' || url.pathname === '/auto-reply/resume') return status(account)
    throw new Error(`Unexpected ${path}`)
  })
})
afterEach(() => { wrapper?.unmount(); vi.restoreAllMocks() })

describe('自动回复设置交互', () => {
  const choose = values => wrapper.find('[aria-label="自动回复范围"]').setValue(values)
  const apply = async () => { await button('保存并应用').trigger('click'); await flushPromises() }
  const confirmWindow = async () => {
    await button('检测微信窗口').trigger('click'); await flushPromises()
    await wrapper.find('[aria-label="微信发送窗口"]').setValue('78')
    await wrapper.find('[data-testid="confirm-start"]').setValue(true)
  }

  it('范围是多选下拉，不展开全部会话卡片，也不再显示总开关', async () => {
    for (let i = 0; i < 47; i++) configs.a.sessions.push({ session_id: `group${i}@chatroom`, target_name: `群${i}`, enabled: true, group_trigger: 'mentions' })
    await mountSettings()
    expect(wrapper.find('[aria-label="自动回复范围"]').attributes('multiple')).toBeDefined()
    expect(wrapper.findAll('.ar-session')).toHaveLength(0)
    expect(wrapper.find('[aria-label="自动回复总开关"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('总开关')
    expect(wrapper.find('[aria-label="自动回复范围"]').attributes('summary')).toBe('已选 47 个群聊、1 个私聊')
  })

  it('多选群聊和个人只编辑范围，保存保留已有定位名称和触发方式', async () => {
    configs.a.sessions.push({ session_id: 'group@chatroom', target_name: '人工确认的完整群名', enabled: false, group_trigger: 'all' })
    chats.push({ username: 'new@chatroom', name: '另一个群' }, { username: 'newpeer', name: '另一位好友' })
    statuses.a = { ...status('a'), state: 'running' }
    await mountSettings()
    await choose(['peer', 'group@chatroom', 'new@chatroom', 'newpeer'])
    expect(request.mock.calls.some(([, options]) => options?.method)).toBe(false)
    await apply()
    expect(configs.a.sessions).toEqual([
      { session_id: 'peer', target_name: '测试好友', enabled: true, group_trigger: 'mentions' },
      { session_id: 'group@chatroom', target_name: '人工确认的完整群名', enabled: true, group_trigger: 'all' },
      { session_id: 'new@chatroom', target_name: '另一个群', enabled: true, group_trigger: 'mentions' },
      { session_id: 'newpeer', target_name: '另一位好友', enabled: true, group_trigger: 'mentions' },
    ])
    expect(request.mock.calls.some(([path]) => ['/auto-reply/start', '/auto-reply/stop'].includes(path))).toBe(false)
  })

  it('停用时选择并确认窗口后，保存配置再启动一次，不发送只读元数据', async () => {
    configs.a.cursors = { peer: 'private' }
    await mountSettings()
    await choose(['peer', 'group@chatroom'])
    await wrapper.find('textarea').setValue('简短回复')
    expect(button('保存并应用').attributes('disabled')).toBeDefined()
    await confirmWindow()
    await apply()
    const mutations = request.mock.calls.filter(([, options]) => options?.method)
    expect(mutations.map(([path]) => path)).toEqual(['/auto-reply/config', '/auto-reply/start'])
    expect(mutations[0][1].body.cursors).toBeUndefined()
    expect(mutations[0][1].body.system_prompt).toBe('简短回复')
    expect(mutations[1][1]).toEqual({ method: 'POST', body: { account: 'a', binding: windows[1], confirmed: true }, retry: 0 })
    expect(wrapper.find('[data-testid="auto-reply-state"]').text()).toContain('运行中')
  })

  it('已保存的范围重启后不会自行启动，确认后无需修改配置即可应用', async () => {
    await mountSettings()
    expect(request.mock.calls.some(([, options]) => options?.method)).toBe(false)
    await confirmWindow(); await apply()
    expect(request.mock.calls.filter(([, options]) => options?.method).map(([path]) => path)).toEqual(['/auto-reply/start'])
  })

  it.each(['starting', 'running', 'paused', 'needs_review'])('清空并应用在 %s 状态下只停止当前账号，保留原群参数', async state => {
    configs.a.sessions.push({ session_id: 'group@chatroom', target_name: '完整群名', enabled: true, group_trigger: 'all' })
    statuses.a = { ...status('a'), state, active_account: 'a' }
    await mountSettings(); await choose([])
    expect(request.mock.calls.some(([, options]) => options?.method)).toBe(false)
    await apply()
    expect(configs.a.sessions.every(session => !session.enabled)).toBe(true)
    expect(configs.a.sessions[1].group_trigger).toBe('all')
    expect(request.mock.calls.filter(([path]) => path === '/auto-reply/stop')).toEqual([
      ['/auto-reply/stop', { method: 'POST', body: { account: 'a' }, retry: 0 }],
    ])
    expect(request.mock.calls.some(([path]) => path === '/auto-reply/start')).toBe(false)
  })

  it('其他账号运行时清空本账号范围不会停止其他账号', async () => {
    statuses.a = { ...status('a'), active_account: 'b' }
    await mountSettings(); await choose([]); await apply()
    expect(configs.a.sessions[0].enabled).toBe(false)
    expect(request.mock.calls.some(([path]) => path === '/auto-reply/stop')).toBe(false)
  })

  it('定位名称编辑无效也可清空停止，随后保存失败明确展示已停止，不重试', async () => {
    statuses.a = { ...status('a'), state: 'running', active_account: 'a' }
    const original = request.getMockImplementation()
    request.mockImplementation((path, options) => options?.method === 'PUT' ? Promise.reject(new Error('完整定位名称无效')) : original(path, options))
    await mountSettings()
    await wrapper.find('[aria-label="调整会话"]').setValue('peer')
    await wrapper.find('[aria-label="peer定位名称"]').setValue('')
    await choose([])
    expect(button('保存并应用').attributes('disabled')).toBeUndefined()
    await apply()
    expect(request.mock.calls.filter(([, options]) => options?.method).map(([path]) => path)).toEqual(['/auto-reply/stop', '/auto-reply/config'])
    expect(wrapper.find('[role="alert"]').text()).toContain('回复已停止')
    expect(wrapper.find('[role="alert"]').text()).toContain('完整定位名称无效')
    expect(wrapper.find('[data-testid="auto-reply-state"]').text()).toContain('未运行')
  })

  it('停止失败不继续提交配置，也不把清空显示为已生效', async () => {
    statuses.a = { ...status('a'), state: 'running', active_account: 'a' }
    const original = request.getMockImplementation()
    request.mockImplementation((path, options) => path === '/auto-reply/stop' ? Promise.reject(new Error('停止失败')) : original(path, options))
    await mountSettings(); await choose([]); await apply()
    expect(request.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false)
    expect(wrapper.find('[role="alert"]').text()).toContain('停止失败')
    expect(wrapper.find('[data-testid="auto-reply-state"]').text()).toContain('运行中')
    expect(wrapper.find('[aria-label="自动回复范围"]').attributes('summary')).toBe('已选 0 个群聊、1 个私聊')
  })

  it('暂停恢复仍须窗口和账号确认，应用不会自行解除逐会话暂停', async () => {
    statuses.a = { ...status('a'), state: 'paused', sessions: [{ session_id: 'peer', status: 'running' }] }
    await mountSettings()
    expect(button('保存并应用').attributes('disabled')).toBeDefined()
    await confirmWindow(); await apply()
    expect(request.mock.calls.filter(([path]) => path === '/auto-reply/start')).toHaveLength(1)
    expect(request.mock.calls.some(([path]) => path === '/auto-reply/resume')).toBe(false)
  })

  it('以后新增的群和个人不会自动加入，重新加载后需再次选择', async () => {
    statuses.a = { ...status('a'), state: 'running' }
    await mountSettings(); await choose(['peer', 'group@chatroom']); await apply()
    chats.push({ username: 'later@chatroom', name: '后来加入的群' })
    await button('重新加载').trigger('click'); await flushPromises()
    expect(wrapper.find('[aria-label="自动回复范围"]').attributes('summary')).toBe('已选 1 个群聊、1 个私聊')
    expect(configs.a.sessions.some(session => session.session_id === 'later@chatroom')).toBe(false)
    await choose(['peer', 'group@chatroom', 'later@chatroom']); await apply()
    expect(configs.a.sessions.at(-1)).toEqual({ session_id: 'later@chatroom', target_name: '后来加入的群', enabled: true, group_trigger: 'mentions' })
  })

  it('单独调整只呈现选定的一项，取消但未保存仍显示当前运行状态', async () => {
    configs.a.sessions.push({ session_id: 'group@chatroom', target_name: '完整群名', enabled: true, group_trigger: 'all' })
    statuses.a = { ...status('a'), state: 'running', sessions: [{ session_id: 'group@chatroom', status: 'running' }] }
    await mountSettings()
    await wrapper.find('[aria-label="调整会话"]').setValue('group@chatroom')
    expect(wrapper.findAll('.ar-session')).toHaveLength(1)
    expect(wrapper.find('[aria-label="group@chatroom定位名称"]').element.value).toBe('完整群名')
    await choose(['peer'])
    expect(wrapper.find('.ar-session .ar-state').text()).toBe('运行中·待保存取消')
    expect(configs.a.sessions[1].enabled).toBe(true)
  })

  it('配置已写入但建立基线失败时重新读取真实配置和状态，不报应用成功', async () => {
    statuses.a = { ...status('a'), state: 'running' }
    const original = request.getMockImplementation()
    request.mockImplementation(async (path, options) => {
      const result = await original(path, options)
      if (options?.method === 'PUT') { statuses.a = { ...status('a'), state: 'paused' }; throw new Error('来源读取失败') }
      return result
    })
    await mountSettings(); await choose(['peer', 'group@chatroom']); await apply()
    expect(wrapper.find('[role="alert"]').text()).toContain('来源读取失败')
    expect(wrapper.find('[data-testid="auto-reply-state"]').text()).toContain('暂停')
    expect(wrapper.find('[aria-label="自动回复范围"]').attributes('summary')).toBe('已选 1 个群聊、1 个私聊')
    expect(wrapper.text()).not.toContain('设置已应用')
    expect(request.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(1)
  })

  it('保存期间切换账号后，旧回调不能启动旧账号或覆盖新账号', async () => {
    await mountSettings(); await confirmWindow()
    let finish
    const original = request.getMockImplementation()
    request.mockImplementation((path, options) => options?.method === 'PUT' ? new Promise(resolve => { finish = resolve }) : original(path, options))
    await wrapper.find('textarea').setValue('账号 a 的指令')
    await button('保存并应用').trigger('click')
    await wrapper.setProps({ account: 'b' }); await flushPromises()
    finish({ ...configs.a, system_prompt: '账号 a 的指令', version: 1 }); await flushPromises()
    expect(wrapper.find('textarea').element.value).toBe('')
    expect(wrapper.text()).not.toContain('设置已应用')
    expect(wrapper.find('[data-testid="auto-reply-account"]').text()).toContain('b')
    expect(request.mock.calls.some(([path]) => path === '/auto-reply/start')).toBe(false)
  })

  it('待核对记录区分证据，人工确认未发出不会重发或自动恢复，并支持分页', async () => {
    items = [{ id: 'attempt-1', account: 'a', session_id: 'peer', target_name: '测试好友', status: 'needs_review',
      draft: '只允许一次', messages: [{ content: '触发内容' }], submission_count: 1, submission_phase: 'intent_persisted', evidence: null,
      error: { code: 'WECHAT_SEND_UNCONFIRMED', detail: '点击后未确认' }, created_at: 100 },
    { id: 'attempt-2', account: 'a', session_id: 'peer', target_name: '测试好友', status: 'completed',
      messages: [], submission_count: 1, submission_phase: 'client_displayed', evidence: { status: 'client_displayed' }, created_at: 99 },
    { id: 'attempt-3', account: 'a', session_id: 'peer', target_name: '测试好友', status: 'pending',
      messages: [], submission_count: 0, evidence: null, created_at: 98 }]
    await mountSettings()
    expect(wrapper.text()).toContain('待核对')
    expect(wrapper.text()).toContain('点击后未确认')
    expect(wrapper.text()).not.toContain('发送成功')
    const phases = wrapper.findAll('.ar-records details').map(details => details.findAll('dd')[1].text())
    expect(phases).toEqual(['提交意图已保存 · 1 次提交', '客户端已显示 · 1 次提交', '未记录提交阶段 · 0 次提交'])
    await button('已核对未发出').trigger('click')
    await flushPromises()
    expect(request.mock.calls.find(([path]) => path === '/auto-reply/review')[1].body).toEqual({ account: 'a', attempt_id: 'attempt-1', conclusion: 'not_sent' })
    expect(request.mock.calls.some(([path]) => ['/auto-reply/start', '/auto-reply/resume'].includes(path))).toBe(false)
    await button('下一页').trigger('click')
    await flushPromises()
    expect(request.mock.calls.some(([path]) => path.includes('/history?') && path.includes('page=2'))).toBe(true)
  })

  it('会话恢复必须重新确认，且只恢复选中的会话', async () => {
    statuses.a = { ...status('a'), state: 'running', sessions: [{ session_id: 'peer', status: 'paused', error: { code: 'DRAFT', detail: '已有草稿' } }] }
    await mountSettings()
    await wrapper.find('[aria-label="调整会话"]').setValue('peer')
    expect(button('恢复会话').attributes('disabled')).toBeDefined()
    await wrapper.find('[data-testid="confirm-resume-peer"]').setValue(true)
    await button('恢复会话').trigger('click')
    await flushPromises()
    expect(request.mock.calls.find(([path]) => path === '/auto-reply/resume')[1].body).toEqual({ account: 'a', session_id: 'peer', confirmed: true })
  })

  it('停用时也能解除已核对会话的暂停，但不会自动开启', async () => {
    statuses.a = { ...status('a'), sessions: [{ session_id: 'peer', status: 'paused', error: { code: 'DRAFT', detail: '已有草稿' } }] }
    await mountSettings()
    await wrapper.find('[aria-label="调整会话"]').setValue('peer')
    await wrapper.find('[data-testid="confirm-resume-peer"]').setValue(true)
    expect(button('恢复会话').attributes('disabled')).toBeUndefined()
    await button('恢复会话').trigger('click'); await flushPromises()
    expect(request.mock.calls.some(([path]) => path === '/auto-reply/resume')).toBe(true)
    expect(request.mock.calls.some(([path]) => path === '/auto-reply/start')).toBe(false)
  })

  it('读取失败明确报错，不能显示为默认关闭或空名单', async () => {
    request.mockRejectedValue(new Error('配置读取失败'))
    await mountSettings()
    expect(wrapper.find('[role="alert"]').text()).toContain('配置读取失败')
    expect(wrapper.find('[data-testid="auto-reply-state"]').text()).toContain('未加载')
    expect(wrapper.find('textarea').exists()).toBe(false)
  })

  it('旧账号记录查询迟到不污染新账号，记录加载状态同时重置', async () => {
    await mountSettings()
    let finish
    const original = request.getMockImplementation()
    request.mockImplementation((path, options) => path.includes('/history?account=a&page=2') ? new Promise(resolve => { finish = resolve }) : original(path, options))
    await button('下一页').trigger('click')
    await wrapper.setProps({ account: 'b' }); await flushPromises()
    expect(button('下一页').attributes('disabled')).toBeUndefined()
    finish({ items: [{ id: 'old', account: 'a', target_name: '旧账号私密内容', status: 'pending' }], total: 21, page: 2, page_size: 20 })
    await flushPromises()
    expect(wrapper.text()).not.toContain('旧账号私密内容')
  })

  it('后端返回其他账号历史时明确报错，不展示跨账号记录', async () => {
    items = [{ id: 'wrong', account: 'b', target_name: '其他账号记录', status: 'pending' }]
    await mountSettings()
    expect(wrapper.find('[role="alert"]').text()).toContain('账号')
    expect(wrapper.text()).not.toContain('其他账号记录')
  })
})
