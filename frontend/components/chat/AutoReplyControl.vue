<script setup>
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { usePrivacyStore } from '~/stores/privacy'

const props = defineProps({
  account: { type: String, required: true },
  sessionId: { type: String, required: true },
  targetName: { type: String, required: true },
  group: Boolean,
  api: { type: Object, default: null },
})
const api = props.api || useAiApi()
const { privacyMode } = storeToRefs(usePrivacyStore())
const { openDialog } = useSettingsDialog()
const config = ref(null), status = ref(null), busy = ref(''), error = ref(''), loadError = ref('')
let generation = 0, mounted = false, timer
const stateLabels = { off: '待确认应用', starting: '启动中', running: '运行中', paused: '已暂停', needs_review: '待核对' }
const selected = computed(() => config.value?.sessions.find(item => item.session_id === props.sessionId))
const enabled = computed(() => selected.value?.enabled === true)
const sessionStatus = computed(() => status.value?.sessions.find(item => item.session_id === props.sessionId))
const statusText = computed(() => {
  if (!props.account || !props.sessionId) return '请选择会话'
  if (busy.value === 'save') return '保存中'
  if (!config.value || !status.value) return loadError.value ? '读取失败' : '读取中'
  if (sessionStatus.value?.status === 'needs_review') return '待核对'
  if (!enabled.value) return '未参与'
  if (status.value.state === 'running' && !sessionStatus.value) return '已加入·等待状态'
  const state = status.value.state === 'running' ? sessionStatus.value.status : status.value.state
  return `已加入·${stateLabels[state]}`
})
const visibleError = computed(() => {
  if (error.value) return error.value
  if (loadError.value) return loadError.value
  const failure = sessionStatus.value?.error || status.value?.error
  return failure ? `${failure.code}：${failure.detail}` : ''
})

function checkResponse(value, account, kind) {
  if (value.account !== account) throw new Error(`自动回复${kind}响应账号不一致，请在设置中检查`)
  if (!Array.isArray(value.sessions)) throw new Error(`自动回复${kind}响应缺少会话列表`)
  if (kind === '状态' && (!(value.state in stateLabels) || value.sessions.some(item => !(item.status in stateLabels)))) {
    throw new Error('自动回复状态响应包含未知状态，请在设置中检查')
  }
}

async function refresh() {
  if (!mounted || !props.account || !props.sessionId || busy.value) return
  const current = generation, account = props.account
  busy.value = 'load'
  try {
    const query = `?account=${encodeURIComponent(account)}`
    const [nextConfig, nextStatus] = await Promise.all([
      api.request(`/auto-reply/config${query}`),
      api.request(`/auto-reply/status${query}`),
    ])
    if (current !== generation) return
    checkResponse(nextConfig, account, '配置')
    checkResponse(nextStatus, account, '状态')
    config.value = nextConfig
    status.value = nextStatus
    loadError.value = ''
  } catch (cause) {
    if (current !== generation) return
    config.value = null
    status.value = null
    loadError.value = cause.message
  } finally {
    if (current === generation) busy.value = ''
  }
}

async function toggle() {
  if (busy.value || !config.value || !status.value) return
  if (!selected.value && !props.targetName.trim()) {
    error.value = '缺少完整定位名称，请在自动回复设置中填写后再加入'
    return
  }
  const current = generation, account = props.account
  const body = Object.fromEntries(
    ['account', 'version', 'profile_id', 'model_id', 'system_prompt', 'reasoning_effort', 'thinking_mode', 'thinking_budget']
      .filter(key => key in config.value).map(key => [key, config.value[key]]),
  )
  body.sessions = config.value.sessions.map(({ session_id, target_name, enabled, group_trigger }) => ({ session_id, target_name, enabled, group_trigger }))
  const existing = body.sessions.find(item => item.session_id === props.sessionId)
  if (existing) existing.enabled = !existing.enabled
  else body.sessions.push({ session_id: props.sessionId, target_name: props.targetName.trim(), enabled: true, group_trigger: 'mentions' })
  busy.value = 'save'
  let stopped = false, saved = false
  try {
    if (!body.sessions.some(item => item.enabled) && status.value.state !== 'off') {
      const nextStatus = await api.request('/auto-reply/stop', { method: 'POST', body: { account }, retry: 0 })
      if (current !== generation) return
      checkResponse(nextStatus, account, '状态')
      status.value = nextStatus
      stopped = true
    }
    const next = await api.request('/auto-reply/config', { method: 'PUT', body, retry: 0 })
    if (current !== generation) return
    checkResponse(next, account, '配置')
    config.value = next
    saved = true
    status.value = null
    const nextStatus = await api.request(`/auto-reply/status?account=${encodeURIComponent(account)}`)
    if (current !== generation) return
    checkResponse(nextStatus, account, '状态')
    status.value = nextStatus
    error.value = ''
    loadError.value = ''
    if (enabled.value && (['off', 'paused'].includes(nextStatus.state) || sessionStatus.value?.status === 'paused')) openDialog('auto-reply')
  } catch (cause) {
    if (current !== generation) return
    if (saved) {
      error.value = ''
      loadError.value = `设置已保存，但状态读取失败：${cause.message}`
    } else error.value = stopped ? `自动回复已停止，但设置未保存：${cause.message}` : cause.message
  } finally {
    if (current === generation) busy.value = ''
  }
}

watch(() => [props.account, props.sessionId], () => {
  generation += 1
  config.value = null
  status.value = null
  error.value = ''
  loadError.value = ''
  busy.value = ''
  refresh()
}, { flush: 'sync' })
const activate = () => {
  if (mounted) return
  mounted = true
  refresh()
  timer = setInterval(refresh, 5000)
}
const deactivate = () => {
  mounted = false
  generation += 1
  clearInterval(timer)
  busy.value = ''
  config.value = null
  status.value = null
}
onMounted(activate)
onActivated(activate)
onDeactivated(deactivate)
onBeforeUnmount(deactivate)
</script>

<template>
  <div class="auto-reply-control">
    <button type="button" role="switch" aria-label="当前会话参与自动回复" :aria-checked="enabled"
      :disabled="!!busy || !config || !status" :class="{ enabled }"
      :title="group ? '将本群加入自动回复范围，首次添加默认仅 @ 我触发；未运行时需在设置中确认应用' : '将当前聊天加入自动回复范围；未运行时需在设置中确认应用'"
      @click="toggle">
      <span class="auto-reply-switch-track" aria-hidden="true" />参与回复
    </button>
    <span role="status">{{ statusText }}</span>
    <button type="button" class="auto-reply-settings" aria-label="自动回复设置" @click="openDialog('auto-reply')">设置</button>
    <span v-if="visibleError" role="alert" class="auto-reply-error" :class="{ 'privacy-blur': privacyMode }" :title="privacyMode ? undefined : visibleError">{{ visibleError }}</span>
  </div>
</template>

<style scoped>
.auto-reply-control { display: inline-flex; align-items: center; gap: 5px; width: auto; flex-shrink: 0; white-space: nowrap; font-size: 11px; color: var(--app-text-secondary); }
button { display: inline-flex; align-items: center; gap: 6px; min-height: 28px; width: auto; padding: 4px 7px; border: 1px solid var(--app-border); border-radius: 9px; background: var(--chat-subtle-bg); color: var(--app-text-primary); font: inherit; white-space: nowrap; cursor: pointer; }
button:hover:not(:disabled) { border-color: var(--chat-focus-ring); }
button:focus-visible { outline: 2px solid var(--chat-focus-ring); outline-offset: 2px; }
button:disabled { opacity: .55; cursor: default; }
button.enabled { color: var(--chat-accent); }
.auto-reply-switch-track { position: relative; flex: 0 0 24px; width: 24px; height: 14px; border-radius: 999px; background: var(--app-text-muted); }
.auto-reply-switch-track::after { content: ''; position: absolute; top: 2px; left: 2px; width: 10px; height: 10px; border-radius: 50%; background: var(--chat-input-bg); }
.enabled .auto-reply-switch-track { background: var(--chat-accent); }
.enabled .auto-reply-switch-track::after { left: 12px; }
.auto-reply-settings { color: var(--app-text-secondary); border-color: transparent; background: transparent; }
.auto-reply-settings:hover { background: var(--chat-subtle-bg); }
.auto-reply-error { max-width: 260px; overflow: hidden; text-overflow: ellipsis; color: var(--danger-hover); }
</style>
