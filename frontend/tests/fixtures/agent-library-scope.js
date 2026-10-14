import { createApp, h, nextTick, ref } from 'vue'
import { createPinia } from 'pinia'
import ChatAgentPanel from '../../components/chat/ChatAgentPanel.vue'
import '../../assets/css/tailwind.css'

const params = new URLSearchParams(location.search)
document.documentElement.dataset.theme = params.get('theme') || 'light'
const scope = { folder_id: 'submission', folder_name: '论文投稿与修改安排', version_id: 'version-2', version_number: 2, version_name: '提交前核对', saved_at: 1791806400, item_count: 3 }
const source = { kind: 'library', source: 'a'.repeat(24), name: '共同确认的修改清单', title: '共同确认的修改清单', text: '周五前完成结果表格复核；暂定下周一提交，具体时间仍待确认。', material_kind: 'message', verified: true, library_scope: scope, library_item_id: 'item-1', coverage: '保存的聊天原文；只反映保存当时的材料。', original_source: { name: '研究讨论群', sender: '陈同学' } }
const run = { id: 'run', thread_id: 'thread', account: 'restored', version: 1, status: 'completed', library_scope: scope, answer: `根据此保存版本：\n\n1. **周五前完成结果表格复核。**\n2. **下周一提交仍为暂定安排**，材料没有确认最终提交时间。[[${source.source}]]\n\n附件只保存了元数据，尚无其正文提取材料；无法据此判断附件中的结果。`, citations: [source], timeline: [], topic_coverage: { total_items: 3, read_items: 2, read_characters: 280, total_characters: 430, complete: false, attachment_metadata_only: 1 } }
const thread = { id: 'thread', account: 'restored', origin: 'agent', username: '', title: '投稿前还有哪些安排需要确认？', library_scope: scope, latest_run: 'run', messages: [{ id: 'question', role: 'user', text: '投稿前还有哪些安排需要确认？', run_id: 'run' }] }
const state = ref({ selected: { 'page:restored:agent': 'thread' }, drafts: {}, pinned: {} })
Object.assign(globalThis, {
  ref, useState: () => state, useApiBase: () => '/fixture',
  useSettingsDialog: () => ({ openDialog() {} }),
  $fetch: async path => {
    if (path.endsWith('/folders')) return [{ id: 'submission', name: scope.folder_name }]
    if (path.endsWith('/dossier')) return { folder_name: scope.folder_name, versions: [{ id: scope.version_id, number: 2, name: scope.version_name, created_at: scope.saved_at, item_count: 3 }] }
    throw new Error(`Unexpected fixture request: ${path}`)
  },
  useAiApi: () => ({ agentEvents: () => () => {}, request: async path => {
    if (path === '/settings') return { profiles: [{ id: 'local', name: '本地模型 · 合成演示', model: 'local' }], selected_model: { profile_id: 'local', model_id: 'local' } }
    if (path.includes('/model-capabilities')) return { metadata: {} }
    if (path === '/agent/threads') return [thread]
    if (path === '/agent/threads/thread') return thread
    if (path === '/agent/runs/run') return run
    if (path.includes('/materials/')) return { ...source, offset: 0, next_offset: null }
    throw new Error(`Unexpected fixture request: ${path}`)
  } }),
})
const app = createApp({ render: () => h('main', { style: 'height:100vh;display:flex;flex-direction:column' }, [h('p', { style: 'font:12px sans-serif;padding:6px 16px;background:#eef2ee;color:#283b34;margin:0' }, '合成界面验收 · 没有模型调用或真实聊天读取'), h(ChatAgentPanel, { account: 'restored', presentation: 'page', canReadChats: false })]) })
app.use(createPinia()).mount('#app')
await nextTick()
setTimeout(() => document.querySelector(params.has('picker') ? '[aria-label="选择专题版本"]' : '.agent-ref')?.click(), 300)
