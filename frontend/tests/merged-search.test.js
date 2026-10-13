import { mount, flushPromises } from '@vue/test-utils';
import { computed, reactive, ref, watch } from 'vue';
import { getActivePinia } from 'pinia';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import SearchPage from '../pages/search.vue';
import AttachmentsRedirect from '../pages/attachments.vue';
import { useApplicationAccountsStore } from '../stores/applicationAccounts';
import { useChatAccountsStore } from '../stores/chatAccounts';

vi.mock('~/composables/useSettingsDialog', () => ({ useSettingsDialog: () => ({ openLocalAcceleration: vi.fn() }) }));

let wrapper, route, accounts, chat, fetch, extracted;
const navigation = new Map();
const attachment = () => ({ id: 'db:messages:1', username: 'room', name: '合同付款.txt', kind: 'file', conversation_name: '项目组', sender: 'alice', sender_name: 'Alice', create_time: 10, size: 20, extraction: { status: 'not_extracted', text_excerpt: '', text_characters: 0, message: '' } });
const attachmentPage = (items = [attachment()]) => ({ status: 'success', items, coverage: { message: '已有附件文字', total_files: 1, total_images: 0, complete: 0, partial: 0 }, has_more: false, next_offset: 1 });
const sourcePage = (query, items = []) => ({ account: query.account, query: query.q, source: query.source, status: 'ready', items, has_more: false, next_offset: items.length, coverage: { available: 1, searched: 1, message: '当前搜索覆盖说明' }, elapsed_ms: 1 });
const sourceItem = title => ({ id: 'db:messages:1', source: 'documents', title, snippet: '合同 付款 新提取正文', matched_fields: ['extracted_text'], timestamp: 10, username: 'room', anchor: 'db:messages:1', conversation_name: '项目组', sender: 'alice', sender_name: 'Alice' });
const calls = url => fetch.mock.calls.filter(([path]) => path === url);
const button = text => wrapper.findAll('button').find(node => node.text() === text);
const switchView = async text => { await button(text).trigger('click'); await flushPromises(); };
const submit = async () => { await wrapper.get('form').trigger('submit'); await flushPromises(); };
const open = async () => { wrapper = mount(SearchPage, { global: { plugins: [getActivePinia()] } }); await flushPromises(); };

beforeEach(() => {
  extracted = false; navigation.clear();
  route = reactive({ path: '/search', query: {} });
  vi.stubGlobal('computed', computed); vi.stubGlobal('watch', watch);
  vi.stubGlobal('useApiBase', () => '/api'); vi.stubGlobal('useRoute', () => route);
  vi.stubGlobal('useHead', vi.fn()); vi.stubGlobal('definePageMeta', vi.fn());
  vi.stubGlobal('useState', key => { if (!navigation.has(key)) navigation.set(key, ref(null)); return navigation.get(key); });
  vi.stubGlobal('navigateTo', vi.fn(async target => { if (typeof target === 'object' && target.path === '/search') route.query = target.query; return true; }));
  fetch = vi.fn(async (url, { query } = {}) => {
    if (url === '/api/library/accounts') return { accounts: ['account-a', 'account-b', 'restored-only'], chat_accounts: ['account-a', 'account-b'] };
    if (url === '/api/search') return sourcePage(query, extracted && query.source === 'documents' ? [sourceItem('刚刚提取的正文')] : []);
    if (url === '/api/attachments') return attachmentPage();
    if (url === '/api/attachments/filters') return { status: 'success', conversations: [{ value: 'room', label: '项目组' }], senders: [{ value: 'alice', label: 'Alice' }] };
    if (url === '/api/attachments/extract') { extracted = true; return { status: 'complete', complete: true, extraction_method: 'document_text', text_characters: 12, text_excerpt: '合同 付款 新提取正文' }; }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('$fetch', fetch);
  chat = useChatAccountsStore(); chat.setSelectedAccount('account-a');
  accounts = useApplicationAccountsStore();
});
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('mounts both real panels with the same account, terms and dates without changing the chat account', async () => {
  await open();
  await wrapper.get('.search-account select').setValue('account-b');
  await wrapper.get('input[type=search]').setValue('合同 付款');
  const dates = wrapper.findAll('input[type=date]'); await dates[0].setValue('2026-10-01'); await dates[1].setValue('2026-10-02');
  await submit();
  const expected = { account: 'account-b', q: '合同 付款', start_time: new Date('2026-10-01T00:00:00').getTime() / 1000, end_time: new Date('2026-10-02T23:59:59').getTime() / 1000 };
  expect(calls('/api/search')).toHaveLength(4);
  for (const [, options] of calls('/api/search')) expect(options.query).toMatchObject(expected);
  await switchView('附件');
  expect(wrapper.find('.attachment-browser').exists()).toBe(true);
  expect(wrapper.find('.content-search-results').exists()).toBe(false);
  expect(calls('/api/attachments').at(-1)[1].query).toMatchObject(expected);
  expect(calls('/api/attachments/filters').at(-1)[1].query.account).toBe('account-b');
  expect(chat.selectedAccount).toBe('account-a');
  await switchView('全部内容');
  expect(wrapper.get('input[type=search]').element.value).toBe('合同 付款');
  expect(wrapper.findAll('input[type=date]').map(node => node.element.value)).toEqual(['2026-10-01', '2026-10-02']);
  expect(wrapper.findAll('.search-result')).toHaveLength(0);
  await submit();
  expect(calls('/api/search')).toHaveLength(8);
  expect(calls('/api/search').at(-1)[1].query).toMatchObject(expected);
  expect(chat.selectedAccount).toBe('account-a');
});

it('aborts both real panels on mode changes and ignores responses from the unmounted mode', async () => {
  const original = fetch.getMockImplementation(); const pending = [];
  fetch.mockImplementation((url, options) => {
    if (url === '/api/search' || url === '/api/attachments' || url === '/api/attachments/filters') return new Promise(resolve => pending.push({ url, options, resolve }));
    return original(url, options);
  });
  await open(); await wrapper.get('input[type=search]').setValue('合同'); await submit();
  expect(pending).toHaveLength(4);
  await switchView('附件');
  expect(pending.slice(0, 4).every(request => request.options.signal.aborted)).toBe(true);
  expect(pending).toHaveLength(6);
  await switchView('全部内容');
  expect(pending.every(request => request.options.signal.aborted)).toBe(true);
  for (const request of pending) request.resolve(request.url === '/api/search' ? sourcePage(request.options.query, [sourceItem('旧模式秘密')]) : request.url === '/api/attachments' ? attachmentPage([{ ...attachment(), name: '旧附件秘密' }]) : { status: 'success', conversations: [], senders: [] });
  await flushPromises();
  expect(wrapper.text()).not.toContain('旧模式秘密'); expect(wrapper.text()).not.toContain('旧附件秘密');
  expect(wrapper.findAll('.search-result')).toHaveLength(0);
});

it('shows newly extracted text only after a fresh all-content search with the preserved conditions', async () => {
  await open(); await wrapper.get('input[type=search]').setValue('合同 付款'); await submit();
  expect(wrapper.get('[data-source=documents]').text()).toContain('没有匹配结果');
  await switchView('附件');
  await button('提取当前页文件正文').trigger('click'); await flushPromises();
  expect(calls('/api/attachments/extract')).toHaveLength(1);
  expect(calls('/api/attachments/extract')[0][1]).toMatchObject({ method: 'POST', query: { account: 'account-a' }, body: { username: 'room', anchor: 'db:messages:1' } });
  await switchView('全部内容');
  expect(wrapper.find('[data-source=documents]').exists()).toBe(false);
  expect(calls('/api/search')).toHaveLength(4);
  await submit();
  expect(calls('/api/search')).toHaveLength(8);
  expect(wrapper.get('[data-source=documents]').text()).toContain('刚刚提取的正文');
});

it('keeps restored-only accounts out of attachment APIs and keeps their notes searchable', async () => {
  await open(); await wrapper.get('.search-account select').setValue('restored-only');
  await wrapper.get('input[type=search]').setValue('合同'); await switchView('附件');
  expect(wrapper.text()).toContain('该账号尚未导入聊天数据库');
  expect(wrapper.find('.attachment-browser').exists()).toBe(false);
  expect(calls('/api/attachments')).toHaveLength(0); expect(calls('/api/attachments/filters')).toHaveLength(0);
  expect(wrapper.get('button[type=submit]').element.disabled).toBe(true);
  await switchView('全部内容'); await submit();
  expect(calls('/api/search').every(([, options]) => options.query.account === 'restored-only')).toBe(true);
  expect(chat.selectedAccount).toBe('account-a');
});

it('aborts attachment extraction on account changes and does not publish the old completion', async () => {
  let resolveExtraction;
  const original = fetch.getMockImplementation();
  fetch.mockImplementation((url, options) => url === '/api/attachments/extract' ? new Promise(resolve => { resolveExtraction = resolve; }) : original(url, options));
  route.query = { view: 'attachments' }; await open();
  await button('提取当前页文件正文').trigger('click'); await flushPromises();
  const signal = calls('/api/attachments/extract')[0][1].signal;
  await wrapper.get('.search-account select').setValue('account-b'); await flushPromises();
  expect(signal.aborted).toBe(true);
  expect(calls('/api/attachments').at(-1)[1].query.account).toBe('account-b');
  resolveExtraction({ status: 'complete', text_excerpt: '旧账号提取秘密', text_characters: 10 }); await flushPromises();
  expect(wrapper.text()).not.toContain('旧账号提取秘密');
  expect(wrapper.text()).not.toContain('处理 1 项');
  expect(chat.selectedAccount).toBe('account-a');
});

it.each([false, new Error('切换请求失败')])('preserves the active view and search when navigation fails with %s', async failure => {
  await open(); await wrapper.get('input[type=search]').setValue('合同'); await submit();
  if (failure instanceof Error) navigateTo.mockRejectedValueOnce(failure); else navigateTo.mockResolvedValueOnce(failure);
  await switchView('附件');
  expect(wrapper.find('.content-search-results').exists()).toBe(true);
  expect(wrapper.findAll('[data-source]')).toHaveLength(4);
  expect(wrapper.get('input[type=search]').element.value).toBe('合同');
  expect(wrapper.get('[role=alert]').text()).toContain(failure instanceof Error ? failure.message : '未能切换搜索范围');
  expect(calls('/api/attachments')).toHaveLength(0);
});

it('redirects the legacy attachments route into the combined search attachment view', () => {
  wrapper = mount(AttachmentsRedirect, { global: { config: { warnHandler: () => {} } } });
  expect(definePageMeta).toHaveBeenCalledWith({ redirect: '/search?view=attachments' });
});

it.each(['restored-only', ''])('rejects submit at the form boundary when its account has no active panel: %s', async account => {
  await open();
  accounts.setSelectedAccount(account); route.query = { view: 'attachments' }; await flushPromises();
  await submit();
  expect(wrapper.get('[role=alert]').text()).toContain(account ? '尚未导入聊天数据库' : '请选择');
  expect(calls('/api/attachments')).toHaveLength(0); expect(calls('/api/search')).toHaveLength(0);
});
