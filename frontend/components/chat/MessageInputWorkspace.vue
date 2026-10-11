<template>
  <div
    class="message-input-workspace flex flex-col shrink-0 relative select-text"
    :style="{ height: `${inputHeight}px`, minHeight: pendingAttachment ? '320px' : selectedAttachments.length ? '260px' : undefined }"
  >
    <!-- Top Draggable Resizer Handle -->
    <div
      class="chat-input-resizer h-1.5 w-full cursor-row-resize bg-transparent hover:bg-[#07c160]/30 transition-colors select-none flex items-center justify-center group shrink-0"
      role="separator"
      tabindex="0"
      aria-orientation="horizontal"
      aria-label="调整输入框高度"
      :aria-valuenow="inputHeight"
      :aria-valuemin="MIN_HEIGHT"
      :aria-valuemax="MAX_HEIGHT"
      @pointerdown="startResize"
      @dblclick="resetHeight"
    >
      <div class="h-0.5 w-8 rounded-full bg-gray-300/80 dark:bg-gray-600/80 group-hover:bg-[#07c160] transition-colors" />
    </div>

    <!-- Verbatim Error Alert Banner (Debug-First / Let-It-Fail) -->
    <div
      v-if="errorInfo"
      class="chat-input-error-banner mx-3 my-1 p-2 rounded bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-800 text-xs text-red-700 dark:text-red-300 flex items-start justify-between gap-2 shadow-sm shrink-0"
      role="alert"
    >
      <div class="flex items-start gap-1.5 min-w-0">
        <span class="inline-block shrink-0 px-1 py-0.5 rounded font-mono font-bold text-[11px] bg-red-200/80 dark:bg-red-900/80 text-red-800 dark:text-red-200">
          {{ errorInfo.code }}
        </span>
        <span class="chat-input-error-message break-words">{{ errorInfo.message }}</span>
      </div>
      <button
        type="button"
        class="chat-input-error-dismiss shrink-0 text-red-400 hover:text-red-600 dark:hover:text-red-200 text-sm font-bold px-1 leading-none"
        title="关闭提示"
        aria-label="关闭错误提示"
        @click="errorInfo = null"
      >
        ×
      </button>
    </div>

    <div v-if="pendingAttachment" role="status" :class="`chat-input-${pendingAttachment.kind}-pending`" class="mx-3 my-1 p-2 rounded border border-amber-300 bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200 text-xs shrink-0">
      <p>附件发送结果待核对：请在微信「{{ pendingAttachment.display_name || pendingAttachment.username }}」中检查「{{ pendingAttachment.name }}」。后续附件已暂停，避免重复发送。</p>
      <div class="flex flex-wrap gap-3 mt-1">
        <button type="button" :class="`chat-input-${pendingAttachment.kind}-confirm`" class="underline disabled:opacity-40" :disabled="!attachmentTargetMatches || isSending" @click="confirmAttachmentSent">已在微信确认发送</button>
        <button type="button" :class="`chat-input-${pendingAttachment.kind}-retry-ready`" class="underline disabled:opacity-40" :disabled="!attachmentTargetMatches || isSending" @click="allowAttachmentRetry">已核对未发送，允许重试</button>
      </div>
      <details class="mt-1 max-h-20 overflow-y-auto">
        <summary class="cursor-pointer">查看原因</summary>
        <p class="break-words">{{ pendingAttachment.confirmation.code }}：{{ pendingAttachment.confirmation.message }}</p>
      </details>
    </div>

    <!-- Action Toolbar -->
    <div class="chat-input-toolbar flex items-center px-3 py-1 text-xs border-b border-gray-100 dark:border-gray-800/80 select-none shrink-0">
      <div class="chat-input-tools flex items-center gap-2">
        <button
          type="button"
          class="chat-input-btn-ai inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium text-[#07c160] hover:bg-[#07c160]/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          :disabled="!canAiSuggest"
          :aria-busy="isGeneratingAiReply"
          title="根据最近上下文生成智能回复建议"
          @click="handleAiSuggest"
        >
          <svg v-if="isGeneratingAiReply" class="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          <svg v-else class="chat-tool-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z" /></svg>
          <span>{{ isGeneratingAiReply ? '生成中...' : 'AI 建议' }}</span>
        </button>

        <button
          type="button"
          class="chat-input-btn-image inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          :disabled="!canPickAttachment"
          :aria-busy="selectingAttachment === 'image'"
          title="添加 PNG 或 JPEG 图片，可多选（Windows 桌面版）"
          @click="handlePickAttachment('image')"
        >
          <svg class="chat-tool-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="4" /><circle cx="8" cy="8" r="1.5" /><path d="m21 15-5-5L5 21" /></svg>
          <span>{{ selectingAttachment === 'image' ? '选图中...' : '图片' }}</span>
        </button>

        <button
          type="button"
          class="chat-input-btn-file inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          :disabled="!canPickAttachment"
          :aria-busy="selectingAttachment === 'file'"
          title="添加文件，可多选（Windows 桌面版，PNG/JPEG 将作为图片发送）"
          @click="handlePickAttachment('file')"
        >
          <svg class="chat-tool-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21 11-8.4 8.4a6 6 0 0 1-8.5-8.5l9-9a4 4 0 0 1 5.7 5.7l-9 9a2 2 0 0 1-2.8-2.8l8.4-8.4" /></svg>
          <span>{{ selectingAttachment === 'file' ? '选择中...' : '文件' }}</span>
        </button>

        <MessageRecognitionControl
          v-if="recognitionState"
          :state="recognitionState"
          :engine="recognitionEngine"
          compact
          @configure="state.toggleInsightsPanel(true)"
        />

        <AutoReplyControl
          :account="account"
          :session-id="contactUsername"
          :target-name="contactDisplayName"
          :group="contactUsername.endsWith('@chatroom')"
        />

        <button
          type="button"
          class="chat-input-btn-clear inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          :disabled="!canClear"
          title="清空输入框与错误提示"
          @click="clearDraft"
        >
          <svg class="chat-tool-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></svg>
          <span>清空</span>
        </button>
      </div>

    </div>

    <!-- Textarea & Send Button -->
    <div class="chat-input-body flex-1 flex flex-col min-h-0 relative px-3 py-1.5">
      <div v-if="selectedAttachments.length" class="chat-input-attachments max-h-32 overflow-y-auto shrink-0 mb-1" aria-label="待发送附件">
        <div v-for="attachment in selectedAttachments" :key="attachment.id" class="chat-input-attachment flex items-center gap-2 mb-1 text-xs">
          <img v-if="attachment.kind === 'image'" :src="attachment.previewDataUrl" :alt="attachment.name" class="chat-input-image-preview w-12 h-10 object-contain rounded border border-gray-200 dark:border-gray-700" />
          <svg v-else class="chat-input-file-preview h-8 w-8 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></svg>
          <div class="min-w-0 flex-1">
            <div class="chat-input-attachment-name truncate" :title="attachment.name">{{ attachment.name }}</div>
            <div class="chat-input-attachment-size text-gray-500">{{ formatFileSize(String(attachment.sizeBytes)) }}</div>
            <div class="text-gray-500 truncate">{{ attachmentTargetMatches ? `发送给 ${attachment.display_name || attachment.username}` : '附件属于原会话，请切回原会话或移除未发送项' }}</div>
          </div>
          <button type="button" :class="`chat-input-${attachment.kind}-remove`" class="chat-input-attachment-remove px-2 py-1 text-gray-500 disabled:opacity-40" :disabled="isSending || !!selectingAttachment || !!attachment.confirmation" :aria-label="`移除 ${attachment.name}`" @click="removeAttachment(attachment.id)">移除</button>
        </div>
        <p v-if="selectedAttachments.some(attachment => attachment.kind === 'image')" class="text-xs text-amber-700 dark:text-amber-300">发送图片会短暂激活微信，随后自动核对本地发送记录。</p>
      </div>
      <textarea
        ref="textareaRef"
        v-model="draftText"
        aria-label="输入消息"
        class="chat-input-textarea flex-1 w-full resize-none outline-none bg-transparent text-sm text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 disabled:opacity-60 disabled:cursor-not-allowed leading-relaxed overflow-y-auto"
        :placeholder="placeholderText"
        :disabled="isDisabled || isSending"
        rows="2"
        @input="onInput"
        @paste="onPaste"
        @keydown.enter.exact="onEnter"
        @compositionstart="isComposing = true"
        @compositionend="isComposing = false"
      />

      <div class="chat-input-footer flex items-center justify-end gap-2 pt-1 pb-0.5 shrink-0">
        <div class="mr-auto text-[11px] text-gray-400 dark:text-gray-500" aria-live="polite">
          {{ selectingAttachment === 'paste' ? '正在读取粘贴附件...' : 'Enter 发送，Shift + Enter 换行' }}
        </div>
        <button
          v-if="selectedAttachments.length"
          type="button"
          class="chat-input-btn-send-attachments px-3 py-1.5 rounded-md text-xs font-medium border border-[#07c160] text-[#07c160] disabled:opacity-40 disabled:cursor-not-allowed"
          :disabled="!canSendAttachment"
          :aria-busy="isSending"
          @click="handleSendAttachment"
        >{{ isSending ? '发送中，剩余' : '发送附件' }}（{{ selectedAttachments.length }}）</button>
        <button
          type="button"
          class="chat-input-btn-send inline-flex items-center justify-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium bg-[#07c160] hover:bg-[#06ad56] text-white shadow-sm disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          :disabled="!canSend"
          :aria-busy="isSending"
          @click="handleSend"
        >
          <svg v-if="isSending" class="animate-spin h-3.5 w-3.5 text-white" viewBox="0 0 24 24" fill="none">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          <span>{{ isSending ? '发送中...' : '发送' }}</span>
          <svg v-if="!isSending" class="chat-tool-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m22 2-7 20-4-9-9-4L22 2ZM22 2 11 13" /></svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, unref, watch, onDeactivated, onMounted, onUnmounted, nextTick } from 'vue'
import { useApi as defaultUseApi } from '~/composables/useApi'
import { formatFileSize } from '~/lib/chat/formatters'
import MessageRecognitionControl from '~/components/chat/MessageRecognitionControl.vue'
import AutoReplyControl from '~/components/chat/AutoReplyControl.vue'

const props = defineProps({
  state: {
    type: Object,
    required: true
  },
  api: {
    type: Object,
    default: null
  }
})

// Resolve API service instance
const getApi = () => {
  if (props.api) return props.api
  if (props.state?.api) return props.state.api
  if (typeof globalThis !== 'undefined' && typeof globalThis.useApi === 'function') {
    return globalThis.useApi()
  }
  return defaultUseApi()
}

// Resizer constants & state
const MIN_HEIGHT = 100
const MAX_HEIGHT = 400
const DEFAULT_HEIGHT = 150
const STORAGE_KEY = 'ui.chat.input_height'

const inputHeight = ref(DEFAULT_HEIGHT)
const textareaRef = ref(null)
const draftText = ref('')
const isSending = ref(false)
const selectingAttachment = ref('')
const selectedAttachments = ref([])
let nextAttachmentId = 0
let targetRevision = 0
const isGeneratingAiReply = ref(false)
const isComposing = ref(false)
const errorInfo = ref(null)
const aiView = useState('chat-agent-ui', () => ({ selected: {}, drafts: {}, pinned: {} }))
const recognitionState = computed(() => unref(props.state.recognitionState))
const recognitionEngine = computed(() => unref(props.state.recognitionEngine))

// Computed helpers to extract data from props.state safely
const account = computed(() => {
  const acc = unref(props.state?.selectedAccount)
  if (!acc) return ''
  if (typeof acc === 'object') return String(acc.account || acc.username || '').trim()
  return String(acc).trim()
})

const contact = computed(() => unref(props.state?.selectedContact) || null)
const contactUsername = computed(() => contact.value?.username || '')
const contactDisplayName = computed(() => {
  return contact.value?.name || contact.value?.enterpriseName || contact.value?.remark || contact.value?.nickname || ''
})

const hasAccount = computed(() => !!account.value)
const hasContact = computed(() => !!contactUsername.value)
const isDisabled = computed(() => !hasAccount.value || !hasContact.value)

const placeholderText = computed(() => {
  if (!hasAccount.value) {
    return '未选择解密账号，请先在左侧选择账号'
  }
  if (!hasContact.value) {
    return '未选定会话，请从左侧选择联系人或群聊'
  }
  return '输入消息，Enter 发送；Ctrl+V 粘贴图片、文件'
})

const canSend = computed(() => {
  return hasAccount.value && hasContact.value && !isSending.value && !selectingAttachment.value && !!draftText.value.trim()
})

// Even switching away and back invalidates a running selection/send batch.
watch([account, contactUsername], () => { targetRevision += 1 }, { flush: 'sync' })

const pendingAttachment = computed(() => selectedAttachments.value.find(attachment => attachment.confirmation))
const attachmentTargetMatches = computed(() => selectedAttachments.value.every(attachment => attachment.account === account.value && attachment.username === contactUsername.value))
const canPickAttachment = computed(() => hasAccount.value && hasContact.value && !isSending.value && !selectingAttachment.value && !pendingAttachment.value && attachmentTargetMatches.value)
const canSendAttachment = computed(() => canPickAttachment.value && selectedAttachments.value.length > 0)

const canAiSuggest = computed(() => {
  return hasAccount.value && hasContact.value && !isSending.value && !isGeneratingAiReply.value
})

const canClear = computed(() => {
  return hasAccount.value && hasContact.value && !isSending.value && (!!draftText.value || !!errorInfo.value)
})

// Auto-resizing textarea logic
const resizeDraft = () => {
  const textarea = textareaRef.value
  if (!textarea) return
  textarea.style.height = 'auto'
  textarea.style.height = `${Math.max(28, textarea.scrollHeight)}px`
}

const onInput = () => {
  resizeDraft()
}

// Drag Resizer Handlers
let startY = 0
let startHeight = 0

const onPointerMove = (e) => {
  // Dragging up increases height
  const deltaY = startY - e.clientY
  inputHeight.value = Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, startHeight + deltaY))
}

const onPointerUp = () => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', onPointerUp)
  }
  try {
    localStorage.setItem(STORAGE_KEY, String(inputHeight.value))
  } catch {}
}

const startResize = (e) => {
  startY = e.clientY
  startHeight = inputHeight.value
  if (typeof window !== 'undefined') {
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
  }
}

const resetHeight = () => {
  inputHeight.value = DEFAULT_HEIGHT
  try {
    localStorage.setItem(STORAGE_KEY, String(DEFAULT_HEIGHT))
  } catch {}
}

// Keyboard Enter & IME Guard
const onEnter = (e) => {
  if (e.isComposing || isComposing.value) {
    return
  }
  e.preventDefault()
  void handleSend()
}

// Send Message Handler
const handleSend = async () => {
  // Synchronous concurrency lock check
  if (isSending.value) return
  if (!canSend.value) return

  const content = draftText.value.trim()
  if (!content) return

  isSending.value = true
  errorInfo.value = null

  try {
    const api = getApi()
    const receipt = await api.sendChatMessage({
      account: account.value,
      username: contactUsername.value,
      display_name: contactDisplayName.value || null,
      content: draftText.value
    })
    if (receipt?.success !== true) {
      throw Object.assign(new Error('后端未确认消息发送成功，请检查微信后再决定是否重试'), { code: 'WECHAT_SEND_UNCONFIRMED' })
    }

    // Debug-First / Let-It-Fail: Draft is ONLY cleared on successful send!
    draftText.value = ''
    await nextTick()
    resizeDraft()
    textareaRef.value?.focus()

    if (typeof props.state?.refreshSelectedMessages === 'function') {
      props.state.refreshSelectedMessages()
    }
  } catch (err) {
    // Debug-First / Let-It-Fail:
    // Draft text is strictly PRESERVED on error. NEVER cleared!
    const code = err.code || err.data?.code || (typeof err.detail === 'object' ? err.detail?.code : '') || 'SEND_ERROR'
    const message = err.message || err.detail || err.data?.detail || '发送消息失败'
    errorInfo.value = {
      code: String(code),
      message: typeof message === 'object' ? (message.message || message.detail || JSON.stringify(message)) : String(message)
    }
  } finally {
    isSending.value = false
  }
}

const showAttachmentError = (err, kind) => {
  const code = err.code || err.data?.code || (typeof err.detail === 'object' ? err.detail?.code : '') || `${kind.toUpperCase()}_SEND_ERROR`
  const message = err.message || err.detail || err.data?.detail || String(err)
  errorInfo.value = {
    code: String(code),
    message: typeof message === 'object' ? (message.message || message.detail || JSON.stringify(message)) : String(message)
  }
}

const onPaste = (event) => {
  // Snapshot FileList during the event; text-only paste keeps native editing behavior.
  const files = Array.from(event.clipboardData?.files || [])
  if (!files.length) return
  event.preventDefault()
  if (!canPickAttachment.value) {
    errorInfo.value = { code: 'ATTACHMENT_PASTE_BLOCKED', message: '当前无法添加附件，请等待当前操作完成，并核对待发送列表及目标会话。' }
    return
  }
  void handlePickAttachment('paste', files)
}

const handlePickAttachment = async (kind, files) => {
  if (!canPickAttachment.value) return
  const target = { account: account.value, username: contactUsername.value, display_name: contactDisplayName.value || null }
  const revision = targetRevision
  const label = kind === 'paste' ? '附件' : kind === 'image' ? '图片' : '文件'
  selectingAttachment.value = kind
  errorInfo.value = null
  try {
    const desktop = window.wechatDesktop
    const choose = kind === 'paste' ? desktop?.importChatAttachments : kind === 'image' ? desktop?.chooseImage : desktop?.chooseFile
    if (desktop?.platform !== 'win32' || typeof choose !== 'function') {
      throw Object.assign(new Error(`${label}添加需要 Windows 桌面版支持，请使用最新源码启动桌面应用后重试`), { code: `${kind.toUpperCase()}_SEND_UNSUPPORTED` })
    }
    const result = kind === 'paste' ? await choose(files) : await choose()
    if (result.canceled) return
    if (revision !== targetRevision) {
      throw Object.assign(new Error(`选择${label}期间会话已切换，请在当前会话重新选择${label}`), { code: `${kind.toUpperCase()}_TARGET_CHANGED` })
    }
    if (!Array.isArray(result.attachments) || !result.attachments.length) {
      throw Object.assign(new Error('桌面应用未返回附件列表，请重启桌面应用后重试'), { code: 'ATTACHMENT_PICKER_INVALID' })
    }
    selectedAttachments.value.push(...result.attachments.map(attachment => ({ ...attachment, ...target, id: ++nextAttachmentId })))
  } catch (err) {
    showAttachmentError(err, kind)
  } finally {
    selectingAttachment.value = ''
  }
}

const removeAttachment = (id) => {
  if (isSending.value || selectingAttachment.value) return
  selectedAttachments.value = selectedAttachments.value.filter(attachment => attachment.id !== id || attachment.confirmation)
}

const confirmAttachmentSent = () => {
  if (!pendingAttachment.value || !attachmentTargetMatches.value || isSending.value) return
  // User acknowledgement only; no backend success is manufactured and no resend occurs.
  selectedAttachments.value = selectedAttachments.value.filter(attachment => attachment.id !== pendingAttachment.value.id)
  if (typeof props.state?.refreshSelectedMessages === 'function') {
    props.state.refreshSelectedMessages()
  }
}

const allowAttachmentRetry = () => {
  if (!pendingAttachment.value || !attachmentTargetMatches.value || isSending.value) return
  delete pendingAttachment.value.confirmation
}

const handleSendAttachment = async () => {
  if (!canSendAttachment.value) return
  const revision = targetRevision
  const queue = [...selectedAttachments.value]
  let sentCount = 0
  isSending.value = true
  errorInfo.value = null
  try {
    const api = getApi()
    for (const attachment of queue) {
      // Existing backend shares a 1 s per-session cooldown across message types.
      // This is pacing between confirmed sends, never an automatic retry.
      if (sentCount > 0) await new Promise(resolve => setTimeout(resolve, 1000))
      if (revision !== targetRevision) {
        errorInfo.value = { code: 'ATTACHMENT_TARGET_CHANGED', message: '会话已切换，后续附件已暂停；请切回原会话后重新点击发送。' }
        return
      }
      try {
        const target = { account: attachment.account, username: attachment.username, display_name: attachment.display_name }
        const receipt = attachment.kind === 'image'
          ? await api.sendChatImage({ ...target, image_path: attachment.path })
          : await api.sendChatFile({ ...target, file_path: attachment.path })
        if (receipt?.success !== true) {
          throw Object.assign(new Error('后端未确认附件发送成功，请检查微信后再决定是否重试'), { code: 'WECHAT_SEND_UNCONFIRMED' })
        }
      } catch (err) {
        showAttachmentError(err, attachment.kind)
        // A missing/transport response cannot prove that the native send did not happen.
        if (errorInfo.value.code === 'WECHAT_SEND_UNCONFIRMED' || !errorInfo.value.code.startsWith('WECHAT_')) {
          attachment.confirmation = errorInfo.value
          errorInfo.value = null
        }
        return
      }
      selectedAttachments.value = selectedAttachments.value.filter(item => item.id !== attachment.id)
      sentCount += 1
    }
  } finally {
    isSending.value = false
    if (sentCount > 0 && revision === targetRevision && typeof props.state?.refreshSelectedMessages === 'function') {
      props.state.refreshSelectedMessages()
    }
  }
}

// AI Reply Suggestion Handler
const handleAiSuggest = async () => {
  if (!canAiSuggest.value) return
  isGeneratingAiReply.value = true
  errorInfo.value = null

  try {
    const api = getApi()
    const choice = aiView.value.modelSelection?.choice
    const res = await api.getAiSuggestedReply({
      account: account.value,
      username: contactUsername.value,
      display_name: contactDisplayName.value || null,
      count: 10,
      ...(choice?.profile_id ? { selected_model: { ...choice } } : {})
    })

    if (typeof res?.suggestion !== 'string' || !res.suggestion.trim()) {
      throw new Error('AI 建议未返回有效的回复草稿')
    }
    draftText.value = res.suggestion
    await nextTick()
    resizeDraft()
    textareaRef.value?.focus()
  } catch (err) {
    const code = err.code || err.data?.code || (typeof err.detail === 'object' ? err.detail?.code : '') || 'AI_SUGGEST_ERROR'
    const message = err.message || err.detail || err.data?.detail || '获取 AI 建议失败'
    errorInfo.value = {
      code: String(code),
      message: typeof message === 'object' ? (message.message || message.detail || JSON.stringify(message)) : String(message)
    }
  } finally {
    isGeneratingAiReply.value = false
  }
}

// Clear Draft Handler
const clearDraft = () => {
  if (isSending.value) return
  draftText.value = ''
  errorInfo.value = null
  nextTick(() => {
    resizeDraft()
    textareaRef.value?.focus()
  })
}

// Lifecycle Hooks
onMounted(() => {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      const parsed = Number.parseInt(saved, 10)
      if (!Number.isNaN(parsed)) {
        inputHeight.value = Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, parsed))
      }
    }
  } catch {}
})

onUnmounted(() => {
  targetRevision += 1
  if (typeof window !== 'undefined') {
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', onPointerUp)
  }
})

onDeactivated(() => {
  targetRevision += 1
  onPointerUp()
})

defineExpose({
  draftText,
  isSending,
  isGeneratingAiReply,
  errorInfo,
  inputHeight,
  handleSend,
  handleAiSuggest,
  clearDraft,
  resizeDraft
})
</script>

<style scoped>
.message-input-workspace {
  margin: 0 24px 20px;
  border: 1px solid var(--chat-input-border);
  border-radius: 18px;
  background-color: var(--chat-input-bg);
}
.message-input-workspace:focus-within { border-color: var(--chat-focus-ring); }
.chat-input-resizer { border-radius: 18px 18px 0 0; }
.chat-input-resizer > div { background: var(--chat-input-border); }
.chat-input-resizer:hover { background: var(--chat-subtle-bg); }
.chat-input-resizer:hover > div { background: var(--chat-focus-ring); }
.chat-input-toolbar { min-height: 38px; padding: 3px 10px 7px; overflow-x: auto; border-color: var(--app-border-subtle); scrollbar-width: thin; }
.chat-input-tools { gap: 6px; flex-wrap: nowrap; white-space: nowrap; }
.chat-input-tools > * { flex-shrink: 0; }
.chat-input-tools > button { height: 28px; gap: 6px; border-radius: 9px; padding: 4px 8px; color: var(--app-text-secondary); }
.chat-input-tools > button:hover:not(:disabled) { color: var(--chat-accent); background: var(--chat-subtle-bg); }
.chat-input-tools > .chat-input-btn-ai { color: var(--chat-ai-text); background: var(--chat-ai-bg); padding-right: 11px; padding-left: 11px; }
.chat-input-tools > .chat-input-btn-ai:hover:not(:disabled) { color: var(--chat-ai-text); background: var(--chat-ai-bg); filter: brightness(.97); }
.chat-tool-icon { width: 15px; height: 15px; flex-shrink: 0; }
.chat-input-body { padding: 9px 14px 10px; }
.chat-input-textarea { background: transparent; color: var(--app-text-primary); caret-color: var(--chat-focus-ring); font-size: 14px; line-height: 1.65; }
.chat-input-textarea::placeholder { color: var(--chat-sender-name); }
.chat-input-footer { padding-top: 5px; }
.chat-input-footer > div { color: var(--app-text-secondary); }
.chat-input-btn-send { min-width: 78px; height: 32px; gap: 8px; border-radius: 10px; background: var(--chat-accent); color: var(--chat-input-bg); box-shadow: none; }
.chat-input-btn-send:hover:not(:disabled) { background: var(--chat-accent-hover); }
.chat-input-btn-send-attachments { border-color: var(--chat-accent); color: var(--chat-accent); border-radius: 10px; }
.chat-input-btn-send-attachments:hover:not(:disabled) { background: var(--chat-subtle-bg); }
.chat-input-tools > button:focus-visible, .chat-input-footer button:focus-visible, .chat-input-resizer:focus-visible { outline: 2px solid var(--chat-focus-ring); outline-offset: 2px; }
@container (max-width: 840px) {
  .message-input-workspace { margin-right: 20px; margin-left: 20px; margin-bottom: 16px; }
}
@container (max-width: 480px) {
  .message-input-workspace { margin-right: 12px; margin-left: 12px; margin-bottom: 12px; border-radius: 14px; }
  .chat-input-body { padding-right: 10px; padding-left: 10px; }
  .chat-input-footer { flex-wrap: wrap; }
  .chat-input-footer > div { font-size: 10px; }
}
</style>
