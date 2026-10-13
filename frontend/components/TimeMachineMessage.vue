<template>
  <article class="time-message" :class="{ 'time-message--sent': item.is_sent, 'privacy-blur': privacy }">
    <header><strong>{{ item.is_sent ? '我' : item.sender_name || (item.kind === 'system' ? '系统消息' : '发送者未识别') }}</strong><time :datetime="new Date(item.create_time * 1000).toISOString()">{{ timestamp }}</time></header>
    <p class="time-conversation">{{ item.conversation_name }}</p>
    <p v-if="item.is_revoked" class="time-revoked">已撤回</p>
    <template v-if="item.kind === 'image'">
      <img v-if="!imageError" :src="imageUrl" alt="聊天记录中的图片" loading="lazy" @error="imageError = true" />
      <p v-else class="time-image-error" role="status">图片暂时无法读取，请在原聊天中检查是否已下载。</p>
    </template>
    <p v-if="item.title" class="time-message-title">{{ item.title }}</p>
    <p v-if="item.content" class="time-message-content">{{ item.content }}</p>
    <blockquote v-if="item.quote_content">{{ item.quote_content }}</blockquote>
    <p v-if="!item.content && !item.title && item.kind !== 'image'" class="time-conversation">{{ kindLabel }} · 打开原消息查看</p>
    <footer><span v-if="item.kind === 'image'">本地可读取的图片，可能为缩略图</span><button type="button" @click="$emit('locate', item)">查看原消息</button></footer>
  </article>
</template>

<script setup>
import { computed, ref } from 'vue';
const props = defineProps({ item: { type: Object, required: true }, imageUrl: { type: String, required: true }, privacy: Boolean });
defineEmits(['locate']);
const imageError = ref(false);
const timestamp = computed(() => new Date(props.item.create_time * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }));
const kindLabel = computed(() => ({ video: '视频', voice: '语音', file: '文件', emoji: '表情', link: '链接', chatHistory: '聊天记录', system: '系统消息' })[props.item.kind] || props.item.kind);
</script>

<style scoped>
.time-message { padding: 18px 0; border-bottom: 1px solid var(--border-color); }
.time-message header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; font-size: 13px; }
.time-message header strong { color: var(--text-primary); overflow-wrap: anywhere; }
.time-message time { color: var(--wx-text-muted); font-size: 12px; white-space: nowrap; }
.time-message--sent header strong { color: var(--chat-accent); }
.time-conversation { margin: 4px 0 8px; color: var(--wx-text-muted); font-size: 12px; overflow-wrap: anywhere; }
.time-revoked { margin: 6px 0; color: var(--wx-red); font-size: 12px; font-weight: 600; }
.time-message-content, .time-message-title { margin: 8px 0; font-size: 14px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.time-message-title { font-weight: 600; }
.time-message img { display: block; width: auto; max-width: 100%; max-height: 280px; margin: 10px 0; object-fit: contain; border-radius: 8px; }
.time-image-error { padding: 14px; background: var(--chat-subtle-bg); font-size: 13px; }
.time-message blockquote { margin: 10px 0; padding-left: 12px; border-left: 2px solid var(--chat-input-border); font-size: 13px; white-space: pre-wrap; overflow-wrap: anywhere; }
.time-message footer { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.time-message footer span { max-width: 180px; font-size: 11px; color: var(--wx-text-muted); }
.time-message button { padding: 4px 0; border: 0; background: transparent; color: var(--chat-accent); cursor: pointer; font-size: 12px; text-decoration: underline; text-underline-offset: 3px; }
</style>
