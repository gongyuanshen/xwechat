import { onScopeDispose, ref, watch } from 'vue';

export const localDate = (date = new Date()) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

export function comparisonWindows(filters, years) {
  if (filters.mode === 'range') {
    if (!filters.start || !filters.end || filters.start > filters.end) throw new Error('请选择有效的开始和结束日期，开始日期不能晚于结束日期。');
    return years.filter(year => year >= Number(filters.start.slice(0, 4)) && year <= Number(filters.end.slice(0, 4)))
      .map(year => ({ year, start: filters.start > `${year}-01-01` ? filters.start : `${year}-01-01`,
        end: filters.end < `${year}-12-31` ? filters.end : `${year}-12-31`, missingDate: false }));
  }
  if (!filters.day) throw new Error('请选择要对照的日期。');
  const monthDay = filters.day.slice(5), [month, day] = monthDay.split('-').map(Number);
  return years.map(year => ({ year, start: `${year}-${monthDay}`, end: `${year}-${monthDay}`,
    missingDate: new Date(year, month - 1, day).getMonth() !== month - 1 }));
}

export function timeMachineError(error) {
  const detail = error.data?.detail;
  return typeof detail === 'string' ? detail : detail?.message || error.message;
}

export function useTimeMachine(account, filters) {
  const base = `${useApiBase()}/time-machine`;
  const catalog = ref(null), columns = ref([]), applied = ref(null), error = ref(''), loading = ref(false), refreshing = ref(false), notice = ref('');
  let generation = 0, controller, activeQuery;

  function invalidate() {
    generation += 1; controller?.abort(); catalog.value = null; columns.value = []; applied.value = null; error.value = ''; notice.value = '';
    loading.value = false; refreshing.value = false;
  }

  async function loadColumn(column) {
    const token = generation, owner = account.value, query = activeQuery, signal = controller.signal;
    column.loading = true; column.error = '';
    try {
      const result = await $fetch(`${base}/messages`, { query: { account: owner, username: query.username,
        start: column.start, end: column.end, revision: catalog.value.revision, offset: column.offset, limit: 30 }, signal, retry: 0 });
      if (token !== generation || signal.aborted || owner !== account.value) return;
      if (result.account !== owner || result.revision !== catalog.value.revision) throw new Error('消息账号或索引版本不一致，请重新回看。');
      column.items.push(...result.items); column.hasMore = result.has_more; column.offset = result.next_offset;
      column.loaded = true;
    } catch (e) { if (token === generation && !signal.aborted) column.error = timeMachineError(e); }
    finally { if (token === generation) column.loading = false; }
  }

  async function explore() {
    invalidate();
    if (!account.value) return;
    controller = new AbortController();
    const signal = controller.signal, token = generation, owner = account.value;
    activeQuery = { ...filters.value };
    loading.value = true;
    try {
      // Validate dates before opening the index or starting background indexing.
      comparisonWindows(activeQuery, []);
      const result = await $fetch(`${base}/catalog`, { query: { account: owner, username: activeQuery.username }, signal, retry: 0 });
      if (token !== generation || signal.aborted || owner !== account.value) return;
      if (result.account !== owner) throw new Error('返回了其他账号的聊天目录，请重新回看。');
      catalog.value = result;
      applied.value = { ...activeQuery, name: activeQuery.username
        ? result.conversations.find(item => item.username === activeQuery.username)?.name || activeQuery.username : '全部普通聊天' };
      columns.value = comparisonWindows(activeQuery, result.years).map(window => ({ ...window, items: [], offset: 0,
        hasMore: false, error: '', loading: false, loaded: false }));
      await Promise.all(columns.value.filter(column => !column.missingDate).map(loadColumn));
    } catch (e) { if (token === generation && !signal.aborted) error.value = timeMachineError(e); }
    finally { if (token === generation) loading.value = false; }
  }

  async function refreshIndex() {
    controller?.abort(); controller = new AbortController();
    const token = ++generation, owner = account.value, signal = controller.signal;
    // Old columns stay readable while the explicitly requested rebuild runs.
    for (const column of columns.value) column.loading = false;
    loading.value = false; refreshing.value = true; error.value = ''; notice.value = '';
    try {
      const result = await $fetch(`${base}/refresh-index`, { method: 'POST', query: { account: owner }, signal, retry: 0 });
      if (token !== generation || signal.aborted) return;
      if (result.status === 'error') throw new Error(result.message || result.error);
      if (result.index?.build?.status === 'error') throw new Error(result.index.build.error);
      notice.value = '已请求更新本地索引。完成后点击“开始回看”读取新版本。';
    } catch (e) { if (token === generation && !signal.aborted) error.value = timeMachineError(e); }
    finally { if (token === generation) refreshing.value = false; }
  }

  watch(account, () => { filters.value.username = ''; void explore(); }, { immediate: true });
  onScopeDispose(invalidate);
  return { catalog, columns, applied, error, loading, refreshing, notice, explore, loadColumn, refreshIndex };
}
