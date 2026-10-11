import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { usePrivacyStore } from '~/stores/privacy'
import AutoReplySettings from '../components/AutoReplySettings.vue'
import AutoReplyControl from '../components/chat/AutoReplyControl.vue'

vi.mock('~/stores/chatAccounts', () => ({ useChatAccountsStore: () => ({
  accounts: ['fixture-account'], selectedAccount: 'fixture-account',
  accountInfoByName: { 'fixture-account': { displayName: 'Fixture owner' } }, setSelectedAccount: vi.fn(),
}) }))

const session = { session_id: 'fixture-peer', target_name: 'Fixture friend', enabled: true, group_trigger: 'mentions' }
const configuration = { account: 'fixture-account', version: 1, profile_id: 'fixture-local', model_id: 'fixture-text', system_prompt: 'Fixture private instruction', sessions: [session] }
const failure = { code: 'FIXTURE_ERROR', detail: 'Fixture private error' }
const status = { account: 'fixture-account', state: 'off', sessions: [{ session_id: session.session_id, status: 'paused', error: failure }], error: failure }
const attempt = { id: 'fixture-attempt', account: 'fixture-account', session_id: session.session_id, target_name: session.target_name,
  draft: 'Fixture private reply', messages: [{ sender: 'Fixture sender', content: 'Fixture private trigger' }],
  evidence: { text: 'Fixture private receipt' }, error: failure, model: { profile_id: 'fixture-local' },
  status: 'needs_review', created_at: 100, submission_count: 1 }
let wrapper, request, privacy
const masked = node => !!node.element.closest('.privacy-blur')
beforeEach(() => {
  vi.useFakeTimers()
  privacy = usePrivacyStore()
  privacy.set(true)
  vi.stubGlobal('useSettingsDialog', () => ({ openDialog: vi.fn() }))
  request = vi.fn(async path => {
    if (path === '/settings') return { profiles: [] }
    if (path.startsWith('/conversations?')) return [{ username: session.session_id, name: session.target_name }]
    if (path.startsWith('/auto-reply/config?')) return structuredClone(configuration)
    if (path.startsWith('/auto-reply/status?')) return structuredClone(status)
    if (path.startsWith('/auto-reply/history?')) return { items: [structuredClone(attempt)], total: 1, page: 1, page_size: 5 }
    throw new Error(`Unexpected fixture request: ${path}`)
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.useRealTimers(); vi.unstubAllGlobals(); document.body.innerHTML = '' })

it('protects reply records and account confirmation while keeping status and actions readable', async () => {
  wrapper = mount(AutoReplySettings, { props: { account: 'fixture-account', api: { request } }, global: { stubs: { AgentModelPicker: true } } })
  await flushPromises()
  for (const selector of ['.ar-account p', '.ar-consent strong', '.ar-configuration textarea', '.ar-record-heading strong', '.ar-draft', '.ar-records .ar-warning', '.ar-records pre', '.ar-records dd:last-child']) {
    expect(wrapper.find(selector).exists(), selector).toBe(true)
    expect(masked(wrapper.find(selector)), selector).toBe(true)
  }
  expect(masked(wrapper.find('[data-testid=auto-reply-state]'))).toBe(false)
  expect(wrapper.findAll('button').every(node => !masked(node))).toBe(true)
  expect(masked(wrapper.find('.ar-records dd'))).toBe(false)
  privacy.set(false)
  await flushPromises()
  expect(wrapper.findAll('.privacy-blur')).toHaveLength(0)
  expect(wrapper.find('.ar-draft').text()).toBe(attempt.draft)
  expect(request.mock.calls.every(([, options]) => !options?.method)).toBe(true)
})

it('protects actual teleported account and session options, searches and selected names', async () => {
  wrapper = mount(AutoReplySettings, { attachTo: document.body, props: { api: { request } }, global: { stubs: { AgentModelPicker: true } } })
  await flushPromises()
  for (const label of ['自动回复数据账号', '自动回复范围', '调整会话', '记录会话筛选']) {
    const trigger = wrapper.find(`[aria-label="${label}"]`)
    await trigger.trigger('click'); await flushPromises()
    const menu = new DOMWrapper(document.body.querySelector('.ui-select-menu'))
    expect(wrapper.element.contains(menu.element)).toBe(false)
    expect(menu.findAll('.ui-select-option-copy').every(masked), label).toBe(true)
    const search = menu.find('input[type=search]')
    if (search.exists()) { await search.setValue('Fixture'); expect(masked(search)).toBe(true) }
    if (label === '自动回复数据账号') {
      expect(masked(trigger.find('span'))).toBe(true)
      expect(trigger.attributes('title')).toBeUndefined()
    }
    expect(menu.findAll('button').every(node => !masked(node))).toBe(true)
    await trigger.trigger('click'); await flushPromises()
  }
  await wrapper.find('[aria-label="调整会话"]').trigger('click'); await flushPromises()
  await new DOMWrapper(document.body.querySelector('[role=option]')).trigger('click'); await flushPromises()
  expect(masked(wrapper.find('.ar-session-top strong'))).toBe(true)
  expect(masked(wrapper.find('.ar-session-fields input'))).toBe(true)
  expect(masked(wrapper.find('.ar-session .ar-warning'))).toBe(true)
  expect(masked(wrapper.find('.ar-session-top .ar-state'))).toBe(false)
  await wrapper.find('[aria-label="自动回复数据账号"]').trigger('click'); await flushPromises()
  privacy.set(false); await flushPromises()
  expect(document.body.querySelectorAll('.privacy-blur')).toHaveLength(0)
  expect(wrapper.find('[aria-label="自动回复数据账号"]').attributes('title')).toBe('Fixture owner')
})

it('protects toolbar error content and its native tooltip without hiding the switch or state', async () => {
  wrapper = mount(AutoReplyControl, { props: { account: 'fixture-account', sessionId: session.session_id, targetName: session.target_name, api: { request } } })
  await flushPromises()
  const error = wrapper.find('[role=alert]')
  expect(masked(error)).toBe(true)
  expect(error.attributes('title')).toBeUndefined()
  expect(masked(wrapper.find('[role=status]'))).toBe(false)
  expect(masked(wrapper.find('[role=switch]'))).toBe(false)
  privacy.set(false); await flushPromises()
  expect(masked(error)).toBe(false)
  expect(error.attributes('title')).toContain(failure.detail)
})
