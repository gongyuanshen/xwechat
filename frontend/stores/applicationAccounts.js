import { defineStore } from 'pinia';
import { ref, watch } from 'vue';
import { useChatAccountsStore } from './chatAccounts';

export const useApplicationAccountsStore = defineStore('applicationAccounts', () => {
  const chat = useChatAccountsStore(), apiBase = useApiBase();
  const accounts = ref([]), chatAccounts = ref([]), selectedAccount = ref('');
  const error = ref(''), loading = ref(false), loaded = ref(false), revisions = ref({});
  let requestId = 0, pending, explicitlySelected = false;
  const setSelectedAccount = account => { selectedAccount.value = account; explicitlySelected = true; };
  const notifyRestored = account => { revisions.value = { ...revisions.value, [account]: (revisions.value[account] || 0) + 1 }; };
  watch(() => chat.selectedAccount, account => {
    // Initial chat hydration may finish after an explicit restored-account choice.
    // Later sidebar selections still follow the existing shared-account behavior.
    if (explicitlySelected && chat.loading && !chat.loaded) return;
    if (chatAccounts.value.includes(account)) selectedAccount.value = account;
  }, { flush: 'sync' });
  const ensureLoaded = ({ force = false } = {}) => {
    if (!force && pending) return pending;
    if (!force && loaded.value) return Promise.resolve(true);
    const token = ++requestId;
    loading.value = true; error.value = '';
    pending = (async () => {
      try {
        const result = await $fetch(`${apiBase}/library/accounts`, { retry: 0 });
        if (token !== requestId) return false;
        accounts.value = result.accounts; chatAccounts.value = result.chat_accounts;
        if (!accounts.value.includes(selectedAccount.value)) {
          selectedAccount.value = chatAccounts.value.includes(chat.selectedAccount) ? chat.selectedAccount : accounts.value[0] || '';
        }
        loaded.value = true;
        return true;
      } catch (cause) {
        if (token === requestId) error.value = typeof cause.data?.detail === 'string' ? cause.data.detail : cause.message;
        return false;
      } finally {
        if (token === requestId) { loading.value = false; pending = null; }
      }
    })();
    return pending;
  };
  return { accounts, chatAccounts, selectedAccount, error, loading, loaded, revisions, ensureLoaded, setSelectedAccount, notifyRestored };
});
