<template>
  <details class="dossier-evidence" :open="expanded">
    <summary><span :class="{ 'privacy-blur': privacyMode }">{{ item.title }}</span> <small>{{ kindLabel }} · {{ item.verified ? '已人工核验' : '待人工核验' }}</small></summary>
    <AgentAnswer v-if="item.kind === 'report'" :class="{ 'privacy-blur': privacyMode }" :text="item.content"
      :citations="item.report.citations" :references="item.report.references" @locate="$emit('locate', $event)" />
    <pre v-else :class="{ 'privacy-blur': privacyMode }">{{ item.content }}</pre>
    <p v-if="item.notes" :class="{ 'privacy-blur': privacyMode }">备注：{{ item.notes }}</p>
    <p v-if="item.tags.length" :class="{ 'privacy-blur': privacyMode }">标签：{{ item.tags.join('、') }}</p>
    <p v-for="warning in item.report?.coverage_warnings" :key="warning" :class="{ 'privacy-blur': privacyMode }">{{ warning }}</p>
    <details v-if="item.report?.evidence?.length" class="saved-evidence">
      <summary>此报告保留的原文证据（{{ item.report.evidence.length }}）</summary>
      <article v-for="source in item.report.evidence" :key="source.source">
        <p :class="{ 'privacy-blur': privacyMode }">{{ source.name || source.username }} · {{ source.sender }}</p>
        <pre :class="{ 'privacy-blur': privacyMode }">{{ source.text }}</pre>
        <button v-if="source.username && source.anchor" type="button" @click="$emit('locate', source)">打开原消息</button>
      </article>
    </details>
    <template v-if="item.kind === 'report' && item.content !== item.report.answer">
      <h5>保存时的原始回答</h5>
      <AgentAnswer :class="{ 'privacy-blur': privacyMode }" :text="item.report.answer"
        :citations="item.report.citations" :references="item.report.references" @locate="$emit('locate', $event)" />
    </template>
    <p v-if="item.kind === 'attachment'" :class="{ 'privacy-blur': privacyMode }">{{ item.attachment.name }} · {{ item.attachment.preservation_note }}</p>
    <p v-if="item.source" class="source" :class="{ 'privacy-blur': privacyMode }">来源会话：{{ item.source.username }}<template v-if="item.source.sender"> · {{ item.source.sender }}</template></p>
    <div class="evidence-actions">
      <button v-if="item.source?.username && item.source?.anchor" type="button" @click="$emit('locate', item.source)">打开原消息</button>
      <button v-if="item.kind === 'attachment'" :disabled="busy" type="button" @click="$emit('download', { item, versionId })">下载附件副本</button>
    </div>
  </details>
</template>

<script setup>
import { computed } from 'vue';
import AgentAnswer from '~/components/chat/AgentAnswer.vue';
const props = defineProps({ item: { type: Object, required: true }, privacyMode: Boolean, expanded: Boolean, busy: Boolean, versionId: String });
defineEmits(['locate', 'download']);
const kindLabel = computed(() => ({ message: '聊天原文', report: 'AI 报告', attachment: '附件副本', extracted_text: '识别/提取文字' })[props.item.kind]);
</script>

<style scoped>
.dossier-evidence { min-width: 0; padding: 12px 0; border-bottom: 1px solid var(--app-border); overflow-wrap: anywhere; }
summary { cursor: pointer; font-weight: 500; }
small, .source { font-size: 12px; color: var(--app-text-secondary); font-weight: 400; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.7; margin: 12px 0; }
p { white-space: pre-wrap; margin: 10px 0; }
h5 { margin-top: 16px; font-weight: 600; }
.evidence-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
button { border: 1px solid var(--app-border); border-radius: 6px; padding: 6px 10px; background: transparent; color: inherit; font: inherit; }
button:hover { background: var(--app-surface-muted); }
button:disabled { opacity: .5; }
button:focus-visible, summary:focus-visible { outline: 2px solid var(--app-accent); outline-offset: 2px; }
</style>
