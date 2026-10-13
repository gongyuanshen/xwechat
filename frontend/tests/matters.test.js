import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import MatterSaveDialog from '../components/chat/MatterSaveDialog.vue'
import MattersPage from '../pages/matters.vue'
import AiSummaryResult from '../components/chat/AiSummaryResult.vue'

const state = vi.hoisted(() => ({ account: null, privacy: null, api: null }))
vi.mock('~/stores/chatAccounts', () => ({ useChatAccountsStore: () => state.account }))
vi.mock('~/stores/applicationAccounts', () => ({ useApplicationAccountsStore: () => state.account }))
vi.mock('~/stores/privacy', () => ({ usePrivacyStore: () => state.privacy }))
vi.mock('pinia', () => ({ storeToRefs: store => store }))
vi.mock('~/composables/useMattersApi', () => ({ useMattersApi: () => state.api }))
const source = { username: 'room', anchor: 'db:t:1', text: '周五交材料', sender: 'Alice', create_time: 100, render_type: 'text' }
const item = (extra = {}) => ({ id: 'm1', account: 'a', title: '交材料', source, status: 'todo', due_at: 300, reminder_enabled: true, revision: 1, reminder_revision: 1, notified_revision: null, history: [{ kind: 'created', at: 100, due_at: 300 }], ...extra })
let wrapper, navigation
const button = text => wrapper.findAll('button').find(el => el.text() === text)
beforeEach(() => {
  state.account = { selectedAccount: ref('a'), accounts:['a','b'], chatAccounts:['a','b'], revisions:ref({}), error: '', ensureLoaded: vi.fn(), setSelectedAccount: vi.fn(value => { state.account.selectedAccount.value = value }) }
  state.privacy = { privacyMode: ref(false) }
  state.api = { source: vi.fn(async () => source), list: vi.fn(async () => [item()]), create: vi.fn(async body => item()), update: vi.fn(async (a, id, fields) => item(fields)), reschedule: vi.fn(async () => item({ due_at: 600, revision: 2 })) }
  navigation = ref(null)
  vi.stubGlobal('useHead', vi.fn())
  vi.stubGlobal('useState', () => navigation)
  vi.stubGlobal('navigateTo', vi.fn(async () => true))
})
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it.each(['page', 'dialog'])('masks private matter %s failures and restores visibility when privacy mode is off', async surface => {
  const detail = '无法读取 C:/private/account-a/周五合同资料'
  state.privacy.privacyMode.value = true
  if (surface === 'page') {
    state.api.list.mockRejectedValue(new Error(detail))
    wrapper = mount(MattersPage)
  } else {
    state.api.source.mockRejectedValue(new Error(detail))
    wrapper = mount(MatterSaveDialog, { props: { account: 'a', source } })
  }
  await flushPromises()
  const alert = wrapper.get('[role=alert]')
  expect(alert.text()).toContain(detail)
  expect(alert.element.closest('.privacy-blur')).not.toBeNull()
  state.privacy.privacyMode.value = false; await flushPromises()
  expect(alert.element.closest('.privacy-blur')).toBeNull()
})

it('shows authoritative source and requires a deadline before reminder creation', async () => {
  wrapper = mount(MatterSaveDialog, { props: { account: 'a', source: { username: 'room', anchor: 'db:t:1' }, title: '交材料' } })
  await flushPromises()
  expect(wrapper.find('blockquote').text()).toBe('周五交材料')
  await wrapper.find('input[type=checkbox]').setValue(true)
  expect(button('保存事项').attributes('disabled')).toBeDefined()
  await wrapper.find('input[type=datetime-local]').setValue('2026-10-16T15:00')
  await wrapper.find('form').trigger('submit'); await flushPromises()
  expect(state.api.create).toHaveBeenCalledWith('a', { title: '交材料', source, due_at: new Date('2026-10-16T15:00').getTime() / 1000, reminder_enabled: true })
  expect(wrapper.emitted('saved')).toHaveLength(1)
})

it('requires choosing a matter and explicitly confirms a source-backed reschedule', async () => {
  wrapper = mount(MatterSaveDialog, { props: { account: 'a', source } })
  await flushPromises()
  await wrapper.find('select').setValue('reschedule')
  expect(button('确认更新期限').attributes('disabled')).toBeDefined()
  await wrapper.findAll('select')[1].setValue('m1')
  await wrapper.find('input[type=datetime-local]').setValue('2026-10-20T12:00')
  await wrapper.find('form').trigger('submit'); await flushPromises()
  expect(state.api.reschedule).toHaveBeenCalledWith('a', 'm1', { revision: 1, source, confirmed: true, due_at: new Date('2026-10-20T12:00').getTime() / 1000 })
})

it('does not expose stale source or emit an old-account save after switching account', async () => {
  let finish
  state.api.create.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  wrapper = mount(MatterSaveDialog, { props: { account: 'a', source } })
  await flushPromises()
  await wrapper.find('form').trigger('submit')
  state.api.source.mockResolvedValue({ ...source, text: '账号B原文' })
  await wrapper.setProps({ account: 'b' }); await flushPromises()
  finish(item()); await flushPromises()
  expect(wrapper.find('blockquote').text()).toBe('账号B原文')
  expect(wrapper.emitted('saved')).toBeUndefined()
})

it('updates status with revision and stops UI reminder controls after completion', async () => {
  wrapper = mount(MattersPage)
  await flushPromises()
  await wrapper.find('.matter-row').trigger('click')
  await button('标记完成').trigger('click'); await flushPromises()
  expect(state.api.update).toHaveBeenCalledWith('a', 'm1', { revision: 1, status: 'done' })
  expect(wrapper.text()).toContain('此事项已完成，后续提醒已停止')
  expect(wrapper.find('input[type=checkbox]').attributes('disabled')).toBeDefined()
})

it('isolates page requests across account switches and focuses matter notifications', async () => {
  let finish
  state.api.list.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  wrapper = mount(MattersPage)
  navigation.value = { account: 'b', matter_id: 'm2' }
  state.api.list.mockResolvedValue([item({ id: 'm2', account: 'b', title: 'B事项' })])
  await flushPromises()
  finish([item({ title: 'A秘密' })]); await flushPromises()
  expect(state.account.setSelectedAccount).toHaveBeenCalledWith('b')
  expect(wrapper.find('h2').text()).toBe('B事项')
  expect(wrapper.text()).not.toContain('A秘密')
})

it('keeps source failures visible and disables saving', async () => {
  state.api.source.mockRejectedValue({ data: { detail: '原消息已变化' } })
  wrapper = mount(MatterSaveDialog, { props: { account: 'a', source } })
  await flushPromises()
  expect(wrapper.find('[role=alert]').text()).toContain('原消息已变化')
  expect(button('保存事项').attributes('disabled')).toBeDefined()
})

it('navigates original messages as source targets and clears rejected navigation', async () => {
  wrapper = mount(MattersPage)
  await flushPromises()
  await wrapper.find('.matter-row').trigger('click')
  await button('打开原消息').trigger('click'); await flushPromises()
  expect(navigation.value).toEqual({ kind: 'source', account: 'a', username: 'room', anchor: 'db:t:1' })
  navigateTo.mockResolvedValue(false)
  await button('打开原消息').trigger('click'); await flushPromises()
  expect(navigation.value).toBeNull()
  expect(wrapper.find('[role=alert]').text()).toContain('未能打开原消息')
})

it('connects an extracted todo to its actual summary source', async () => {
  wrapper = mount(AiSummaryResult, { props: { account: 'a', sources: [{ ...source, source: 's1' }],
    summary: { overview: '讨论提交材料', todos: [{ text: '交材料', sources: ['s1'] }] } },
    global: { stubs: { MatterSaveDialog: { name: 'MatterSaveDialog', props: ['account', 'source', 'title'], template: '<div />' } } } })
  await button('保存事项').trigger('click')
  expect(wrapper.findComponent({ name: 'MatterSaveDialog' }).props()).toMatchObject({ account: 'a', source, title: '交材料' })
})

it('reads restored-only matters, refreshes them and requires real chat data for original source actions', async () => {
  state.account.selectedAccount.value='zip-only'; state.account.accounts=['zip-only']; state.account.chatAccounts=[];
  wrapper=mount(MattersPage); await flushPromises();
  expect(state.api.list).toHaveBeenCalledWith('zip-only');
  expect(wrapper.get('select[aria-label="应用资料账号"]').element.value).toBe('zip-only');
  await wrapper.find('.matter-row').trigger('click'); await button('打开原消息').trigger('click');
  expect(navigateTo).not.toHaveBeenCalled(); expect(wrapper.get('[role="alert"]').text()).toContain('导入对应账号的聊天数据');
  expect(button('依据此原文确认期限').element.disabled).toBe(true);
  state.api.list.mockResolvedValue([item(),item({id:'restored',title:'新恢复事项'})]);
  state.account.revisions.value={'zip-only':1}; await flushPromises(); expect(wrapper.text()).toContain('新恢复事项');
});
