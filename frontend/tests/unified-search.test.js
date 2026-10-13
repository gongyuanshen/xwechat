import { mount, flushPromises } from '@vue/test-utils';
import { reactive, ref } from 'vue';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import SearchPage from '../pages/search.vue';

const state = vi.hoisted(() => ({ accounts: null, privacy: null }));
vi.mock('~/stores/applicationAccounts', () => ({ useApplicationAccountsStore: () => state.accounts }));
vi.mock('~/stores/privacy', () => ({ usePrivacyStore: () => state.privacy }));
vi.mock('pinia', () => ({ storeToRefs: store => store }));
let wrapper, navigation, fetch, route;
const sources = ['chat', 'documents', 'ocr', 'notes'];
const row = (source, id = 'one') => ({ id, source, title: `${source}标题`, snippet: '<img src=x onerror=alert(1)> 合同 付款', matched_fields: ['content'], timestamp: 10, ...(source === 'notes' ? { library_item_id: id, folder_id: 'folder', folder_name: '项目资料', item_kind: 'report' } : { username: 'room', anchor: `db:t:${id}`, conversation_name: '项目群', sender: 'alice', sender_name: 'Alice' }) });
const result = (source, extra = {}) => ({ account: 'a', query: '合同', source, status: 'ready', items: [row(source)], has_more: false, next_offset: 1, coverage: { available: 8, searched: 8, message: '当前来源覆盖说明' }, elapsed_ms: 3.4, ...extra });
const group = source => wrapper.get(`[data-source="${source}"]`);
const submit = async (q = '合同') => { await wrapper.get('input[type=search]').setValue(q); await wrapper.get('form').trigger('submit'); await flushPromises(); };
const button = (scope, text) => scope.findAll('button').find(node => node.text() === text);
beforeEach(() => {
  state.accounts = { selectedAccount: ref('a'), revisions: ref({}), accounts: ['a', 'b'], chatAccounts: ['a'], error: '', loading: false, ensureLoaded: vi.fn(async () => true), setSelectedAccount: value => { state.accounts.selectedAccount.value = value; } };
  state.privacy = { privacyMode: ref(false) };
  navigation = { 'ai-navigation-target': ref(null), 'library-navigation-target': ref(null) };
  fetch = vi.fn(async (url, options) => result(options.query.source));
  vi.stubGlobal('$fetch', fetch); vi.stubGlobal('useApiBase', () => '/api');
  vi.stubGlobal('useHead', vi.fn()); vi.stubGlobal('useState', key => navigation[key]);
  route = reactive({ query: {}, path: '/search' });
  vi.stubGlobal('useRoute', () => route);
  vi.stubGlobal('navigateTo', vi.fn(async target => { if (typeof target === 'object' && target.path === '/search') route.query = target.query; return true; }));
});
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const open = async () => { wrapper = mount(SearchPage); await flushPromises(); };

it('uses one shared form and account for both views, keeping restored-only attachments unavailable', async () => {
  await open();
  await wrapper.get('input[type=search]').setValue('合同 付款');
  state.accounts.selectedAccount.value = 'b'; await flushPromises();
  await button(wrapper, '附件').trigger('click'); await flushPromises();
  expect(wrapper.findAll('input[type=search]')).toHaveLength(1);
  expect(wrapper.get('input[type=search]').element.value).toBe('合同 付款');
  expect(wrapper.text()).toContain('该账号尚未导入聊天数据库');
  expect(fetch).not.toHaveBeenCalled();
  await button(wrapper, '全部内容').trigger('click'); await flushPromises();
  await wrapper.get('form').trigger('submit'); await flushPromises();
  expect(fetch.mock.calls.every(call => call[1].query.account === 'b')).toBe(true);
});

it('queries each selected source only after submission, shows provenance and renders snippets as text', async () => {
  await open(); expect(fetch).not.toHaveBeenCalled();
  await submit('合同 付款');
  expect(fetch).toHaveBeenCalledTimes(4);
  for (const source of sources) {
    expect(fetch).toHaveBeenCalledWith('/api/search', expect.objectContaining({ query: { account: 'a', q: '合同 付款', source, offset: 0, limit: 20 }, retry: 0, signal: expect.any(AbortSignal) }));
    expect(group(source).text()).toContain(`${source}标题`);
    expect(group(source).text()).toContain('当前来源覆盖说明');
    expect(group(source).find('img').exists()).toBe(false);
  }
  expect(group('chat').text()).toContain('项目群'); expect(group('notes').text()).toContain('项目资料');
});

it('keeps failure, unavailable, indexing, unknown coverage and successful empty results distinct', async () => {
  fetch.mockImplementation(async (url, { query }) => {
    if (query.source === 'chat') throw { data: { detail: { message: '聊天索引损坏' } } };
    if (query.source === 'documents') return result('documents', { status: 'unavailable', items: [], coverage: { available: null, searched: null, message: '尚未导入聊天数据' } });
    if (query.source === 'ocr') return result('ocr', { status: 'index_building', items: [], coverage: { available: null, searched: null, message: '索引正在准备' } });
    return result('notes', { items: [] });
  });
  await open(); await submit();
  expect(group('chat').text()).toContain('聊天索引损坏'); expect(group('chat').text()).not.toContain('没有匹配');
  expect(group('documents').text()).toContain('尚未导入聊天数据'); expect(group('documents').text()).toContain('未知');
  expect(group('ocr').text()).toContain('索引正在准备'); expect(group('notes').text()).toContain('没有匹配');
});

it('appends only the requested source page and preserves loaded rows on explicit retry', async () => {
  fetch.mockImplementation(async (url, { query }) => result(query.source, query.source === 'chat' ? { has_more: true, next_offset: 20 } : {}));
  await open(); await submit();
  fetch.mockRejectedValueOnce(new Error('第二页读取失败'));
  await button(group('chat'), '加载更多').trigger('click'); await flushPromises();
  expect(group('chat').text()).toContain('chat标题'); expect(group('chat').text()).toContain('第二页读取失败');
  fetch.mockResolvedValueOnce(result('chat', { items: [row('chat', 'two')], next_offset: 21 }));
  await button(group('chat'), '重试此来源').trigger('click'); await flushPromises();
  expect(fetch.mock.calls.at(-1)[1].query.offset).toBe(20);
  expect(group('chat').findAll('.search-result')).toHaveLength(2);
  expect(group('notes').findAll('.search-result')).toHaveLength(1);
});

it.each(['query', 'source', 'account', 'revision', 'unmount'])('cancels %s changes and rejects a late prior result', async changed => {
  let resolve;
  fetch.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await open(); await submit();
  const signal = fetch.mock.calls[0][1].signal;
  if (changed === 'query') await wrapper.get('input[type=search]').setValue('新条件');
  if (changed === 'source') await wrapper.get('input[value=chat]').setValue(false);
  if (changed === 'account') state.accounts.selectedAccount.value = 'b';
  if (changed === 'revision') state.accounts.revisions.value = { a: 1 };
  if (changed === 'unmount') wrapper.unmount();
  await flushPromises();
  expect(signal.aborted).toBe(true);
  resolve(result('chat', { items: [{ ...row('chat'), title: '旧查询秘密' }] })); await flushPromises();
  if (changed !== 'unmount') { expect(wrapper.text()).not.toContain('旧查询秘密'); expect(wrapper.findAll('.search-result')).toHaveLength(0); }
});

it('allows restored-only accounts and navigates with authoritative source identities', async () => {
  state.accounts.chatAccounts = []; await open(); await submit();
  await button(group('notes'), '打开资料').trigger('click');
  expect(navigation['library-navigation-target'].value).toEqual({ account: 'a', item_id: 'one' });
  expect(navigateTo).toHaveBeenCalledWith('/library');
  await button(group('chat'), '打开原消息').trigger('click');
  expect(navigation['ai-navigation-target'].value).toEqual({ kind: 'source', origin: 'search', account: 'a', username: 'room', anchor: 'db:t:one' });
});

it('clears only a failed navigation target and displays private errors with private query and result text', async () => {
  state.privacy.privacyMode.value = true; await open(); await submit();
  navigateTo.mockRejectedValueOnce(new Error('私有来源无法打开'));
  await button(group('notes'), '打开资料').trigger('click'); await flushPromises();
  expect(navigation['library-navigation-target'].value).toBeNull();
  expect(wrapper.get('input[type=search]').classes()).toContain('privacy-blur');
  expect(group('notes').get('.search-result-copy').classes()).toContain('privacy-blur');
  expect(group('notes').get('[role=alert]').classes()).toContain('privacy-blur');
});

it('rejects empty query, excessive terms and reversed dates before any source request', async () => {
  await open(); await submit(''); expect(fetch).not.toHaveBeenCalled();
  await submit('1 2 3 4 5 6 7 8 9'); expect(fetch).not.toHaveBeenCalled();
  const dates = wrapper.findAll('input[type=date]'); await dates[0].setValue('2026-10-13'); await dates[1].setValue('2026-10-12');
  await submit(); expect(fetch).not.toHaveBeenCalled(); expect(wrapper.get('[role=alert]').text()).toContain('开始日期');
});

it('shows current extraction counts and index freshness without claiming full OCR coverage', async () => {
  fetch.mockImplementation(async (url, { query }) => result(query.source, { coverage: { available: 20, searched: 8, message: '仅查询有效缓存', index: { ready: true, upToDate: false }, counts: { complete: 5, partial: 3, no_text: 2, not_extracted: 7, stale: 3, document_text: 6, local_ocr: 4 } } }));
  await open(); await submit();
  expect(group('documents').text()).toContain('当前结果不覆盖全部最新消息');
  expect(group('documents').text()).toContain('范围内 20 项 · 可搜索内容 8 项');
  for (const text of ['完整提取 5', '部分提取 3', '未提取到文字 2', '尚未提取 7', '来源已变化 3', '文档提取 6', 'OCR 识别 4']) expect(group('documents').text()).toContain(text);
  expect(group('documents').text()).not.toContain('%');
});

it('labels revoked snapshots and every backend matched field using user-readable text', async () => {
  fetch.mockResolvedValue(result('chat', { items: [{ ...row('chat'), is_revoked: true, revoke_time: 15, matched_fields: ['url', 'quoteTitle', 'quoteContent', 'amount', 'extracted_text'] }] }));
  await open(); await submit();
  expect(group('chat').text()).toContain('已撤回');
  expect(group('chat').text()).toContain('链接、引用标题、引用正文、金额、已提取文字');
});

it('submits dates and only chosen sources while keeping source loading states independent', async () => {
  let resolve;
  fetch.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await open(); await wrapper.get('input[value=documents]').setValue(false); await wrapper.get('input[value=ocr]').setValue(false);
  const dates = wrapper.findAll('input[type=date]'); await dates[0].setValue('2026-10-01'); await dates[1].setValue('2026-10-02');
  await submit();
  expect(fetch.mock.calls.map(call => call[1].query.source)).toEqual(['chat', 'notes']);
  expect(fetch.mock.calls[0][1].query).toMatchObject({ start_time: new Date('2026-10-01T00:00:00').getTime() / 1000, end_time: new Date('2026-10-02T23:59:59').getTime() / 1000 });
  expect(group('chat').text()).toContain('正在查询'); expect(group('notes').text()).toContain('notes标题');
  resolve(result('chat')); await flushPromises();
});
