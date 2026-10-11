import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AutoReplySettings from '../components/AutoReplySettings.vue'

const confirmation = '删除这条回复记录？仅从本程序的回复历史中移除，不会删除或撤回微信消息。'
const bulkConfirmation = '一键删除当前账号所有会话、所有分页中可删除的回复记录？此操作不受当前筛选影响，正在处理和待核对的记录会保留。仅从本程序的回复历史中移除，不会删除或撤回微信消息。'
const record = (id, account = 'a', status = 'completed') => ({ id, account, session_id: 'peer', target_name: `${account} 的记录 ${id}`,
  status, messages: [], submission_count: 0, created_at: 100 })
const SelectStub = { props: ['modelValue', 'options', 'label', 'multiple', 'disabled'], emits: ['update:modelValue'],
  template: '<select :aria-label="label" :multiple="multiple" :disabled="disabled" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="option in options" :value="option.value">{{option.label}}</option></select>' }
let wrapper, records, request, confirm
const button = text => wrapper.findAll('button').find(node => node.text() === text)
const writes = () => request.mock.calls.filter(([, options]) => options?.method)
const historyCalls = () => request.mock.calls.filter(([path, options]) => path.startsWith('/auto-reply/history?') && !options?.method)
const mountSettings = async () => {
  wrapper = mount(AutoReplySettings, { props: { account: 'a', api: { request } }, global: { stubs: { UiSelect: SelectStub, AgentModelPicker: true } } })
  await flushPromises()
}
const removeFirst = async () => { await button('删除记录').trigger('click'); await flushPromises() }
beforeEach(() => {
  vi.useFakeTimers()
  records = { a: [record('first')], b: [record('other-account', 'b')] }
  confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  request = vi.fn(async (path, options = {}) => {
    const url = new URL(path, 'http://fixture'), account = url.searchParams.get('account')
    if (url.pathname === '/settings') return { profiles: [] }
    if (url.pathname === '/conversations') return []
    if (url.pathname === '/auto-reply/config') return { account, version: 0, profile_id: '', model_id: '', system_prompt: '', sessions: [] }
    if (url.pathname === '/auto-reply/status') return { account, state: 'off', sessions: [], error: null }
    if (url.pathname === '/auto-reply/history') {
      if (options.method === 'DELETE') {
        const count = records[account].length
        records[account] = records[account].filter(item => !['completed', 'cancelled', 'failed', 'interrupted'].includes(item.status))
        return { account, deleted_count: count - records[account].length }
      }
      const page = Number(url.searchParams.get('page')), size = Number(url.searchParams.get('page_size'))
      const status = url.searchParams.get('status'), items = records[account].filter(item => !status || item.status === status)
      return { items: structuredClone(items.slice((page - 1) * size, page * size)), total: items.length, page, page_size: size }
    }
    if (url.pathname.startsWith('/auto-reply/history/') && options.method === 'DELETE') {
      const id = decodeURIComponent(url.pathname.slice('/auto-reply/history/'.length))
      records[account] = records[account].filter(item => item.id !== id)
      return { id, account, deleted: true }
    }
    throw new Error(`Unexpected ${options.method || 'GET'} ${path}`)
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.restoreAllMocks(); vi.useRealTimers() })

it('历史固定每页五条，下一页展示后端对应五条范围', async () => {
  records.a = Array.from({ length: 7 }, (_, index) => record(`entry-${index}`))
  await mountSettings()
  expect(new URL(historyCalls()[0][0], 'http://fixture').searchParams.get('page_size')).toBe('5')
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(5)
  await button('下一页').trigger('click'); await flushPromises()
  const query = new URL(historyCalls().at(-1)[0], 'http://fixture').searchParams
  expect([query.get('page'), query.get('page_size')]).toEqual(['2', '5'])
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(2)
  expect(wrapper.find('.ar-pagination').text()).toContain('共 7 条 · 第 2 页')
})

it('只有四种终态提供删除入口，确认后按编码ID删除本程序记录', async () => {
  records.a = ['completed', 'cancelled', 'failed', 'interrupted'].map((state, index) => record(`entry ${index}?`, 'a', state))
  await mountSettings()
  expect(wrapper.findAll('button').filter(node => node.text() === '删除记录')).toHaveLength(4)
  await removeFirst()
  expect(confirm).toHaveBeenCalledWith(confirmation)
  expect(writes()).toEqual([['/auto-reply/history/entry%200%3F?account=a', { method: 'DELETE', retry: 0 }]])
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(3)
  expect(wrapper.text()).not.toContain('a 的记录 entry 0?')
})

it('活动与待核对记录不允许删除', async () => {
  records.a = ['collecting', 'generating', 'pending', 'submitting', 'needs_review'].map(state => record(state, 'a', state))
  await mountSettings()
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(5)
  expect(button('删除记录')).toBeUndefined()
})

it('取消确认不发出删除或其他写入请求', async () => {
  confirm.mockReturnValue(false)
  await mountSettings()
  await removeFirst()
  expect(confirm).toHaveBeenCalledWith(confirmation)
  expect(writes()).toEqual([])
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(1)
})

it('删除失败明确显示原始错误并保留记录，不自动重试', async () => {
  const original = request.getMockImplementation()
  request.mockImplementation((path, options) => options?.method === 'DELETE' ? Promise.reject(new Error('删除记录失败：数据库不可写')) : original(path, options))
  await mountSettings()
  await removeFirst()
  expect(wrapper.findAll('[role=alert]').map(node => node.text()).join(' ')).toContain('删除记录失败：数据库不可写')
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(1)
  await vi.advanceTimersByTimeAsync(10000); await flushPromises()
  expect(writes()).toHaveLength(1)
})

it('删除末页唯一记录后回到有效上一页，保留五条分页', async () => {
  records.a = Array.from({ length: 6 }, (_, index) => record(`entry-${index}`))
  await mountSettings()
  await button('下一页').trigger('click'); await flushPromises()
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(1)
  await removeFirst()
  expect(wrapper.find('.ar-pagination').text()).toContain('共 5 条 · 第 1 页')
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(5)
  expect(button('上一页').attributes('disabled')).toBeDefined()
  expect(button('下一页').attributes('disabled')).toBeDefined()
  expect(historyCalls().every(([path]) => new URL(path, 'http://fixture').searchParams.get('page_size') === '5')).toBe(true)
})

it('删除进行中禁用筛选、分页与删除，重复点击不重复提交', async () => {
  records.a = Array.from({ length: 6 }, (_, index) => record(`entry-${index}`))
  let finish
  const pending = new Promise(resolve => { finish = resolve })
  const original = request.getMockImplementation()
  request.mockImplementation((path, options) => options?.method === 'DELETE' ? pending : original(path, options))
  await mountSettings()
  await button('删除记录').trigger('click')
  expect(button('删除记录').attributes('disabled')).toBeDefined()
  expect(button('下一页').attributes('disabled')).toBeDefined()
  expect(wrapper.find('[aria-label="记录会话筛选"]').attributes('disabled')).toBeDefined()
  expect(wrapper.find('[aria-label="记录状态筛选"]').attributes('disabled')).toBeDefined()
  await button('删除记录').trigger('click')
  expect(writes()).toHaveLength(1)
  expect(confirm).toHaveBeenCalledTimes(1)
  finish({ id: 'entry-0', account: 'a', deleted: true }); await flushPromises()
})

it('删除成功后读取失败明确报错，用户刷新可恢复剩余记录', async () => {
  records.a.push(record('remaining'))
  let deleted = false, failRead = true
  const original = request.getMockImplementation()
  request.mockImplementation(async (path, options) => {
    if (options?.method === 'DELETE') { const result = await original(path, options); deleted = true; return result }
    if (path.startsWith('/auto-reply/history?') && deleted && failRead) throw new Error('剩余历史读取失败')
    return original(path, options)
  })
  await mountSettings()
  await removeFirst()
  expect(wrapper.findAll('[role=alert]').map(node => node.text()).join(' ')).toContain('剩余历史读取失败')
  expect(wrapper.text()).not.toContain('当前筛选下暂无回复记录')
  expect(wrapper.text()).not.toContain('a 的记录 first')
  failRead = false
  await button('刷新状态').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('a 的记录 remaining')
  expect(wrapper.findAll('[role=alert]')).toHaveLength(0)
  expect(writes()).toHaveLength(1)
})

it('删除响应迟到不能修改新账号记录，也不继续刷新旧账号', async () => {
  let finish
  const pending = new Promise(resolve => { finish = resolve })
  const original = request.getMockImplementation()
  request.mockImplementation((path, options) => options?.method === 'DELETE' ? pending : original(path, options))
  await mountSettings()
  await button('删除记录').trigger('click')
  await wrapper.setProps({ account: 'b' }); await flushPromises()
  const callsBeforeReply = request.mock.calls.length
  finish({ id: 'first', account: 'a', deleted: true }); await flushPromises()
  expect(request).toHaveBeenCalledTimes(callsBeforeReply)
  expect(wrapper.text()).toContain('b 的记录 other-account')
  expect(wrapper.text()).not.toContain('a 的记录 first')
  expect(wrapper.findAll('[role=alert]')).toHaveLength(0)
})

it.each([
  { id: 'first', account: 'b', deleted: true },
  { id: 'other', account: 'a', deleted: true },
  { id: 'first', account: 'a', deleted: false },
])('不采用账号、记录ID或删除结果不符的响应：%j', async response => {
  const original = request.getMockImplementation()
  request.mockImplementation((path, options) => options?.method === 'DELETE' ? Promise.resolve(response) : original(path, options))
  await mountSettings()
  await removeFirst()
  expect(wrapper.findAll('[role=alert]').length).toBeGreaterThan(0)
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(1)
  expect(wrapper.text()).toContain('a 的记录 first')
})

it('一键删除跨越所有分页和会话，不受筛选影响，保留活动记录与其他账号', async () => {
  records.a = [...Array.from({ length: 7 }, (_, index) => ({ ...record(`entry-${index}`), session_id: `peer-${index}` })),
    ...['cancelled', 'failed', 'interrupted', 'pending', 'needs_review'].map(status => record(status, 'a', status))]
  await mountSettings()
  await wrapper.find('[aria-label="记录状态筛选"]').setValue('completed'); await flushPromises()
  await button('下一页').trigger('click'); await flushPromises()
  expect(wrapper.find('.ar-pagination').text()).toContain('第 2 页')
  await button('一键删除').trigger('click'); await flushPromises()
  expect(confirm).toHaveBeenCalledWith(bulkConfirmation)
  expect(writes()).toEqual([['/auto-reply/history?account=a', { method: 'DELETE', retry: 0 }]])
  expect(records.a.map(item => item.status)).toEqual(['pending', 'needs_review'])
  expect(records.b).toEqual([record('other-account', 'b')])
  expect(wrapper.find('.ar-history [role=status]').text()).toBe('已删除 10 条回复记录。')
  expect(wrapper.find('[aria-label="记录状态筛选"]').element.value).toBe('completed')
  expect(wrapper.find('.ar-pagination').text()).toContain('共 0 条 · 第 1 页')
  await wrapper.find('[aria-label="记录状态筛选"]').setValue(''); await flushPromises()
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(2)
})

it('取消一键删除不发请求、不改变记录', async () => {
  confirm.mockReturnValue(false)
  await mountSettings()
  await button('一键删除').trigger('click'); await flushPromises()
  expect(confirm).toHaveBeenCalledWith(bulkConfirmation)
  expect(writes()).toEqual([])
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(1)
})

it('一键删除失败保留记录、展示错误且不自动重试', async () => {
  const original = request.getMockImplementation()
  request.mockImplementation((path, options) => options?.method === 'DELETE' ? Promise.reject(new Error('批量删除失败：数据库不可写')) : original(path, options))
  await mountSettings()
  await button('一键删除').trigger('click'); await flushPromises()
  expect(wrapper.find('.ar-history [role=alert]').text()).toContain('批量删除失败：数据库不可写')
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(1)
  expect(wrapper.find('.ar-history [role=status]').exists()).toBe(false)
  await vi.advanceTimersByTimeAsync(10000); await flushPromises()
  expect(writes()).toHaveLength(1)
})

it('一键删除进行中阻止重复提交，迟到响应不修改新账号', async () => {
  let finish
  const original = request.getMockImplementation()
  request.mockImplementation((path, options) => options?.method === 'DELETE' ? new Promise(resolve => { finish = resolve }) : original(path, options))
  await mountSettings()
  await button('一键删除').trigger('click')
  expect(button('删除中…').attributes('disabled')).toBeDefined()
  expect(button('删除记录').attributes('disabled')).toBeDefined()
  expect(wrapper.find('[aria-label="记录状态筛选"]').attributes('disabled')).toBeDefined()
  await button('删除中…').trigger('click')
  expect(writes()).toHaveLength(1)
  expect(confirm).toHaveBeenCalledTimes(1)
  await wrapper.setProps({ account: 'b' }); await flushPromises()
  const callsBeforeReply = request.mock.calls.length
  finish({ account: 'a', deleted_count: 1 }); await flushPromises()
  expect(request).toHaveBeenCalledTimes(callsBeforeReply)
  expect(wrapper.text()).toContain('b 的记录 other-account')
  expect(wrapper.find('.ar-history [role=status]').exists()).toBe(false)
})

it.each([
  { account: 'b', deleted_count: 1 },
  { account: 'a', deleted_count: -1 },
  { account: 'a', deleted_count: '1' },
  { account: 'a', deleted_count: 1.5 },
  { account: 'a' },
])('一键删除拒绝账号或数量无效的响应：%j', async response => {
  const original = request.getMockImplementation()
  request.mockImplementation((path, options) => options?.method === 'DELETE' ? Promise.resolve(response) : original(path, options))
  await mountSettings()
  await button('一键删除').trigger('click'); await flushPromises()
  expect(wrapper.find('.ar-history [role=alert]').text()).toContain('批量删除回复记录的响应与当前账号不符或数量无效')
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(1)
  expect(wrapper.find('.ar-history [role=status]').exists()).toBe(false)
})

it('没有可删除记录时显示真实零结果并保留待核对记录', async () => {
  records.a = [record('review', 'a', 'needs_review')]
  await mountSettings()
  await button('一键删除').trigger('click'); await flushPromises()
  expect(wrapper.find('.ar-history [role=status]').text()).toBe('当前账号没有可删除的回复记录。')
  expect(wrapper.findAll('.ar-records > li')).toHaveLength(1)
  expect(button('已核对发出')).toBeDefined()
})
