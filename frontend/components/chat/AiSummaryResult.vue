<template>
  <div v-if="summary">
    <p class="ai-prose">{{ summary.overview }}</p>
    <section v-for="group in groups" :key="group.key">
      <h4 v-if="summary[group.key]?.length" class="ai-section-title">{{ group.label }}</h4>
      <div v-for="(point, i) in summary[group.key] || []" :key="i" class="mb-2">
        <p class="ai-prose">{{ point.text || point.reason }}</p>
        <button v-for="source in point.sources" :key="source" class="ai-source" type="button" @click="$emit('locate', source)">查看原消息</button>
        <button v-if="group.key === 'todos' && account" class="ai-source" type="button" @click="savePoint(point)">保存事项</button>
      </div>
    </section>
    <p v-if="saveError" class="ai-error" role="alert">{{ saveError }}</p>
    <p v-if="saved" role="status">事项已保存，可在“事项状态面板”持续更新。</p>
    <MatterSaveDialog v-if="saving" :account="account" :source="saving.source" :title="saving.title" @close="saving = null" @saved="saved = true" />
  </div>
</template>
<script setup>
import { ref, watch } from 'vue'
import MatterSaveDialog from './MatterSaveDialog.vue'
const props = defineProps({ summary: Object, account: String, sources: { type: Array, default: () => [] } })
defineEmits(['locate'])
const saving = ref(null), saveError = ref(''), saved = ref(false)
watch(() => [props.account, props.summary], () => { saving.value = null; saveError.value = ''; saved.value = false })
const savePoint = point => {
  saveError.value = ''; saved.value = false
  const source = props.sources.find(source => source.source === point.sources[0])
  if (!source) { saveError.value = '这条待办缺少可核对的原消息，请从聊天原文保存。'; return }
  saving.value = { source, title: point.text }
}
const groups = [{ key: 'topics', label: '主要话题' }, { key: 'conclusions', label: '重要结论' }, { key: 'todos', label: '待办事项' }, { key: 'matches', label: '关注命中' }]
</script>
