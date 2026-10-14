<template>
  <div class="agent-library-notice">
    <p :class="{ 'privacy-blur': privacyMode }">专题：{{ scope.folder_name }} · 第 {{ scope.version_number }} 版 · {{ scope.version_name }}</p>
    <p v-if="coverage">本对话已读取 {{ coverage.read_items }} / {{ coverage.total_items }} 份资料 · {{ coverage.read_characters }} / {{ coverage.total_characters }} 字符 · {{ coverage.complete ? '本版保存文字已完整读取' : '尚未完整读取本版保存文字' }}<template v-if="coverage.attachment_metadata_only"> · {{ coverage.attachment_metadata_only }} 份附件仅含元数据，未读取附件正文</template></p>
  </div>
</template>
<script setup>
import { storeToRefs } from 'pinia'
import { usePrivacyStore } from '~/stores/privacy'
defineProps({ scope: { type: Object, required: true }, coverage: Object })
const { privacyMode } = storeToRefs(usePrivacyStore())
</script>
<style scoped>
.agent-library-notice { color: var(--app-text-secondary); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; margin: 8px 0; }
.agent-library-notice p { margin: 0; }
</style>
