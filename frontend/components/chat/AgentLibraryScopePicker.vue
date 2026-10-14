<template>
  <div ref="anchor" class="agent-chat-scope" @keydown.esc.stop="close(true)">
    <button ref="trigger" type="button" class="agent-chat-scope-trigger" aria-label="选择专题版本" :aria-expanded="open" :aria-controls="id" :disabled="disabled || !account" @click="toggle">
      <FolderOpen :size="16" :stroke-width="1.8" aria-hidden="true" /><span :class="{ 'privacy-blur': privacyMode }">{{ modelValue ? `${modelValue.folder_name} · 第 ${modelValue.version_number} 版 · ${modelValue.version_name}` : '选择专题版本' }}</span>
    </button>
    <section v-if="open" :id="id" class="agent-chat-scope-popover agent-library-picker" aria-label="专题版本范围">
      <header><strong>选择专题保存版本</strong><button type="button" aria-label="关闭专题选择" @click="close(true)"><X :size="16" :stroke-width="1.8" aria-hidden="true" /></button></header>
      <p>只查阅所选版本保存的材料；更改范围会开始新对话。</p>
      <p v-if="loading" role="status">正在读取专题版本…</p>
      <p v-if="error" class="agent-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ error }} <button type="button" @click="loadFolders">重试</button></p>
      <label>专题<select ref="folderInput" v-model="folderId" aria-label="专题" :disabled="loading" :class="{ 'privacy-blur': privacyMode }" @change="loadVersions"><option v-for="folder in folders" :key="folder.id" :value="folder.id">{{ folder.name }}</option></select></label>
      <label v-if="versions.length">保存版本<select v-model="versionId" aria-label="专题保存版本" :disabled="loading" :class="{ 'privacy-blur': privacyMode }"><option v-for="version in versions" :key="version.id" :value="version.id">第 {{ version.number }} 版 · {{ version.name }} · {{ new Date(version.created_at * 1000).toLocaleString() }}</option></select></label>
      <p v-else-if="!loading && !error">{{ folders.length ? '此专题尚无保存版本，请先在资料夹中保存版本。' : '当前账号还没有专题，请先在本地资料夹整理并保存版本。' }}</p>
      <footer><button type="button" aria-label="应用专题版本" :disabled="loading || !!error || !versionId" @click="apply">使用此保存版本</button></footer>
    </section>
  </div>
</template>
<script setup>
import { getCurrentInstance, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { FolderOpen, X } from '@lucide/vue'
import { useLibraryApi } from '~/composables/useLibraryApi'
import { usePrivacyStore } from '~/stores/privacy'
const props = defineProps({ account: String, modelValue: Object, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const api = useLibraryApi(), { privacyMode } = storeToRefs(usePrivacyStore())
const anchor = ref(null), trigger = ref(null), folderInput = ref(null), open = ref(false)
const folders = ref([]), versions = ref([]), folderId = ref(''), versionId = ref(''), loading = ref(false), error = ref(''), folderName = ref('')
const id = `agent-library-scope-${getCurrentInstance().uid}`
let revision = 0
const close = (focus = false) => { ++revision; open.value = false; loading.value = false; if (focus) trigger.value?.focus() }
const message = cause => typeof cause.data?.detail === 'string' ? cause.data.detail : cause.data?.detail?.message || cause.message
async function loadVersions() {
  const token = ++revision, account = props.account, selectedFolder = folderId.value
  versions.value = []; versionId.value = ''; error.value = ''; loading.value = true
  try {
    const result = await api.getDossier(account, selectedFolder)
    if (token !== revision) return
    versions.value = result.versions; folderName.value = result.folder_name
    versionId.value = selectedFolder === props.modelValue?.folder_id && result.versions.some(version => version.id === props.modelValue.version_id)
      ? props.modelValue.version_id : result.versions[0]?.id || ''
  } catch (cause) { if (token === revision) error.value = message(cause) }
  finally { if (token === revision) loading.value = false }
}
async function loadFolders() {
  const token = ++revision, account = props.account
  loading.value = true; error.value = ''; folders.value = []; versions.value = []; versionId.value = ''
  try {
    const result = await api.listFolders(account)
    if (token !== revision) return
    folders.value = result
    folderId.value = result.some(folder => folder.id === props.modelValue?.folder_id) ? props.modelValue.folder_id : result[0]?.id || ''
    if (folderId.value) await loadVersions()
  } catch (cause) { if (token === revision) error.value = message(cause) }
  finally { if (token === revision) loading.value = false }
}
async function toggle() {
  if (open.value) { close(); return }
  open.value = true
  await loadFolders(); await nextTick(); if (open.value) folderInput.value?.focus()
}
function apply() {
  const version = versions.value.find(item => item.id === versionId.value)
  emit('update:modelValue', { folder_id: folderId.value, folder_name: folderName.value, version_id: version.id, version_name: version.name, version_number: version.number, saved_at: version.created_at, item_count: version.item_count })
  close(true)
}
const outside = event => { if (!anchor.value?.contains(event.target)) close() }
watch(() => props.account, () => { close(); folders.value = []; versions.value = []; error.value = ''; folderId.value = ''; versionId.value = '' }, { flush: 'sync' })
watch(() => props.disabled, value => { if (value) close() })
onMounted(() => document.addEventListener('pointerdown', outside))
onBeforeUnmount(() => { ++revision; document.removeEventListener('pointerdown', outside) })
</script>
<style scoped>
.agent-library-picker p { padding: 0 14px; margin: 10px 0; color: var(--app-text-secondary); font-size: 12px; line-height: 1.6; }
.agent-library-picker label { display: grid; gap: 6px; margin: 12px 14px; font-size: 12px; }
.agent-library-picker select { width: 100%; min-width: 0; padding: 8px; background: var(--app-surface-bg); border: 1px solid var(--app-border); border-radius: 6px; color: inherit; font: inherit; }
.agent-library-picker select:focus-visible { outline: 2px solid var(--app-accent); outline-offset: 2px; }
</style>
