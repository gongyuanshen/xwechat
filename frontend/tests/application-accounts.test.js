import { reactive } from 'vue';
import { beforeEach, expect, it, vi } from 'vitest';
import { useApplicationAccountsStore } from '../stores/applicationAccounts';
const state = vi.hoisted(() => ({ chat: null }));
vi.mock('~/stores/chatAccounts', () => ({ useChatAccountsStore: () => state.chat }));
beforeEach(() => {
  state.chat = reactive({ selectedAccount: null, accounts: [] });
  vi.stubGlobal('useApiBase', () => '/api');
  vi.stubGlobal('$fetch', vi.fn(async () => ({ accounts: ['restored'], chat_accounts: [] })));
});
it('loads restored-only accounts without inventing chat accounts and keeps explicit selection', async () => {
  const store = useApplicationAccountsStore();
  expect(await store.ensureLoaded()).toBe(true);
  expect(store.accounts).toEqual(['restored']); expect(store.selectedAccount).toBe('restored');
  expect(store.chatAccounts).toEqual([]); expect(state.chat.accounts).toEqual([]); expect(state.chat.selectedAccount).toBeNull();
  expect($fetch).toHaveBeenCalledWith('/api/library/accounts', { retry: 0 });
});
it('prefers and follows only legitimate chat selections without changing the chat store', async () => {
  state.chat.selectedAccount = 'chat-a';
  $fetch.mockResolvedValue({ accounts: ['restored', 'chat-a', 'chat-b'], chat_accounts: ['chat-a', 'chat-b'] });
  const store = useApplicationAccountsStore(); await store.ensureLoaded();
  expect(store.selectedAccount).toBe('chat-a');
  store.setSelectedAccount('restored'); await store.ensureLoaded({ force: true });
  expect(store.selectedAccount).toBe('restored'); expect(state.chat.selectedAccount).toBe('chat-a');
  state.chat.selectedAccount = 'chat-b'; expect(store.selectedAccount).toBe('chat-b');
  state.chat.selectedAccount = 'missing'; expect(store.selectedAccount).toBe('chat-b');
});
it('reports account refresh failures while preserving confirmed accounts and restore revisions', async () => {
  const store = useApplicationAccountsStore(); await store.ensureLoaded();
  store.notifyRestored('restored'); $fetch.mockRejectedValueOnce(new Error('账号目录读取失败'));
  expect(await store.ensureLoaded({ force: true })).toBe(false);
  expect(store.error).toBe('账号目录读取失败'); expect(store.accounts).toEqual(['restored']);
  expect(store.revisions.restored).toBe(1); expect($fetch).toHaveBeenCalledTimes(2);
});
it('ignores an older list response after a forced refresh', async () => {
  let finish; $fetch.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const store = useApplicationAccountsStore(); const first = store.ensureLoaded();
  await store.ensureLoaded({ force: true });
  finish({ accounts: ['obsolete'], chat_accounts: ['obsolete'] }); await first;
  expect(store.accounts).toEqual(['restored']); expect(store.selectedAccount).toBe('restored');
});
