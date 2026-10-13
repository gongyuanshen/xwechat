<template>
  <div ref="chatPageRef" class="chat-page-shell relative h-screen w-full min-w-0 flex overflow-hidden">
    <SessionListPanel :state="chatState" />

    <div class="chat-page-main flex-1 flex flex-col min-h-0 min-w-0">
      <div class="flex-1 flex min-h-0 min-w-0">
        <ConversationPane v-show="!insightsPanelOpen || !selectedContact" :state="chatState" />
        <ChatInsightsPanel v-if="insightsPanelOpen && selectedContact" :key="`${selectedAccount}:${selectedContact.username}`" :state="insightsState" :recognition="recognitionState" :contact="selectedContact" :locate-source="locateInsightSource" :privacy-mode="privacyMode" :show-settings="insightsSettingsOpen" @close="insightsPanelOpen = false" />
      </div>
    </div>

    <ResourceSidebar :state="chatState" />
    <VoiceTranscriptionSidebar :state="chatState" />
    <GroupMembersSidebar
      v-if="groupMembersSidebarOpen && selectedContact?.isGroup"
      :key="`${selectedAccount}:${selectedContact.username}`"
      :account="selectedAccount"
      :username="selectedContact.username"
      :privacy-mode="privacyMode"
      :state="chatState"
    />
    <ChatAgentPanel v-if="aiSidebarOpen" :account="selectedAccount" :contact="selectedContact" :contacts="contacts" :focus-task-id="aiFocusTaskId" :locate-source="locateAiSource" :prepare-source="prepareAiSource" :profile-state="chatState" @close="aiSidebarOpen = false" />
    <ChatOverlays :state="chatState" />
  </div>
</template>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { storeToRefs } from 'pinia'

import ResourceSidebar from '~/components/chat/ResourceSidebar.vue'
import VoiceTranscriptionSidebar from '~/components/chat/VoiceTranscriptionSidebar.vue'
import GroupMembersSidebar from '~/components/chat/GroupMembersSidebar.vue'
import ChatAgentPanel from '~/components/chat/ChatAgentPanel.vue'
import ChatInsightsPanel from '~/components/chat/ChatInsightsPanel.vue'
import { useChatInsights } from '~/composables/chat/useChatInsights'
import { useMessageRecognition } from '~/composables/chat/useMessageRecognition'
import { useApi } from '~/composables/useApi'
import { createEmptySearchContext, useChatSearch } from '~/composables/chat/useChatSearch'
import { useChatSessions } from '~/composables/chat/useChatSessions'
import { useChatMessages } from '~/composables/chat/useChatMessages'
import { useSnapshotRefresh } from '~/composables/chat/useSnapshotRefresh'
import { useChatExport } from '~/composables/chat/useChatExport'
import { useChatEditing } from '~/composables/chat/useChatEditing'
import { useChatHistoryWindows } from '~/composables/chat/useChatHistoryWindows'
import {
  formatCount as formatSearchCount,
  formatMessageFullTime,
  formatTransferAmount,
  getChatHistoryPreviewLines,
  getRedPacketText,
  getTransferTitle,
  highlightKeyword,
  isTransferOverdue,
  isTransferReturned
} from '~/lib/chat/formatters'
import { heatColor } from '~/lib/wrapped/heatmap'
import { parseTextWithEmoji } from '~/lib/wechat-emojis'
import { PROJECT_VOICE_TRANSCRIPTS_INVALIDATED_EVENT } from '~/lib/voice-transcript-invalidation'
import { useChatAccountsStore } from '~/stores/chatAccounts'
import { showErrorAlert } from '~/composables/useErrorNotice'
import { createAiNavigationConsumer } from '~/utils/createAiNavigationConsumer'
import { usePrivacyStore } from '~/stores/privacy'

defineOptions({ name: 'ChatPage' })
definePageMeta({
  key: 'chat'
})

useHead({
  title: '聊天记录 - xwechat'
})

const route = useRoute()
const chatPageRef = ref(null)
const chatPageActive = ref(true)
const snapshotRefreshReady = ref(false)
let chatInitialized = false
let savedScrollPositions = []
const api = useApi()
const apiBase = useApiBase()
const { openDialog: openSettingsDialog } = useSettingsDialog()

const routeUsername = computed(() => {
  const raw = route.params.username
  return (Array.isArray(raw) ? raw[0] : raw) || ''
})

const isDesktopShell = () => {
  if (!process.client || typeof window === 'undefined') return false
  return !!window.wechatDesktop?.__brand
}

const desktopDebugEnabled = ref(false)
const chatBootstrapStartedAt = process.client && typeof performance !== 'undefined' ? performance.now() : 0
let messageLoadSequence = 0
let firstSelectContactLogged = false
let firstLoadMessagesLogged = false

const resolveDesktopDebugEnabled = async () => {
  if (!isDesktopShell() || typeof window.wechatDesktop?.isDebugEnabled !== 'function') {
    desktopDebugEnabled.value = false
    return false
  }

  try {
    desktopDebugEnabled.value = !!(await window.wechatDesktop.isDebugEnabled())
  } catch {
    desktopDebugEnabled.value = false
  }

  return desktopDebugEnabled.value
}

const chatBootstrapElapsedMs = () => {
  if (!process.client || typeof performance === 'undefined') return null
  const elapsed = performance.now() - chatBootstrapStartedAt
  return Number.isFinite(elapsed) ? Number(elapsed.toFixed(1)) : null
}

const shouldLogChatBootstrap = () => isDesktopShell() || desktopDebugEnabled.value

const logChatBootstrap = (phase, details = {}) => {
  if (!shouldLogChatBootstrap()) return
  try {
    window.wechatDesktop?.logDebug?.('chat-bootstrap', phase, details)
  } catch {}
  console.info(`[chat-bootstrap] ${phase}`, {
    elapsedMs: chatBootstrapElapsedMs(),
    route: route.fullPath,
    ...details
  })
}

const waitForNextPaint = async () => {
  await nextTick()
  if (!process.client || typeof window === 'undefined') return
  await new Promise((resolve) => {
    window.requestAnimationFrame(() => {
      window.setTimeout(resolve, 0)
    })
  })
}

const nextMessageLoadToken = () => {
  messageLoadSequence += 1
  return messageLoadSequence
}

const buildTransientContact = ({ username, name = '', avatar = '', isGroup = null } = {}) => {
  const u = String(username || '').trim()
  const displayName = String(name || u).trim() || u
  return {
    id: u,
    username: u,
    name: displayName,
    avatar: String(avatar || '').trim() || null,
    avatarColor: '#4B5563',
    lastMessage: '',
    lastMessageTime: '',
    unreadCount: 0,
    isGroup: typeof isGroup === 'boolean' ? isGroup : u.endsWith('@chatroom'),
    isTop: false
  }
}

const buildChatPath = (username) => {
  return username ? `/chat/${encodeURIComponent(username)}` : '/chat'
}

const privacyStore = usePrivacyStore()
privacyStore.init()
const { privacyMode } = storeToRefs(privacyStore)

const chatAccounts = useChatAccountsStore()
const { selectedAccount } = storeToRefs(chatAccounts)

const searchContext = ref(createEmptySearchContext())

const sessionState = useChatSessions({
  chatAccounts,
  selectedAccount,
  api
})

const {
  availableAccounts,
  contacts,
  selectedContact,
  searchQuery,
  filteredContacts,
  isLoadingContacts,
  contactsError,
  showSearchAccountSwitcher,
  sessionListWidth,
  sessionListResizing,
  loadContacts,
  loadSessionsForSelectedAccount,
  refreshSessionsForSelectedAccount,
  onSessionListResizerPointerDown,
  stopSessionListResize,
  resetSessionListWidth
} = sessionState

const messageState = useChatMessages({
  api,
  apiBase,
  selectedAccount,
  selectedContact,
  privacyMode,
  searchContext,
  onSnapshotChanged: () => snapshotRefresh.refreshView()
})

const {
  allMessages,
  messagesMeta,
  messages,
  renderMessages,
  hasMoreMessages,
  isLoadingMessages,
  messagesError,
  messageContainerRef,
  showJumpToBottom,
  messagePageSize,
  messageTypeFilter,
  messageTypeFilterOptions,
  reverseMessageSides,
  previewImageUrl,
  previewVideoUrl,
  previewVideoPosterUrl,
  previewVideoError,
  highlightServerIdStr,
  highlightMessageId,
  normalizeMessage,
  updateJumpToBottomState,
  scrollToBottom,
  flashMessage,
  scrollToMessageId,
  openImagePreview,
  closeImagePreview,
  openVideoPreview,
  closeVideoPreview,
  onPreviewVideoError,
  loadMessages,
  loadMoreMessages,
  refreshSelectedMessages,
  refreshCurrentMessageMedia,
  resetMessageState,
  onAvatarError,
  contactProfileCardOpen,
  contactProfileCardMessageId,
  contactProfileLoading,
  contactProfileError,
  contactProfileResolvedName,
  contactProfileResolvedUsername,
  contactProfileResolvedNickname,
  contactProfileResolvedAlias,
  contactProfileResolvedGender,
  contactProfileResolvedRegion,
  contactProfileResolvedRemark,
  contactProfileResolvedSignature,
  contactProfileResolvedSource,
  contactProfileResolvedSourceScene,
  contactProfileResolvedAvatar,
  clearContactProfileHoverHideTimer,
  closeContactProfileCard,
  onMessageAvatarMouseEnter,
  onMessageAvatarMouseLeave,
  onContactCardMouseEnter,
  toggleReverseMessageSides
} = messageState

const groupAnnouncement = ref('')
const groupAnnouncementOpen = ref(false)
let groupAnnouncementRequestSeq = 0

const loadGroupAnnouncement = async (username) => {
  const seq = ++groupAnnouncementRequestSeq
  const account = String(selectedAccount.value || '').trim()
  const target = String(username || '').trim()
  groupAnnouncement.value = ''
  groupAnnouncementOpen.value = false
  if (!account || !target.endsWith('@chatroom')) return

  try {
    const response = await api.getChatContactProfile({ account, username: target, source: 'auto' })
    if (
      seq !== groupAnnouncementRequestSeq
      || account !== String(selectedAccount.value || '').trim()
      || target !== String(selectedContact.value?.username || '').trim()
    ) return
    groupAnnouncement.value = String(response?.contact?.announcement || '').trim()
  } catch {}
}

const openGroupAnnouncement = () => {
  if (groupAnnouncement.value) groupAnnouncementOpen.value = true
}

const closeGroupAnnouncement = () => {
  groupAnnouncementOpen.value = false
}

let exitSearchContext = async () => {}

const runMessageLoad = async ({ username, reset = true, deferUntilPaint = false, reason = '', token = nextMessageLoadToken() } = {}) => {
  const nextUsername = String(username || '').trim()
  if (!nextUsername) return false

  if (deferUntilPaint) {
    logChatBootstrap('loadMessages:scheduled', {
      username: nextUsername,
      reason,
      token
    })
    await waitForNextPaint()
    if (token !== messageLoadSequence) {
      logChatBootstrap('loadMessages:skipped-stale', {
        username: nextUsername,
        reason,
        token
      })
      return false
    }
  }

  const isFirstLoad = !firstLoadMessagesLogged
  if (isFirstLoad) {
    firstLoadMessagesLogged = true
  }

  logChatBootstrap(isFirstLoad ? 'loadMessages:first:start' : 'loadMessages:start', {
    username: nextUsername,
    reason,
    token,
    reset
  })

  await loadMessages({ username: nextUsername, reset })

  logChatBootstrap(isFirstLoad ? 'loadMessages:first:end' : 'loadMessages:end', {
    username: nextUsername,
    reason,
    token,
    renderedMessages: messages.value.length
  })

  return true
}

const selectContact = async (contact, options = {}) => {
  if (!contact) return
  const selectionReason = String(options.reason || 'manual-select').trim() || 'manual-select'
  const loadToken = nextMessageLoadToken()
  const nextUsername = contact?.username || ''
  if (searchContext.value?.active && searchContext.value.username && searchContext.value.username !== nextUsername) {
    await exitSearchContext()
  }

  const isFirstSelect = !firstSelectContactLogged
  if (isFirstSelect) {
    firstSelectContactLogged = true
  }
  logChatBootstrap(isFirstSelect ? 'selectContact:first' : 'selectContact', {
    username: nextUsername,
    reason: selectionReason,
    deferLoadMessages: !!options.deferLoadMessages,
    skipLoadMessages: !!options.skipLoadMessages,
    syncRoute: options.syncRoute !== false
  })

  if (selectedContact.value?.username !== nextUsername) savedScrollPositions = []
  selectedContact.value = contact
  if (!nextUsername) return

  if (!options.skipLoadMessages) {
    void runMessageLoad({
      username: nextUsername,
      reset: true,
      deferUntilPaint: !!options.deferLoadMessages,
      reason: selectionReason,
      token: loadToken
    })
  }

  if (chatPageActive.value && options.syncRoute !== false && nextUsername) {
    const current = routeUsername.value || ''
    if (current !== nextUsername) {
      await navigateTo(buildChatPath(nextUsername), { replace: options.replaceRoute !== false })
    }
  }
}

const applyRouteSelection = async (options = {}) => {
  if (!chatPageActive.value) return
  const selectionReason = String(options.reason || 'route-selection').trim() || 'route-selection'
  const requested = routeUsername.value || ''
  const fallbackToFirstWhenMissing = !!options.fallbackToFirstWhenMissing
  // Returning via the chat icon should resume the conversation, not select row one.
  if (!requested && selectedContact.value?.username) {
    await navigateTo(buildChatPath(selectedContact.value.username), { replace: true })
    return
  }
  if ((!contacts.value || contacts.value.length === 0) && requested) {
    if (selectedContact.value?.username === requested) {
      return
    }
    await selectContact(buildTransientContact({ username: requested }), {
      syncRoute: false,
      deferLoadMessages: !!options.deferLoadMessages,
      reason: `${selectionReason}:transient-route-empty-list`
    })
    return
  }
  if (!contacts.value || contacts.value.length === 0) {
    selectedContact.value = null
    return
  }

  if (requested) {
    if (selectedContact.value?.username === requested) {
      return
    }
    const matched = contacts.value.find((contact) => contact.username === requested)
    if (matched) {
      if (selectedContact.value?.username !== matched.username) {
        await selectContact(matched, {
          syncRoute: false,
          deferLoadMessages: !!options.deferLoadMessages,
          reason: `${selectionReason}:matched-route`
        })
      }
      return
    }
    if (fallbackToFirstWhenMissing) {
      await selectContact(contacts.value[0], {
        syncRoute: true,
        replaceRoute: true,
        deferLoadMessages: !!options.deferLoadMessages,
        reason: `${selectionReason}:route-missing-fallback-first-contact`
      })
      return
    }
    await selectContact(buildTransientContact({ username: requested }), {
      syncRoute: false,
      deferLoadMessages: !!options.deferLoadMessages,
      reason: `${selectionReason}:transient-route`
    })
    return
  }

  await selectContact(contacts.value[0], {
    syncRoute: true,
    replaceRoute: true,
    deferLoadMessages: !!options.deferLoadMessages,
    reason: `${selectionReason}:fallback-first-contact`
  })
}

const searchState = useChatSearch({
  api,
  heatColor,
  contacts,
  selectedAccount,
  selectedContact,
  privacyMode,
  allMessages,
  messagesMeta,
  messages,
  messageContainerRef,
  messagePageSize,
  hasMoreMessages,
  isLoadingMessages,
  normalizeMessage,
  updateJumpToBottomState,
  scrollToMessageId,
  flashMessage,
  highlightMessageId,
  searchContext,
  selectContact,
  loadMoreMessages,
  refreshSelectedMessages,
  invalidateSnapshotMessages: messageState.invalidateSnapshotMessages,
  onSnapshotChanged: () => snapshotRefresh.refreshView()
})

exitSearchContext = searchState.exitSearchContext

const snapshotRefresh = useSnapshotRefresh({
  api,
  selectedAccount,
  active: computed(() => chatPageActive.value && snapshotRefreshReady.value),
  getDisplayedGeneration: () => {
    const meta = messagesMeta.value[selectedContact.value?.username]
    return meta ? meta.snapshotGeneration : null
  },
  onPublished: async ({ account, signal }) => {
    await searchState.refreshSnapshotWindow({ signal })
    if (signal.aborted || account !== selectedAccount.value) return
    await refreshSessionsForSelectedAccount({ sourceOverride: 'decrypted', force: true, throwOnError: true, signal })
  }
})

const refreshChatFromWechat = () => snapshotRefresh.refreshOnce()

let locateServerIdTimer = null
const locateMessageByServerId = async (serverIdStr) => {
  if (!process.client) return false
  const target = String(serverIdStr || '').trim()
  if (!target) return false
  if (!selectedContact.value) return false

  for (let i = 0; i < 30; i++) {
    const list = messages.value || []
    const found = list.find((message) => String(message?.serverIdStr || message?.serverId || '').trim() === target)
    if (found) {
      await nextTick()
      const container = messageContainerRef.value
      const element = container?.querySelector?.(`[data-server-id="${target}"]`)
      if (element && typeof element.scrollIntoView === 'function') {
        element.scrollIntoView({ block: 'center', behavior: 'smooth' })
      }
      highlightServerIdStr.value = target
      if (locateServerIdTimer) clearTimeout(locateServerIdTimer)
      locateServerIdTimer = setTimeout(() => {
        highlightServerIdStr.value = ''
        locateServerIdTimer = null
      }, 1800)
      return true
    }

    if (!hasMoreMessages.value) break
    if (isLoadingMessages.value) {
      await new Promise((resolve) => setTimeout(resolve, 120))
      continue
    }
    await loadMoreMessages()
  }

  return false
}

const exportState = useChatExport({
  api,
  apiBase,
  contacts,
  selectedAccount,
  selectedContact,
  privacyMode
})

const historyState = useChatHistoryWindows({
  api,
  apiBase,
  selectedAccount,
  selectedContact,
  openImagePreview,
  openVideoPreview
})

const editingState = useChatEditing({
  api,
  selectedAccount,
  selectedContact,
  locateMessageByServerId
})

const {
  contextMenu,
  closeContextMenu
} = editingState

const {
  floatingWindows,
  closeTopFloatingWindow,
  closeChatHistoryModal,
  chatHistoryModalVisible,
  onFloatingWindowMouseMove,
  onFloatingWindowMouseUp
} = historyState

const { stopExportPolling } = exportState

const voiceSidebarOpen = ref(false)
const voicePanelBusy = ref(false)
const voicePanelError = ref('')
const voiceBatchJob = ref({ status: 'idle', percent: 0 })
const voiceBatchConcurrency = ref(0)
let voiceBatchPollTimer = null
let voicePanelRequestRevision = 0

const isVoiceBatchActive = (job = voiceBatchJob.value) => {
  return ['queued', 'running'].includes(String(job?.status || '').toLowerCase())
}

const normalizeVoiceBatchConcurrency = (value) => {
  const concurrency = Number(value)
  return Number.isInteger(concurrency) && concurrency >= 0 ? concurrency : 0
}

const setVoiceBatchConcurrency = (value) => {
  if (voicePanelBusy.value || isVoiceBatchActive()) return
  voiceBatchConcurrency.value = normalizeVoiceBatchConcurrency(value)
}

const voiceApiErrorMessage = (error, fallback) => {
  const detail = error?.data?.detail || error?.detail
  if (detail && typeof detail === 'object') return String(detail.message || fallback)
  return String(detail || error?.message || fallback)
}

const isVoiceJobMissingError = (error) => {
  const status = Number(error?.statusCode || error?.status || error?.response?.status || 0)
  return status === 404 || /\b404\b|(?:任务|job).*(?:不存在|not found)/i.test(voiceApiErrorMessage(error, ''))
}

const beginVoicePanelRequest = () => {
  const context = {
    revision: ++voicePanelRequestRevision,
    account: String(selectedAccount.value || '')
  }
  voicePanelBusy.value = true
  voicePanelError.value = ''
  stopVoiceBatchPolling()
  return context
}

const voicePanelContextStillCurrent = (context) => (
  context?.revision === voicePanelRequestRevision
  && context.account === String(selectedAccount.value || '')
)

const stopVoiceBatchPolling = () => {
  if (!voiceBatchPollTimer) return
  clearTimeout(voiceBatchPollTimer)
  voiceBatchPollTimer = null
}

const scheduleVoiceBatchPoll = () => {
  stopVoiceBatchPolling()
  if (!process.client || !chatPageActive.value || !voiceSidebarOpen.value || !isVoiceBatchActive()) return
  voiceBatchPollTimer = window.setTimeout(() => {
    voiceBatchPollTimer = null
    void pollVoiceBatch()
  }, 1200)
}

const applyVoiceBatchJob = (job) => {
  const wasActive = isVoiceBatchActive()
  voiceBatchJob.value = job && typeof job === 'object' ? job : { status: 'idle', percent: 0 }
  if (job && Object.prototype.hasOwnProperty.call(job, 'requestedConcurrency')) {
    voiceBatchConcurrency.value = normalizeVoiceBatchConcurrency(job.requestedConcurrency)
  }
  if (wasActive && !isVoiceBatchActive() && selectedContact.value?.username) {
    void refreshSelectedMessages()
  }
  scheduleVoiceBatchPoll()
}

const pollVoiceBatch = async () => {
  if (!voiceSidebarOpen.value || !selectedAccount.value) return
  const context = {
    revision: voicePanelRequestRevision,
    account: String(selectedAccount.value)
  }
  const jobId = String(voiceBatchJob.value?.jobId || '').trim()
  try {
    const job = jobId && isVoiceBatchActive()
      ? await api.getVoiceTranscriptionBatch(jobId)
      : await api.getLatestVoiceTranscriptionBatch(context.account)
    if (!voicePanelContextStillCurrent(context)) return
    if (job?.account && String(job.account) !== context.account) {
      voiceBatchJob.value = { status: 'idle', percent: 0 }
      voicePanelError.value = '之前的批量转写任务与当前账号不匹配，请重新开始。'
      stopVoiceBatchPolling()
      return
    }
    voicePanelError.value = ''
    applyVoiceBatchJob(job)
  } catch (error) {
    if (!voicePanelContextStillCurrent(context)) return
    if (isVoiceJobMissingError(error)) {
      voiceBatchJob.value = { status: 'idle', percent: 0 }
      voicePanelError.value = '之前的批量转写任务已失效，请重新开始。'
      stopVoiceBatchPolling()
      return
    }
    voicePanelError.value = voiceApiErrorMessage(error, '无法读取批量转写进度')
    scheduleVoiceBatchPoll()
  }
}

const refreshVoicePanel = async () => {
  if (voicePanelBusy.value) return
  const context = beginVoicePanelRequest()
  try {
    await messageState.refreshVoiceTranscriptionStatus({ force: true })
    if (!voicePanelContextStillCurrent(context)) return
    if (context.account) {
      const job = await api.getLatestVoiceTranscriptionBatch(context.account)
      if (!voicePanelContextStillCurrent(context)) return
      applyVoiceBatchJob(job)
    }
  } catch (error) {
    if (!voicePanelContextStillCurrent(context)) return
    voicePanelError.value = voiceApiErrorMessage(error, '无法读取语音转文字状态')
  } finally {
    if (voicePanelContextStillCurrent(context)) {
      voicePanelBusy.value = false
      scheduleVoiceBatchPoll()
    }
  }
}

const setVoicePanelDevice = async (device) => {
  if (voicePanelBusy.value || isVoiceBatchActive()) return
  const context = beginVoicePanelRequest()
  try {
    await api.setVoiceTranscriptionDevice(device)
    await messageState.refreshVoiceTranscriptionStatus({ force: true })
  } catch (error) {
    if (!voicePanelContextStillCurrent(context)) return
    voicePanelError.value = voiceApiErrorMessage(error, '无法保存推理设备')
  } finally {
    if (voicePanelContextStillCurrent(context)) voicePanelBusy.value = false
  }
}

const setVoicePanelModel = async (model) => {
  if (voicePanelBusy.value || isVoiceBatchActive()) return
  const context = beginVoicePanelRequest()
  try {
    await api.setVoiceTranscriptionModel(model)
    await messageState.refreshVoiceTranscriptionStatus({ force: true })
  } catch (error) {
    if (!voicePanelContextStillCurrent(context)) return
    voicePanelError.value = voiceApiErrorMessage(error, '无法保存识别模型')
  } finally {
    if (voicePanelContextStillCurrent(context)) voicePanelBusy.value = false
  }
}

const startVoiceBatch = async () => {
  if (voicePanelBusy.value || isVoiceBatchActive() || !selectedAccount.value) return
  const context = beginVoicePanelRequest()
  try {
    const job = await api.startVoiceTranscriptionBatch({
      account: context.account,
      force: false,
      concurrency: normalizeVoiceBatchConcurrency(voiceBatchConcurrency.value)
    })
    if (!voicePanelContextStillCurrent(context)) return
    applyVoiceBatchJob(job)
  } catch (error) {
    if (!voicePanelContextStillCurrent(context)) return
    voicePanelError.value = voiceApiErrorMessage(error, '无法开始批量语音转写')
  } finally {
    if (voicePanelContextStillCurrent(context)) {
      voicePanelBusy.value = false
      scheduleVoiceBatchPoll()
    }
  }
}

const cancelVoiceBatch = async () => {
  const jobId = String(voiceBatchJob.value?.jobId || '').trim()
  if (!jobId || voicePanelBusy.value || !isVoiceBatchActive()) return
  const context = beginVoicePanelRequest()
  try {
    const job = await api.cancelVoiceTranscriptionBatch(jobId)
    if (!voicePanelContextStillCurrent(context)) return
    applyVoiceBatchJob(job)
  } catch (error) {
    if (!voicePanelContextStillCurrent(context)) return
    voicePanelError.value = voiceApiErrorMessage(error, '无法取消批量语音转写')
  } finally {
    if (voicePanelContextStillCurrent(context)) {
      voicePanelBusy.value = false
      scheduleVoiceBatchPoll()
    }
  }
}

const closeVoiceSidebar = () => {
  if (!voiceSidebarOpen.value) return
  voiceSidebarOpen.value = false
  voicePanelRequestRevision += 1
  voicePanelBusy.value = false
  stopVoiceBatchPolling()
}

const openVoiceSidebar = () => {
  aiSidebarOpen.value = false
  messageState.closeResourceSidebar()
  searchState.closeMessageSearch('voice-panel')
  searchState.closeTimeSidebar()
  voiceSidebarOpen.value = true
  void refreshVoicePanel()
}

const toggleVoiceSidebar = () => {
  if (voiceSidebarOpen.value) {
    closeVoiceSidebar()
    return
  }
  openVoiceSidebar()
}

const toggleChatResourceSidebar = async () => {
  aiSidebarOpen.value = false
  closeVoiceSidebar()
  await messageState.toggleResourceSidebar()
}

const toggleChatMessageSearch = async () => {
  aiSidebarOpen.value = false
  closeVoiceSidebar()
  await searchState.toggleMessageSearch()
}

const openChatMessageSearch = async () => {
  aiSidebarOpen.value = false
  closeVoiceSidebar()
  await searchState.openMessageSearch()
}

const toggleChatTimeSidebar = async () => {
  aiSidebarOpen.value = false
  closeVoiceSidebar()
  await searchState.toggleTimeSidebar()
}

const openVoiceModelSettings = () => {
  closeVoiceSidebar()
  openSettingsDialog('voice')
}

const resetVoiceBatchState = () => {
  voicePanelRequestRevision += 1
  stopVoiceBatchPolling()
  voicePanelBusy.value = false
  voicePanelError.value = ''
  voiceBatchJob.value = { status: 'idle', percent: 0 }
}

const onProjectVoiceTranscriptsInvalidated = () => {
  resetVoiceBatchState()
}

let accountBootstrapInProgress = false
let accountChangeInProgress = false
let accountChangeQueued = false
let accountChangeDisposed = false

const resetAccountScopedState = () => {
  savedScrollPositions = []
  selectedContact.value = null
  resetMessageState()
  searchState.resetSearchState()
  closeContextMenu()
  clearContactProfileHoverHideTimer()
  closeContactProfileCard()
  resetVoiceBatchState()
}

const onAccountChange = async () => {
  if (!chatPageActive.value) {
    accountChangeQueued = true
    return
  }
  // A second selection can arrive while the previous account's session
  // request is being aborted.  Coalesce those changes and replay the latest
  // selected account instead of dropping the watcher event.
  if (accountChangeInProgress) {
    accountChangeQueued = true
    return
  }
  accountChangeInProgress = true
  snapshotRefreshReady.value = false
  try {
    const accountAtStart = String(selectedAccount.value || '').trim()
    logChatBootstrap('accountChange:start', {
      selectedAccount: selectedAccount.value
    })
    resetAccountScopedState()
    try {
      isLoadingContacts.value = true
      contactsError.value = ''
      await loadSessionsForSelectedAccount()
    } catch (error) {
      contactsError.value = error?.message || '加载会话失败'
    } finally {
      isLoadingContacts.value = false
    }

    if (accountAtStart !== String(selectedAccount.value || '').trim()) {
      accountChangeQueued = true
      return
    }

    logChatBootstrap('accountChange:applyRouteSelection:start', {
      selectedAccount: selectedAccount.value,
      contactCount: contacts.value.length
    })
    await applyRouteSelection({
      reason: 'account-change',
      fallbackToFirstWhenMissing: true
    })
    logChatBootstrap('accountChange:end', {
      selectedAccount: selectedAccount.value,
      selectedUsername: selectedContact.value?.username || '',
      contactCount: contacts.value.length
    })
  } finally {
    accountChangeInProgress = false
    if (accountChangeQueued && !accountChangeDisposed) {
      accountChangeQueued = false
      void onAccountChange().catch((error) => {
        contactsError.value = error?.message || '加载会话失败'
      })
    } else if (accountChangeDisposed) {
      accountChangeQueued = false
    } else {
      snapshotRefreshReady.value = true
    }
  }
}

const onGlobalClick = (event) => {
  if (contextMenu.value.visible) closeContextMenu()
  if (searchState.messageSearchSenderDropdownOpen.value) {
    const element = searchState.messageSearchSenderDropdownRef.value
    const target = event?.target
    if (element && target && !element.contains(target)) {
      searchState.closeMessageSearchSenderDropdown()
    }
  }
}

const onGlobalKeyDown = (event) => {
  if (!process.client) return

  const key = String(event?.key || '')
  const lower = key.toLowerCase()

  if ((event.ctrlKey || event.metaKey) && lower === 'f') {
    event.preventDefault()
    void openChatMessageSearch()
    return
  }

  if (key === 'Escape') {
    if (contextMenu.value.visible) closeContextMenu()
    if (previewImageUrl.value) closeImagePreview()
    if (previewVideoUrl.value) closeVideoPreview()
    if (Array.isArray(floatingWindows.value) && floatingWindows.value.length) closeTopFloatingWindow()
    if (chatHistoryModalVisible.value) closeChatHistoryModal()
    if (contactProfileCardOpen.value) {
      clearContactProfileHoverHideTimer()
      closeContactProfileCard()
    }
    if (searchState.messageSearchSenderDropdownOpen.value) searchState.closeMessageSearchSenderDropdown()
    if (searchState.messageSearchOpen.value) searchState.closeMessageSearch()
    if (searchState.timeSidebarOpen.value) searchState.closeTimeSidebar()
    if (voiceSidebarOpen.value) closeVoiceSidebar()
    groupMembersSidebarOpen.value = false
    if (searchContext.value?.active) exitSearchContext()
  }
}

const RESUME_MEDIA_REFRESH_MIN_INTERVAL_MS = 1200
const RESUME_MEDIA_REFRESH_MIN_HIDDEN_MS = 30 * 1000

let lastResumeMediaRefreshAt = 0
let lastPageHiddenAt = 0

const hasLoadedConversationMedia = () => {
  const list = Array.isArray(messages.value) ? messages.value : []
  return list.some((message) => {
    return !!(
      String(message?.imageUrl || '').trim()
      || String(message?.videoThumbUrl || '').trim()
      || String(message?.quoteImageUrl || '').trim()
    )
  })
}

const maybeRefreshMediaOnResume = () => {
  if (!process.client) return
  if (!selectedContact.value?.username) return
  if (searchContext.value?.active) return
  if (!hasLoadedConversationMedia()) return

  const hiddenDuration = lastPageHiddenAt > 0 ? (Date.now() - lastPageHiddenAt) : 0
  if (hiddenDuration < RESUME_MEDIA_REFRESH_MIN_HIDDEN_MS) return

  const now = Date.now()
  if ((now - lastResumeMediaRefreshAt) < RESUME_MEDIA_REFRESH_MIN_INTERVAL_MS) return
  lastResumeMediaRefreshAt = now
  lastPageHiddenAt = 0
  void refreshCurrentMessageMedia()
}

const onWindowFocus = () => {
  maybeRefreshMediaOnResume()
}

const onVisibilityChange = () => {
  if (document.visibilityState === 'hidden') {
    lastPageHiddenAt = Date.now()
    return
  }
  if (document.visibilityState !== 'visible') return
  maybeRefreshMediaOnResume()
}

const attachChatViewListeners = () => {
  document.addEventListener('click', onGlobalClick)
  document.addEventListener('keydown', onGlobalKeyDown)
  document.addEventListener('mousemove', onFloatingWindowMouseMove)
  document.addEventListener('mouseup', onFloatingWindowMouseUp)
  document.addEventListener('touchmove', onFloatingWindowMouseMove)
  document.addEventListener('touchend', onFloatingWindowMouseUp)
  document.addEventListener('touchcancel', onFloatingWindowMouseUp)
  window.addEventListener('focus', onWindowFocus)
  document.addEventListener('visibilitychange', onVisibilityChange)
}

const detachChatViewListeners = () => {
  document.removeEventListener('click', onGlobalClick)
  document.removeEventListener('keydown', onGlobalKeyDown)
  document.removeEventListener('mousemove', onFloatingWindowMouseMove)
  document.removeEventListener('mouseup', onFloatingWindowMouseUp)
  document.removeEventListener('touchmove', onFloatingWindowMouseMove)
  document.removeEventListener('touchend', onFloatingWindowMouseUp)
  document.removeEventListener('touchcancel', onFloatingWindowMouseUp)
  window.removeEventListener('focus', onWindowFocus)
  document.removeEventListener('visibilitychange', onVisibilityChange)
}

onBeforeRouteLeave(() => {
  closeVideoPreview()
  savedScrollPositions = [...chatPageRef.value.querySelectorAll('.overflow-y-auto')]
    .map(element => [element, element.scrollTop])
  for (const media of chatPageRef.value.querySelectorAll('audio, video')) media.pause()
})

onActivated(async () => {
  chatPageActive.value = true
  attachChatViewListeners()
  if (!chatInitialized) return
  if (accountChangeQueued) {
    accountChangeQueued = false
    await onAccountChange()
  } else {
    await applyRouteSelection({ reason: 'route-resume' })
  }
  await nextTick()
  if (!chatPageActive.value) return
  for (const [element, scrollTop] of savedScrollPositions) element.scrollTop = scrollTop
  savedScrollPositions = []
  updateJumpToBottomState()
  if (voiceSidebarOpen.value) void pollVoiceBatch()
  void consumeAiNavigation()
})

onDeactivated(() => {
  chatPageActive.value = false
  detachChatViewListeners()
  stopVoiceBatchPolling()
  stopSessionListResize()
  onFloatingWindowMouseUp()
  closeContextMenu()
  clearContactProfileHoverHideTimer()
  closeContactProfileCard()
  closeImagePreview()
  closeVideoPreview()
  closeGroupAnnouncement()
})

onMounted(async () => {
  if (!process.client) return

  window.addEventListener(PROJECT_VOICE_TRANSCRIPTS_INVALIDATED_EVENT, onProjectVoiceTranscriptsInvalidated)
  await resolveDesktopDebugEnabled()
  logChatBootstrap('route mount start', {
    requestedUsername: routeUsername.value,
    selectedAccount: selectedAccount.value,
    desktopShell: isDesktopShell()
  })

  logChatBootstrap('loadContacts:start', {
    selectedAccount: selectedAccount.value
  })
  accountBootstrapInProgress = true
  try {
    await chatAccounts.ensureLoaded()
    await loadContacts()
  } finally {
    accountBootstrapInProgress = false
  }
  logChatBootstrap('loadContacts:end', {
    selectedAccount: selectedAccount.value,
    contactCount: contacts.value.length
  })
  chatInitialized = true

  const deferInitialConversationBoot = isDesktopShell()
  await waitForNextPaint()
  logChatBootstrap('first render completion', {
    contactCount: contacts.value.length,
    deferInitialConversationBoot
  })

  logChatBootstrap('applyRouteSelection:start', {
    requestedUsername: routeUsername.value,
    deferLoadMessages: deferInitialConversationBoot
  })
  await applyRouteSelection({
    deferLoadMessages: deferInitialConversationBoot,
    reason: deferInitialConversationBoot ? 'initial-route-post-paint' : 'initial-route'
  })
  logChatBootstrap('applyRouteSelection:end', {
    selectedUsername: selectedContact.value?.username || '',
    requestedUsername: routeUsername.value
  })
  snapshotRefreshReady.value = true

})

onUnmounted(() => {
  if (!process.client) return

  accountChangeDisposed = true
  accountChangeQueued = false

  detachChatViewListeners()
  window.removeEventListener(PROJECT_VOICE_TRANSCRIPTS_INVALIDATED_EVENT, onProjectVoiceTranscriptsInvalidated)

  if (locateServerIdTimer) clearTimeout(locateServerIdTimer)
  locateServerIdTimer = null
  stopVoiceBatchPolling()
  stopSessionListResize()
  stopExportPolling()
})

watch(
  () => selectedContact.value?.username,
  (username) => {
    void loadGroupAnnouncement(username)
  }
)

watch(messageTypeFilter, async (next, prev) => {
  if (String(next || '') === String(prev || '')) return
  if (!selectedContact.value?.username) return
  await refreshSelectedMessages()
})

watch(
  selectedAccount,
  async (next, prev) => {
    if (!process.client) return
    if (accountBootstrapInProgress) return
    if (String(next || '') === String(prev || '')) return
    await onAccountChange()
    if (voiceSidebarOpen.value) await refreshVoicePanel()
  }
)

watch(
  routeUsername,
  async (next, prev) => {
    if (!process.client) return
    if (isLoadingContacts.value) return
    if (!contacts.value.length) return
    logChatBootstrap('routeUsername:change', {
      previousUsername: prev || '',
      nextUsername: next || ''
    })
    await applyRouteSelection({
      reason: 'route-watch'
    })
  }
)

const aiSidebarOpen = ref(false)
const insightsPanelOpen = ref(false)
const insightsSettingsOpen = ref(false)
const insightSharedState = useState('chat-agent-ui', () => ({ selected: {}, drafts: {}, pinned: {} }))
const insightsState = useChatInsights({ account: selectedAccount, contact: selectedContact, open: insightsPanelOpen, api: useAiApi(), shared: insightSharedState.value })
const recognitionState = useMessageRecognition({ account: selectedAccount, contact: selectedContact, messages,
  engine: insightsState.engine, modelChoice: insightsState.modelChoice, api: useAiApi() })
const toggleInsightsPanel = (showSettings = false) => {
  insightsSettingsOpen.value = showSettings === true
  insightsPanelOpen.value = !insightsPanelOpen.value
  if (insightsPanelOpen.value) {
    aiSidebarOpen.value = false
    closeVoiceSidebar()
    messageState.closeResourceSidebar()
    searchState.closeMessageSearch('insights-panel')
    searchState.closeTimeSidebar()
    groupMembersSidebarOpen.value = false
  }
}
watch([aiSidebarOpen, voiceSidebarOpen, messageState.resourceSidebarOpen, searchState.messageSearchOpen, searchState.timeSidebarOpen], opened => {
  if (opened.some(Boolean)) insightsPanelOpen.value = false
})
const groupMembersSidebarOpen = ref(false)
const toggleGroupMembersSidebar = () => {
  if (groupMembersSidebarOpen.value) {
    groupMembersSidebarOpen.value = false
    return
  }
  if (!selectedContact.value?.isGroup) return
  insightsPanelOpen.value = false
  aiSidebarOpen.value = false
  closeVoiceSidebar()
  messageState.closeResourceSidebar()
  searchState.closeMessageSearch('group-members')
  searchState.closeTimeSidebar()
  groupMembersSidebarOpen.value = true
}
watch([selectedAccount, () => selectedContact.value?.username], () => {
  groupMembersSidebarOpen.value = false
})
watch([
  aiSidebarOpen,
  voiceSidebarOpen,
  messageState.resourceSidebarOpen,
  searchState.messageSearchOpen,
  searchState.timeSidebarOpen
], (opened) => {
  if (opened.some(Boolean)) groupMembersSidebarOpen.value = false
})
const aiFocusTaskId = ref('')
watch(selectedAccount, () => { aiFocusTaskId.value = '' })
const aiNavigation = useState('ai-navigation-target', () => null)
const aiDiagnosticApi = useAiApi()
const toggleAiSidebar = () => {
  aiSidebarOpen.value = !aiSidebarOpen.value
  if (aiSidebarOpen.value) {
    closeVoiceSidebar()
    messageState.closeResourceSidebar()
    searchState.closeMessageSearch('ai-panel')
    searchState.closeTimeSidebar()
  }
}
const diagnoseAiSource = async (source, operation) => {
  const started = Date.now()
  try {
    const result = await operation()
    aiDiagnosticApi.diagnostic(result === false ? 'source.failed' : 'source.ready', { component: 'source', source_id: source.source, duration_ms: Date.now() - started })
    return result
  } catch (error) {
    aiDiagnosticApi.diagnostic('source.failed', { component: 'source', source_id: source.source, duration_ms: Date.now() - started, trace_id: error?.trace_id, diagnostic_id: error?.diagnostic_id })
    throw error
  }
}
const prepareAiSource = source => diagnoseAiSource(source, () => searchState.prepareAnchorContext({ targetUsername: source.username, anchorId: source.anchor }))
const locateAiSource = source => diagnoseAiSource(source, () => searchState.locateByAnchorId({ targetUsername: source.username, anchorId: source.anchor, kind: 'ai', label: 'AI 消息来源', throwOnError: true }))
const locateInsightSource = async source => {
  const account = selectedAccount.value, username = selectedContact.value.username
  // 定位要求消息真正进入可见区；先恢复聊天布局，保留输入组件和草稿。
  insightsPanelOpen.value = false
  await nextTick()
  try {
    const found = await locateAiSource(source)
    if (found === false) throw new Error('未定位到来源消息')
    return found
  } catch (error) {
    if (selectedAccount.value === account && selectedContact.value?.username === username) {
      insightsPanelOpen.value = true
      await nextTick()
    }
    throw error
  }
}
const consumeAiNavigation = createAiNavigationConsumer({
  navigation: aiNavigation,
  account: selectedAccount,
  isActive: () => chatPageActive.value,
  ensureLoaded: () => chatAccounts.ensureLoaded(),
  selectAccount: account => chatAccounts.setSelectedAccount(account),
  waitForAccount: async () => {
    await nextTick()
    // 等待现有账号切换流程结束，防止定位结果被初始会话加载覆盖。
    for (let i = 0; i < 100 && (!snapshotRefreshReady.value || accountBootstrapInProgress || accountChangeInProgress); i++) await new Promise(resolve => setTimeout(resolve, 100))
    if (!snapshotRefreshReady.value || accountBootstrapInProgress || accountChangeInProgress) throw new Error('聊天初始化或账号切换尚未完成，请稍后重试定位')
  },
  showSourceChat: async () => {
    aiSidebarOpen.value = false
    insightsPanelOpen.value = false
    await nextTick()
  },
  openTask: taskId => {
    aiSidebarOpen.value = true
    aiFocusTaskId.value = taskId
  },
  locateSource: source => source.origin === 'wrapped'
    ? searchState.locateByAnchorId({ targetUsername: source.username, anchorId: source.anchor, kind: 'wrapped', label: '年度总结来源', throwOnError: true })
    : source.origin === 'search'
      ? searchState.locateByAnchorId({ targetUsername: source.username, anchorId: source.anchor, kind: 'search', label: '搜索来源', throwOnError: true })
    : locateAiSource(source),
  diagnostic: (event, context) => aiDiagnosticApi.diagnostic(event, context),
  showError: showErrorAlert,
})
watch(aiNavigation, () => { void consumeAiNavigation() })
onMounted(() => { void consumeAiNavigation() })

const chatState = {
  insightsPanelOpen,
  toggleInsightsPanel,
  insightLabels: computed(() => ({ ...insightsState.labels.value, ...recognitionState.labels.value })),
  recognitionState,
  recognitionEngine: insightsState.engine,
  recognitionHeader: recognitionState.header,
  groupMembersSidebarOpen,
  toggleGroupMembersSidebar,
  aiSidebarOpen,
  toggleAiSidebar,
  chatAccounts,
  snapshotRefresh,
  refreshChatFromWechat,
  isRefreshingMessages: snapshotRefresh.manualRefreshing,
  selectedAccount,
  availableAccounts,
  contacts,
  selectedContact,
  searchContext,
  filteredContacts,
  searchQuery,
  showSearchAccountSwitcher,
  isLoadingContacts,
  contactsError,
  sessionListWidth,
  sessionListResizing,
  onSessionListResizerPointerDown,
  resetSessionListWidth,
  selectContact,
  onAccountChange,
  privacyMode,
  parseTextWithEmoji,
  formatMessageFullTime,
  highlightKeyword,
  formatCount: formatSearchCount,
  formatTransferAmount,
  getChatHistoryPreviewLines,
  getRedPacketText,
  getTransferTitle,
  isTransferOverdue,
  isTransferReturned,
  ...messageState,
  ...searchState,
  ...exportState,
  ...editingState,
  ...historyState,
  groupAnnouncement,
  groupAnnouncementOpen,
  openGroupAnnouncement,
  closeGroupAnnouncement,
  voiceSidebarOpen,
  voicePanelBusy,
  voicePanelError,
  voiceBatchJob,
  voiceBatchConcurrency,
  openVoiceSidebar,
  closeVoiceSidebar,
  toggleVoiceSidebar,
  refreshVoicePanel,
  setVoicePanelDevice,
  setVoicePanelModel,
  setVoiceBatchConcurrency,
  startVoiceBatch,
  cancelVoiceBatch,
  openVoiceModelSettings,
  toggleResourceSidebar: toggleChatResourceSidebar,
  toggleMessageSearch: toggleChatMessageSearch,
  openMessageSearch: openChatMessageSearch,
  toggleTimeSidebar: toggleChatTimeSidebar
}
</script>
