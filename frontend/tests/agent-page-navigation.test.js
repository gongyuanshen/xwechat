import { ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { createAiNavigationConsumer } from '../utils/createAiNavigationConsumer'

const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes }); return { promise, resolve } }
const setup = (target = { kind: 'source', account: 'a', username: 'friend', anchor: '1' }) => {
  const navigation = ref(target), account = ref('a'), active = ref(true)
  const deps = {
    navigation, account, isActive: () => active.value,
    ensureLoaded: vi.fn(async () => {}), selectAccount: vi.fn(next => { account.value = next }),
    waitForAccount: vi.fn(async () => {}), showSourceChat: vi.fn(async () => {}), openTask: vi.fn(), locateSource: vi.fn(async () => true),
    diagnostic: vi.fn(), showError: vi.fn(),
  }
  return { ...deps, active, consume: createAiNavigationConsumer(deps) }
}

describe('AI 来源与任务通知导航', () => {
  it('统一搜索定位失败返回正确入口', async () => {
    const state = setup({ kind: 'source', origin: 'search', account: 'a', username: 'friend', anchor: '1' })
    state.locateSource.mockRejectedValue(new Error('原消息已不可用'))
    await state.consume()
    expect(state.showError).toHaveBeenCalledWith(expect.stringContaining('返回搜索'))
    expect(state.showError.mock.calls[0][0]).not.toContain('返回 AI 助手')
  })
  it('事项通知由事项页面消费，活动聊天页不抢先清除或切换账号', async () => {
    const target = { account: 'b', matter_id: 'matter-1' }, state = setup(target)
    await state.consume()
    expect(state.navigation.value).toEqual(target)
    expect(state.ensureLoaded).not.toHaveBeenCalled()
    expect(state.selectAccount).not.toHaveBeenCalled()
    expect(state.openTask).not.toHaveBeenCalled()
  })
  it('来源只定位聊天原消息，不打开任务侧栏', async () => {
    const state = setup(); await state.consume()
    expect(state.locateSource).toHaveBeenCalledWith(expect.objectContaining({ kind: 'source', anchor: '1' }))
    expect(state.openTask).not.toHaveBeenCalled()
    expect(state.showSourceChat).toHaveBeenCalledOnce()
    expect(state.navigation.value).toBeNull()
  })
  it('原有任务通知继续打开任务侧栏并聚焦任务', async () => {
    const target = { account: 'b', task_id: 'task-1' }, state = setup(target)
    await state.consume()
    expect(state.selectAccount).toHaveBeenCalledWith('b')
    expect(state.openTask).toHaveBeenCalledWith('task-1')
    expect(state.locateSource).not.toHaveBeenCalled()
    expect(state.navigation.value).toBeNull()
  })
  it.each([false, new Error('数据库读取失败')])('来源定位失败显示错误并保留目标以便返回后重试：%s', async result => {
    const state = setup()
    state.locateSource.mockImplementation(async () => { if (result instanceof Error) throw result; return result })
    await state.consume()
    expect(state.showError).toHaveBeenCalledWith(expect.stringMatching(/未定位到来源消息|数据库读取失败/))
    expect(state.navigation.value).not.toBeNull()
    expect(state.diagnostic).not.toHaveBeenCalledWith('navigation.finished', expect.anything())
  })
  it.each(['target', 'account', 'inactive'])('等待来源期间 %s 改变，旧请求不清除新目标或显示错误', async changed => {
    const state = setup(), pending = deferred(); state.locateSource.mockReturnValue(pending.promise)
    const consume = state.consume(); await Promise.resolve(); await Promise.resolve()
    if (changed === 'target') state.navigation.value = { kind: 'source', account: 'a', username: 'friend', anchor: 'new' }
    if (changed === 'account') state.account.value = 'other'
    if (changed === 'inactive') state.active.value = false
    pending.resolve(false); await consume
    expect(state.showError).not.toHaveBeenCalled()
    expect(state.navigation.value).not.toBeNull()
  })
  it('同一目标的挂载与激活回调只启动一次定位', async () => {
    const state = setup(), pending = deferred(); state.waitForAccount.mockReturnValue(pending.promise)
    const first = state.consume(), second = state.consume(); pending.resolve(); await Promise.all([first, second])
    expect(state.locateSource).toHaveBeenCalledOnce()
  })
  it('聊天准备结束前不定位；等待超时按失败显示且不记成功', async () => {
    const state = setup(), pending = deferred(); state.waitForAccount.mockReturnValue(pending.promise)
    const consume = state.consume(); await Promise.resolve(); await Promise.resolve()
    expect(state.locateSource).not.toHaveBeenCalled()
    pending.resolve(); await consume
    expect(state.locateSource).toHaveBeenCalledOnce()
    const timeout = setup(); timeout.waitForAccount.mockRejectedValue(new Error('聊天初始化尚未完成'))
    await timeout.consume()
    expect(timeout.locateSource).not.toHaveBeenCalled()
    expect(timeout.showError).toHaveBeenCalledWith(expect.stringContaining('聊天初始化尚未完成'))
    expect(timeout.navigation.value).not.toBeNull()
  })
})
