<template>
  <div class="qa-notice">自动回复隔离验收 · 全部为虚构数据 · 不连接后端、模型或微信</div>
  <main v-if="mode === 'settings'" class="qa-settings"><AiSettings /></main>
  <div v-else-if="mode === 'settings-dialog'" class="qa-dialog-overlay">
    <!-- Match the real SettingsDialog clipping and scroll containers. -->
    <div class="qa-dialog-panel">
      <aside class="qa-dialog-sidebar"><h2>设置</h2></aside>
      <div class="qa-dialog-content">
        <header>AI 服务</header>
        <main class="qa-dialog-scroll"><AiSettings /></main>
      </div>
    </div>
  </div>
  <main v-else class="qa-chat"><h2>产品测试群</h2><p>仅检查真实输入区控件的布局与状态展示。</p><MessageInputWorkspace :state="state" :api="{}" /></main>
</template>
<script setup>
import { reactive } from 'vue'
import AiSettings from '../../components/AiSettings.vue'
import MessageInputWorkspace from '../../components/chat/MessageInputWorkspace.vue'
const mode = new URLSearchParams(location.search).get('mode') || 'settings'
const state = reactive({ selectedAccount: '演示数据账号', selectedContact: { username: 'group@chatroom', name: '产品测试群' } })
</script>
<style>
body { margin: 0; font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif; background: var(--app-surface-soft); color: var(--app-text-primary); }
.qa-notice { padding: 14px 20px; font-size: 12px; text-align: center; color: var(--app-text-secondary); }
.qa-settings { max-width: 920px; margin: 0 auto 32px; padding: 24px; background: var(--app-surface-bg); border: 1px solid var(--app-border); border-radius: 12px; }
.qa-dialog-overlay { position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; padding: 16px; box-sizing: border-box; background: #0000001a; }
.qa-dialog-panel { display: flex; height: 88vh; min-height: 380px; width: 100%; max-width: 1000px; overflow: hidden; border-radius: 10px; border: 1px solid #e2e2e2; background: white; }
.qa-dialog-sidebar { display: none; width: 160px; flex-shrink: 0; background: #fcfcfc; border-right: 1px solid #eee; }
.qa-dialog-sidebar h2, .qa-dialog-content > header { padding: 16px; }
.qa-dialog-content { display: flex; min-width: 0; flex: 1; flex-direction: column; }
.qa-dialog-content > header { flex-shrink: 0; }
.qa-dialog-scroll { flex: 1; overflow-y: auto; padding: 4px 16px 32px; }
@media (min-width: 640px) { .qa-dialog-sidebar { display: block; } .qa-dialog-scroll { padding-inline: 24px; } }
.qa-chat { container-type: inline-size; max-width: 1040px; margin: 24px auto; padding: 24px 0; background: var(--chat-page-bg); }
.qa-chat h2 { margin: 0 24px 8px; font-size: 18px; }
.qa-chat > p { margin: 0 24px 80px; color: var(--app-text-secondary); font-size: 13px; }
@media (max-width: 700px) { .qa-settings { margin: 0 10px 20px; padding: 16px; } }
</style>
