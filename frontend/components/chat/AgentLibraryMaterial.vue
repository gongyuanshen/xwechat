<template>
  <div class="agent-library-material">
    <small class="agent-library-material-kind">{{ materialKind }} · {{ source.verified ? '已人工核验' : '待人工核验' }}</small>
    <p class="agent-citation-text" :class="{ 'privacy-blur': privacyMode }">{{ text }}</p>
    <p v-if="source.coverage" class="agent-source-hint" :class="{ 'privacy-blur': privacyMode }">{{ source.coverage }}</p>
    <p v-if="loading" role="status">正在读取保存材料…</p>
    <p v-if="error" class="agent-citation-error" :class="{ 'privacy-blur': privacyMode }" role="alert">{{ error }}</p>
    <button v-if="error" type="button" aria-label="重试读取保存材料" @click="load(nextOffset)">重试读取保存材料</button>
    <button v-else-if="nextOffset !== null && loaded" type="button" aria-label="继续读取保存材料" :disabled="loading" @click="load(nextOffset)">继续读取保存材料</button>
    <small v-else-if="!loaded && source.excerpt">此处为已保存材料的节选。</small>
  </div>
</template>
<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { usePrivacyStore } from '~/stores/privacy'
const props = defineProps({ source: { type: Object, required: true }, read: Function })
const { privacyMode } = storeToRefs(usePrivacyStore())
const materialKind = computed(() => {
  const component = props.source.component
  if (component === 'notes') return '人工笔记'
  if (component === 'arrangements') return '人工安排记录'
  if (component === 'conclusions') return '人工结论记录'
  if (component === 'original_report') return '原始 AI 回答'
  if (component?.startsWith('evidence:')) return '报告保存的引用资料'
  if (component === 'source_message') return '附件对应的原消息正文'
  if (component === 'attachment_metadata') return '附件元数据'
  return ({ message: '聊天原文', report: '保存的 AI 报告', extracted_text: '识别/提取文字' })[props.source.material_kind]
})
const text = ref(''), loading = ref(false), loaded = ref(false), error = ref(''), nextOffset = ref(0)
let revision = 0
async function load(offset) {
  const token = ++revision, source = props.source
  loading.value = true; error.value = ''
  try {
    const result = await props.read(source, offset)
    if (token !== revision) return
    text.value = offset === 0 ? result.text : text.value + result.text
    nextOffset.value = result.next_offset; loaded.value = true
  } catch (cause) {
    if (token === revision) error.value = typeof cause.data?.detail === 'string' ? cause.data.detail : cause.message
  } finally { if (token === revision) loading.value = false }
}
watch(() => [props.source.source, props.source.run_id, props.source.run_version, props.read], () => {
  ++revision; text.value = props.source.text; error.value = ''; loaded.value = false; loading.value = false; nextOffset.value = 0
  if (props.read && props.source.run_id) void load(0)
}, { immediate: true })
onBeforeUnmount(() => { ++revision })
</script>
