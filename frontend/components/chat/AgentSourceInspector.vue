<template>
  <aside id="agent-source-inspector" class="agent-source-inspector" aria-label="原文出处" @keydown.esc.stop.prevent="$emit('close')">
    <header><h3>原文出处</h3><button type="button" aria-label="关闭原文出处" @click="$emit('close')"><X :size="16" :stroke-width="1.8" aria-hidden="true" /></button></header>
    <p class="agent-source-heading"><template v-if="number">引用 <strong>{{ number }}</strong></template><template v-else>相关原文</template><span :class="{ 'privacy-blur': privacyMode }"> / {{ source.name || source.username }}</span></p>
    <AgentLibraryScopeNotice v-if="source.kind === 'library'" :scope="source.library_scope" />
    <article class="agent-source-message is-selected"><template v-if="source.kind === 'library'"><strong :class="{ 'privacy-blur': privacyMode }">{{ source.title }}</strong><AgentLibraryMaterial :source="source" :read="readMaterial" /></template><template v-else><div class="agent-source-sender"><AgentAvatar :path="source.sender_avatar_path" :name="source.sender" /><small>{{ source.sender || '未知发送者' }}<time>{{ date(source.time) }}</time></small></div><p>{{ source.text }}</p></template></article>
    <p v-if="source.excerpt && source.kind !== 'library'" class="agent-source-hint">此处为原文节选，可定位查看完整消息。</p>
    <section v-if="source.kind !== 'library'" class="agent-source-context" aria-label="上下文消息">
      <h4>上下文消息</h4>
      <p v-if="loading" class="agent-source-hint" role="status">正在读取上下文…</p>
      <p v-else-if="contextError" class="agent-source-hint" role="status">{{ contextError }}<button type="button" @click="loadContext">重试</button></p>
      <template v-else-if="messages.length">
        <article v-for="(message, index) in messages" :key="message.id || index" class="agent-source-message" :class="{ 'is-selected': String(message.id) === contextAnchor }"><small>{{ message.senderDisplayName || message.sender_display_name || message.sender_name || message.sender || '聊天消息' }}<time>{{ date(message.createTime || message.create_time || message.time) }}</time></small><p>{{ message.content_text || message.content || message.text || '[非文本消息，点击定位查看]' }}</p></article>
      </template>
      <p v-else class="agent-source-hint">定位到聊天，查看这条消息前后的完整记录。</p>
    </section>
    <p v-if="locateError" class="agent-citation-error" role="alert">{{ locateError }}</p>
    <button v-if="source.kind !== 'library'" type="button" class="agent-source-locate" :disabled="locating" :aria-busy="locating" @click="locateMessage"><LoaderCircle v-if="locating" class="agent-icon-spin" :size="16" :stroke-width="1.8" aria-hidden="true" /><ExternalLink v-else :size="16" :stroke-width="1.8" aria-hidden="true" />{{ locating ? '正在定位…' : locateError ? '重试定位' : '定位到聊天' }}</button>
    <p class="agent-source-hint">{{ source.kind === 'library' ? '引用内容来自此保存版本，保留原材料类型与人工核验状态。' : '引用内容来自已读取的聊天记录。' }}</p>
    <p v-if="source.kind === 'library' && source.original_source" class="agent-source-hint" :class="{ 'privacy-blur': privacyMode }">保存时的原始来源：{{ source.original_source.name || source.original_source.username }}<template v-if="source.original_source.sender"> · {{ source.original_source.sender }}</template></p>
  </aside>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { usePrivacyStore } from '~/stores/privacy'
import { ExternalLink, LoaderCircle, X } from '@lucide/vue'
import AgentAvatar from './AgentAvatar.vue'
import AgentLibraryScopeNotice from './AgentLibraryScopeNotice.vue'
import AgentLibraryMaterial from './AgentLibraryMaterial.vue'
const props = defineProps({ source: { type: Object, required: true }, number: Number, prepare: Function, locate: Function, readMaterial: Function })
const { privacyMode } = storeToRefs(usePrivacyStore())
defineEmits(['close'])
const contextAnchor = ref('')
const messages = ref([]), loading = ref(false), contextError = ref(''), locating = ref(false), locateError = ref('')
let version = 0
const date = value => value ? new Date(Number(value) * 1000).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''
const loadContext = async () => {
  const current = ++version
  if (props.source.kind === 'library') { messages.value = []; loading.value = false; contextError.value = ''; return }
  loading.value = !!props.prepare; contextError.value = ''; messages.value = []
  try {
    const result = await props.prepare?.(props.source)
    if (current !== version) return
    const all = result?.messages || []
    // 只显示引用前后两条真实消息，避免把整个聊天再次挤进出处栏。
    contextAnchor.value = String(result?.anchorId || props.source.anchor)
    const index = all.findIndex(message => String(message.id) === contextAnchor.value)
    messages.value = index >= 0 ? all.slice(Math.max(0, index - 2), index + 3) : []
  } catch { if (current === version) contextError.value = '上下文暂时无法读取，仍可查看引用原文。' }
  finally { if (current === version) loading.value = false }
}
const locateMessage = async () => {
  if (locating.value) return
  const current = version
  locating.value = true; locateError.value = ''
  try { if (await props.locate?.(props.source) === false) throw new Error('定位未完成，请重试') }
  catch (error) { if (current === version) locateError.value = error?.message || '定位失败，请重试' }
  finally { if (current === version) locating.value = false }
}
watch(() => props.source, () => { locating.value = false; locateError.value = ''; void loadContext() }, { immediate: true })
onBeforeUnmount(() => { ++version })
</script>
