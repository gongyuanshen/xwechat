<template>
  <main class="theme-scope h-screen flex flex-col min-h-0 min-w-0 overflow-hidden">
    <div class="agent-account-toolbar"><label>应用资料账号<select aria-label="应用资料账号" :value="selectedAccount" :disabled="accounts.loading" :class="{ 'privacy-blur': privacyMode }" @change="accounts.setSelectedAccount($event.target.value)"><option v-if="!accounts.accounts.length" value="">暂无资料账号</option><option v-for="account in accounts.accounts" :key="account" :value="account">{{ account }}</option></select></label><button type="button" :disabled="accounts.loading" @click="loadAccounts(true)">刷新账号</button></div>
    <ErrorNotice v-if="accounts.error || targetError" :message="accounts.error || targetError" class="m-4" />
    <p v-if="accounts.loading && !selectedAccount" class="p-4 text-sm text-gray-500" role="status">正在加载账号…</p>
    <ChatAgentPanel v-else-if="selectedAccount" presentation="page" :account="selectedAccount" :can-read-chats="canReadChats" :library-target="libraryTarget" :prepare-source="prepareSource" :locate-source="locateSource" />
    <p v-else class="p-4 text-sm text-gray-500">请导入微信账号，或在设置的“资料备份”中恢复应用资料。</p>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { isNavigationFailure } from 'vue-router'
import ChatAgentPanel from '~/components/chat/ChatAgentPanel.vue'
import { useApplicationAccountsStore } from '~/stores/applicationAccounts'
import { usePrivacyStore } from '~/stores/privacy'
import { useApi } from '~/composables/useApi'
import { createAnchorContextCache } from '~/utils/anchorContextCache'

useHead({ title: 'AI 助手 - xwechat' })
const accounts = useApplicationAccountsStore()
const { selectedAccount } = storeToRefs(accounts)
const { privacyMode } = storeToRefs(usePrivacyStore())
const canReadChats = computed(() => accounts.chatAccounts.includes(selectedAccount.value))
const libraryTarget = ref(null), targetError = ref('')
const libraryNavigation = useState('agent-library-navigation-target', () => null)
const api = useApi()
const navigation = useState('ai-navigation-target', () => null)
const sourceCache = createAnchorContextCache(async params => {
  const response = await api.getChatMessagesAround(params)
  if (!response?.messages?.length) throw new Error('未找到原消息，记录可能已更新或移除')
  return response
})
const sourceTarget = source => {
  if (!selectedAccount.value) throw new Error('请先选择聊天账号')
  if (!canReadChats.value) throw new Error('请先导入对应账号的聊天数据，再打开原消息。')
  if (source?.kind === 'library') throw new Error('专题引用来自保存版本，请在材料出处中查看。')
  if (!source?.username || !source?.anchor) throw new Error('该来源缺少定位信息')
  return { ...source, kind: 'source', account: selectedAccount.value }
}
const prepareSource = async source => {
  const target = sourceTarget(source)
  return sourceCache.read({ account: target.account, username: target.username, anchor_id: target.anchor, before: 35, after: 35, source: 'auto', ai_diagnostic: true })
}
const locateSource = async source => {
  navigation.value = sourceTarget(source)
  const target = navigation.value
  try {
    const result = await navigateTo('/chat')
    if (result === false || isNavigationFailure(result)) throw new Error('未能打开聊天原消息，请重试定位')
    return true
  } catch (error) {
    if (navigation.value === target) navigation.value = null
    throw error
  }
}
const consumeLibraryTarget = () => {
  const target = libraryNavigation.value
  if (!target?.folder_id || !target?.version_id) return
  targetError.value = ''
  if (!accounts.accounts.includes(target.account)) { targetError.value = '专题所属账号不可用，请刷新账号后重试。'; return }
  libraryNavigation.value = null
  accounts.setSelectedAccount(target.account)
  libraryTarget.value = target
}
const loadAccounts = async (force = false) => { if (await accounts.ensureLoaded({ force })) consumeLibraryTarget() }
watch(libraryNavigation, consumeLibraryTarget)
watch(selectedAccount, () => { sourceCache.clear(); libraryTarget.value = null; targetError.value = '' }, { flush: 'sync' })
onUnmounted(() => sourceCache.clear())
onMounted(() => loadAccounts())
</script>

<style scoped>
.agent-account-toolbar { display: flex; align-items: center; gap: 12px; padding: 8px 16px; border-bottom: 1px solid var(--app-border); font-size: 12px; }
.agent-account-toolbar label { display: flex; align-items: center; gap: 8px; min-width: 0; }
.agent-account-toolbar select { max-width: 300px; min-width: 0; }
.agent-account-toolbar select, .agent-account-toolbar button { color: inherit; font: inherit; background: var(--app-surface-bg); border: 1px solid var(--app-border); border-radius: 6px; padding: 5px 8px; }
.agent-account-toolbar button { white-space: nowrap; }
.agent-account-toolbar :focus-visible { outline: 2px solid var(--app-accent); outline-offset: 2px; }
</style>
