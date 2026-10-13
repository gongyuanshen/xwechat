import { effectScope, nextTick, ref } from 'vue';
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { comparisonWindows, useTimeMachine } from '../composables/useTimeMachine';
import TimeMachineMessage from '../components/TimeMachineMessage.vue';
import TimeMachinePage from '../pages/time-machine.vue';

let pageAccounts;
vi.mock('pinia', () => ({ storeToRefs: store => store }));
vi.mock('~/stores/chatAccounts', () => ({ useChatAccountsStore: () => pageAccounts }));
vi.mock('~/stores/privacy', () => ({ usePrivacyStore: () => ({ privacyMode: ref(false) }) }));

let scope, wrapper, fetcher;
const entry = (id, owner = 'a') => ({ id: `message_0:Msg_test:${id}`, username: 'friend', conversation_name: '朋友',
  sender_name: '朋友', create_time: 1791849600, kind: 'text', content: `${owner} 消息 ${id}`, title: '', quote_content: '', is_sent: false });
const catalog = (account = 'a') => ({ account, revision: 'revision-1', built_at: 1791849600, up_to_date: true, timezone: '中国标准时间', years: [2026], conversations: [{ username: 'friend', name: '朋友' }] });
const page = (items, has_more = false) => ({ account: 'a', revision: 'revision-1', items, has_more, next_offset: has_more ? items.length : null });
beforeEach(() => {
  vi.stubGlobal('useApiBase', () => '/api');
  fetcher = vi.fn(async url => url.endsWith('/catalog') ? catalog() : page([entry(1)]));
  vi.stubGlobal('$fetch', fetcher);
});
afterEach(() => { scope?.stop(); wrapper?.unmount(); scope = null; wrapper = null; vi.unstubAllGlobals(); });
async function start() {
  const account = ref('a'), filters = ref({ mode: 'day', day: '2026-10-13', username: '', start: '2026-01-01', end: '2026-12-31' });
  scope = effectScope();
  const state = scope.run(() => useTimeMachine(account, filters));
  await flushPromises();
  return { account, filters, ...state };
}

it('同日比较保留全部年份，非闰年明确标记无该日期，不滚动到三月', () => {
  const windows = comparisonWindows({ mode: 'day', day: '2024-02-29' }, [2026, 2025, 2024]);
  expect(windows.map(w => [w.start, w.missingDate])).toEqual([['2026-02-29', true], ['2025-02-29', true], ['2024-02-29', false]]);
});

it('跨年范围按真实首尾日分列且拒绝反向日期', () => {
  expect(comparisonWindows({ mode: 'range', start: '2024-12-20', end: '2026-01-04' }, [2026, 2025, 2024, 2023]))
    .toEqual([{ year: 2026, start: '2026-01-01', end: '2026-01-04', missingDate: false },
      { year: 2025, start: '2025-01-01', end: '2025-12-31', missingDate: false },
      { year: 2024, start: '2024-12-20', end: '2024-12-31', missingDate: false }]);
  expect(() => comparisonWindows({ mode: 'range', start: '2026-01-01', end: '2025-01-01' }, [])).toThrow('开始日期不能晚于结束日期');
});

it('初页、续页使用同一索引版本和查询范围，失败保留已显示原文且只在点击后重试', async () => {
  fetcher.mockImplementation(async url => url.endsWith('/catalog') ? catalog() : page([entry(1)], true));
  const state = await start(), column = state.columns.value[0];
  expect(column.items).toHaveLength(1);
  state.filters.value.username = 'edited-but-not-applied';
  expect(state.applied.value.username).toBe('');
  expect(state.applied.value.name).toBe('全部普通聊天');
  fetcher.mockRejectedValueOnce(new Error('本地索引已更新'));
  await state.loadColumn(column);
  expect(column.items).toHaveLength(1);
  expect(column.error).toBe('本地索引已更新');
  expect(fetcher.mock.calls.at(-1)[1].query).toMatchObject({ username: '', revision: 'revision-1', offset: 1 });
  expect(fetcher.mock.calls.at(-1)[1].retry).toBe(0);
  fetcher.mockResolvedValueOnce(page([entry(2)]));
  await state.loadColumn(column);
  expect(column.items.map(item => item.id)).toEqual([entry(1).id, entry(2).id]);
  expect(column.hasMore).toBe(false);
});

it('切换账号立即清空旧记录并取消旧请求，迟到响应不能带回私密历史', async () => {
  let resolveOld;
  fetcher.mockImplementation(async (url, options) => {
    if (url.endsWith('/catalog')) return catalog(options.query.account);
    if (options.query.account === 'a') return new Promise(resolve => { resolveOld = resolve; });
    return { ...page([entry(2, 'b')]), account: 'b' };
  });
  const state = await start();
  const oldSignal = fetcher.mock.calls.find(([url]) => url.endsWith('/messages'))[1].signal;
  state.filters.value.username = 'friend'; state.account.value = 'b';
  await nextTick();
  expect(state.filters.value.username).toBe('');
  expect(oldSignal.aborted).toBe(true);
  await flushPromises();
  resolveOld(page([entry(1, 'a')]));
  await flushPromises();
  expect(state.columns.value[0].items.map(item => item.content)).toEqual(['b 消息 2']);
});

it('重新回看使同账号旧日期请求失效，错误和索引准备状态不显示为空记录', async () => {
  let resolveOld;
  fetcher.mockImplementation(async url => url.endsWith('/catalog') ? catalog() : new Promise(resolve => { resolveOld = resolve; }));
  const state = await start();
  state.filters.value.day = '2026-10-14';
  fetcher.mockRejectedValueOnce({ data: { detail: { code: 'index_building', message: '正在建立本地索引' } }, message: '409' });
  await state.explore();
  resolveOld(page([entry(1)])); await flushPromises();
  expect(state.columns.value).toEqual([]);
  expect(state.error.value).toBe('正在建立本地索引');
});

it('不为不存在的闰日请求消息，其他年份照常加载', async () => {
  fetcher.mockImplementation(async url => url.endsWith('/catalog') ? { ...catalog(), years: [2025, 2024] } : page([entry(1)]));
  const state = await start();
  state.filters.value.day = '2024-02-29'; fetcher.mockClear();
  await state.explore();
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/messages')).map(([, options]) => options.query.start)).toEqual(['2024-02-29']);
  expect(state.columns.value[0].missingDate).toBe(true);
});

it('索引更新必须显式点击，并展示后台构建失败', async () => {
  const state = await start();
  expect(fetcher.mock.calls.some(([url]) => url.endsWith('/refresh-index'))).toBe(false);
  fetcher.mockResolvedValueOnce({ status: 'success', index: { build: { status: 'error', error: '磁盘写入失败' } } });
  await state.refreshIndex();
  expect(state.error.value).toBe('磁盘写入失败');
  expect(state.columns.value[0].items).toHaveLength(1);
});

it('图片加载失败有明确提示，原消息按钮传递真实定位，隐私遮罩覆盖内容', async () => {
  const item = { ...entry(3), kind: 'image', content: '[图片]', is_sent: true };
  wrapper = mount(TimeMachineMessage, { props: { item, imageUrl: '/api/attachments/content?account=a', privacy: true } });
  expect(wrapper.find('article').classes()).toContain('privacy-blur');
  expect(wrapper.find('strong').text()).toBe('我');
  await wrapper.find('img').trigger('error');
  expect(wrapper.find('img').exists()).toBe(false);
  expect(wrapper.text()).toContain('图片暂时无法读取');
  await wrapper.find('button').trigger('click');
  expect(wrapper.emitted('locate')[0][0]).toEqual(item);
});

it('原消息导航被拒绝时撤回当前目标并显示错误，不留下待消费的旧跳转', async () => {
  const navigation = ref(null);
  pageAccounts = { selectedAccount: ref('a'), ensureLoaded: vi.fn(), error: '' };
  vi.stubGlobal('useHead', vi.fn());
  vi.stubGlobal('useState', () => navigation);
  vi.stubGlobal('useAttachmentsApi', () => ({ contentUrl: () => '/api/attachments/content' }));
  vi.stubGlobal('navigateTo', vi.fn(async () => false));
  wrapper = mount(TimeMachinePage);
  await flushPromises();
  await wrapper.find('.time-message button').trigger('click');
  await flushPromises();
  expect(navigation.value).toBeNull();
  expect(wrapper.find('[role="alert"]').text()).toContain('未能打开聊天原消息');
});

it('防撤回保留的原文明确标记已撤回', () => {
  wrapper = mount(TimeMachineMessage, { props: { item: { ...entry(1), is_revoked: true, revoke_time: 1791849700 }, imageUrl: '/api/attachments/content' } });
  expect(wrapper.find('.time-revoked').text()).toBe('已撤回');
  expect(wrapper.find('.time-message-content').text()).toBe(entry(1).content);
});
