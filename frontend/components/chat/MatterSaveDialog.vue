<template>
  <div class="matter-shade" @click.self="emit('close')">
    <form ref="dialog" class="matter-dialog theme-scope" role="dialog" aria-modal="true" aria-labelledby="matter-save-title" tabindex="-1"
      @submit.prevent="save" @keydown.esc.stop="emit('close')" @keydown.tab="trapFocus">
      <header><h2 id="matter-save-title">从原消息保存事项</h2><button type="button" aria-label="关闭事项对话框" @click="emit('close')">关闭</button></header>
      <p v-if="loading" role="status">正在核对原消息…</p>
      <p v-if="error" class="matter-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ error }} <button v-if="!evidence" type="button" @click="load">重新加载</button></p>
      <template v-if="evidence">
        <p class="matter-muted">{{ formatDate(evidence.create_time) }} · <span :class="{ 'privacy-blur': privacyMode }">{{ evidence.sender }}</span></p>
        <blockquote :class="{ 'privacy-blur': privacyMode }">{{ evidence.text }}</blockquote>
        <fieldset :disabled="busy">
          <label>操作<select v-model="mode"><option value="create">保存为新事项</option><option value="reschedule">作为已有事项的改期依据</option></select></label>
          <template v-if="mode === 'create'">
            <label>事项标题<input v-model="title" :class="{ 'privacy-blur': privacyMode }" maxlength="200" required /></label>
            <label>确认期限（可留空）<input v-model="due" type="datetime-local" /></label>
            <label class="matter-check"><input v-model="reminder" type="checkbox" />到期提醒一次</label>
            <p class="matter-muted">期限由你确认；留空表示尚未安排。提醒需要设置期限，并保持 xwechat 运行。</p>
          </template>
          <template v-else>
            <label>选择要改期的事项<select v-model="matterId" :class="{ 'privacy-blur': privacyMode }" required><option value="" disabled>请选择未完成事项</option><option v-for="item in activeMatters" :key="item.id" :value="item.id">{{ item.title }}</option></select></label>
            <p v-if="!activeMatters.length" class="matter-muted">当前账号没有未完成事项，请先保存新事项。</p>
            <p v-if="selected" class="matter-muted">原期限：{{ selected.due_at === null ? '尚未安排' : formatDate(selected.due_at) }}</p>
            <label>确认新的期限<input v-model="due" type="datetime-local" required /></label>
            <p class="matter-muted">请核对上方原文确实对应此事项。确认后保留改期依据和原期限，取消尚未发出的旧提醒。</p>
          </template>
        </fieldset>
      </template>
      <footer><button type="button" @click="emit('close')">取消</button><button class="matter-primary" type="submit" :disabled="!canSave">{{ busy ? '正在保存…' : mode === 'create' ? '保存事项' : '确认更新期限' }}</button></footer>
    </form>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { usePrivacyStore } from '~/stores/privacy'
import { useMattersApi } from '~/composables/useMattersApi'
const props = defineProps({ account: { type: String, required: true }, source: { type: Object, required: true }, title: { type: String, default: '' }, matterId: { type: String, default: '' } })
const emit = defineEmits(['close', 'saved'])
const api = useMattersApi(), { privacyMode } = storeToRefs(usePrivacyStore())
const evidence = ref(null), matters = ref([]), mode = ref('create'), title = ref(''), due = ref(''), reminder = ref(false), matterId = ref('')
const error = ref(''), loading = ref(false), busy = ref(false), dialog = ref(null)
const activeMatters = computed(() => matters.value.filter(item => item.status !== 'done'))
const selected = computed(() => activeMatters.value.find(item => item.id === matterId.value))
const canSave = computed(() => !loading.value && !busy.value && evidence.value && (mode.value === 'create'
  ? title.value.trim() && (!reminder.value || due.value) : selected.value && due.value))
const formatDate = value => new Date(value * 1000).toLocaleString()
const errorText = value => typeof value.data?.detail === 'string' ? value.data.detail : value.message
let generation = 0, previousFocus
const load = async () => {
  const token = ++generation, account = props.account
  evidence.value = null; matters.value = []; mode.value = 'create'; title.value = props.title; due.value = ''; reminder.value = false; matterId.value = ''; error.value = ''; loading.value = true; busy.value = false
  try {
    const [source, items] = await Promise.all([api.source(account, { username: props.source.username, anchor: props.source.anchor }), api.list(account)])
    if (token !== generation) return
    evidence.value = source; matters.value = items
    if (props.matterId) { mode.value = 'reschedule'; matterId.value = props.matterId }
    if (!props.title) title.value = source.text.slice(0, 200)
  } catch (e) { if (token === generation) error.value = errorText(e) }
  finally { if (token === generation) loading.value = false }
}
watch(() => [props.account, props.source.username, props.source.anchor, props.matterId], load, { immediate: true })
watch([mode, matterId], () => { due.value = '' })
const save = async () => {
  const token = generation, account = props.account
  busy.value = true; error.value = ''
  try {
    const dueAt = due.value ? Math.floor(new Date(due.value).getTime() / 1000) : null
    const result = mode.value === 'create'
      ? await api.create(account, { title: title.value.trim(), source: evidence.value, due_at: dueAt, reminder_enabled: reminder.value })
      : await api.reschedule(account, selected.value.id, { revision: selected.value.revision, due_at: dueAt, source: evidence.value, confirmed: true })
    if (token !== generation) return
    emit('saved', result); emit('close')
  } catch (e) { if (token === generation) error.value = errorText(e) }
  finally { if (token === generation) busy.value = false }
}
onMounted(async () => { previousFocus = document.activeElement; await nextTick(); dialog.value?.focus() })
onUnmounted(() => { generation++; previousFocus?.focus() })
const trapFocus = event => {
  const controls = [...dialog.value.querySelectorAll('button,input,select')].filter(el => !el.matches(':disabled'))
  const first = controls[0], last = controls[controls.length - 1]
  if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.value)) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
</script>

<style scoped>
.matter-shade{position:fixed;inset:0;z-index:10000;background:rgb(0 0 0 / .38);display:flex;align-items:center;justify-content:center;padding:20px}
.matter-dialog{background:var(--app-surface-bg);color:var(--app-text-primary);border:1px solid var(--app-border);border-radius:12px;width:560px;max-width:100%;max-height:90vh;overflow:auto;padding:24px;font-size:14px;line-height:1.65}
header,footer{display:flex;align-items:center;justify-content:space-between;gap:12px}h2{font-size:18px;font-weight:650;margin:0}footer{justify-content:flex-end;margin-top:24px}
label{display:grid;gap:6px;margin:14px 0}input,select,button{font:inherit;color:inherit;background:var(--app-surface-bg);border:1px solid var(--app-border);border-radius:6px;padding:8px 10px}input,select{width:100%;min-width:0}button{white-space:nowrap;cursor:pointer}button:hover{background:var(--app-surface-muted)}button:disabled{opacity:.5;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--app-accent);outline-offset:2px}
fieldset{border:0;padding:0;margin:0;min-width:0}.matter-check{display:flex;align-items:center;gap:8px}.matter-check input{width:auto}.matter-primary{background:var(--chat-accent);color:var(--chat-input-bg)}.matter-primary:hover{background:var(--chat-accent-hover)}.matter-muted{color:var(--app-text-secondary);font-size:13px}.matter-error{color:var(--danger-color,#b91c1c)}blockquote{margin:16px 0;padding:12px;background:var(--app-surface-muted);white-space:pre-wrap;overflow-wrap:anywhere;max-height:200px;overflow:auto}
@media(max-width:600px){.matter-shade{padding:12px}.matter-dialog{padding:18px}}
</style>
