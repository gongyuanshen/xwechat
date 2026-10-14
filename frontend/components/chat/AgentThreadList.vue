<template>
  <nav class="agent-thread-list" aria-label="AI 会话列表">
    <header><strong><MessageCircleMore :size="16" :stroke-width="1.8" aria-hidden="true" />AI 助手</strong><button type="button" class="agent-thread-new" aria-label="新对话" title="新对话" @click="$emit('new')"><SquarePen :size="16" :stroke-width="1.8" aria-hidden="true" /></button></header>
    <label class="agent-thread-search"><Search :size="16" :stroke-width="1.8" aria-hidden="true" /><input v-model="query" aria-label="搜索 AI 会话" placeholder="搜索会话" /></label>
    <div class="agent-thread-list-heading"><span>{{ heading }}</span><button type="button" aria-label="刷新会话列表" :disabled="loading" @click="$emit('refresh')"><component :is="loading ? LoaderCircle : RotateCw" :class="loading ? 'agent-icon-spin' : undefined" :size="16" :stroke-width="1.8" aria-hidden="true" /></button></div>
    <p v-if="error" class="agent-thread-error" role="alert">{{ error }}</p>
    <div class="agent-thread-items" :aria-busy="loading">
      <p v-if="loading && !items.length" class="agent-thread-empty" role="status">正在加载会话…</p>
      <p v-else-if="!filtered.length" class="agent-thread-empty">{{ query ? '没有匹配的会话' : '还没有对话，点击上方开始。' }}</p>
      <article v-for="item in filtered" :key="item.id" class="agent-thread-item" :class="{ 'is-current': item.id === current, 'has-menu': menu === item.id, 'is-running': isRunning(item) }">
        <button type="button" class="agent-thread-select" :aria-current="item.id === current ? 'page' : undefined" :title="item.title || '新的对话'" @click="$emit('select', item)"><span>{{ item.title || '新的对话' }}</span><small><AgentAvatar v-if="item.username" class="agent-thread-avatar" :path="avatarFor(item.username)" :name="nameFor(item.username)" /><span :class="{ 'privacy-blur': item.library_scope && privacyMode }">{{ descriptionFor ? descriptionFor(item) : nameFor(item.username) }}</span></small></button>
        <div class="agent-thread-actions">
          <span v-if="isRunning(item)" class="agent-thread-running" role="status" :aria-label="`${item.title || '新的对话'}：正在处理`" title="正在处理"><LoaderCircle class="agent-icon-spin" :size="16" :stroke-width="1.8" aria-hidden="true" /></span>
          <button type="button" class="agent-thread-more" :aria-label="`管理会话：${item.title || '新的对话'}`" aria-haspopup="menu" :aria-controls="menu === item.id ? menuId : undefined" :aria-expanded="menu === item.id" @click="openMenu(item, $event)"><Ellipsis :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
        </div>
      </article>
    </div>
    <div v-if="selected" :id="menuId" ref="popup" popover="auto" class="agent-thread-management" :class="{ 'is-confirming': editing || deleting }" :role="editing || deleting ? 'dialog' : 'menu'" :aria-label="editing ? '重命名对话' : deleting ? '删除对话' : '会话操作'" @keydown="onKeydown" @toggle="onToggle">
      <form v-if="editing" @submit.prevent="!busy && title.trim() && $emit('rename', selected, title.trim())">
        <label :for="`${menuId}-title`">重命名对话</label>
        <input :id="`${menuId}-title`" v-model="title" aria-label="对话新名称" maxlength="100" :disabled="busy" />
        <div class="agent-thread-menu-actions"><button type="submit" :disabled="busy || !title.trim()">保存名称</button><button type="button" @click="closeMenu(true)">取消</button></div>
      </form>
      <template v-else-if="deleting">
        <strong>删除这段对话？</strong>
        <p>删除后无法恢复{{ isRunning(selected) ? '，正在运行的任务也会停止' : '' }}。</p>
        <div class="agent-thread-menu-actions"><button type="button" class="is-destructive" :disabled="busy" @click="$emit('delete', selected)">确认删除对话</button><button type="button" @click="closeMenu(true)">取消</button></div>
      </template>
      <template v-else>
        <button type="button" role="menuitem" @click="showForm('rename')"><SquarePen :size="16" :stroke-width="1.8" aria-hidden="true" />重命名</button>
        <button type="button" role="menuitem" class="is-destructive" @click="showForm('delete')"><Trash2 :size="16" :stroke-width="1.8" aria-hidden="true" />删除对话</button>
      </template>
    </div>
    <footer><button type="button" @click="$emit('settings')"><SlidersHorizontal :size="16" :stroke-width="1.8" aria-hidden="true" />模型与服务</button></footer>
  </nav>
</template>
<script setup>
import { computed, getCurrentInstance, nextTick, onBeforeUnmount, onDeactivated, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { usePrivacyStore } from '~/stores/privacy'
import AgentAvatar from './AgentAvatar.vue'
import { Ellipsis, LoaderCircle, MessageCircleMore, RotateCw, Search, SlidersHorizontal, SquarePen, Trash2 } from '@lucide/vue'
const props = defineProps({ heading: { type: String, default: '当前聊天的对话' }, descriptionFor: Function, items: { type: Array, default: () => [] }, current: String, runningIds: { type: Array, default: () => [] }, loading: Boolean, busy: Boolean, error: String, nameFor: { type: Function, default: value => value }, avatarFor: { type: Function, default: () => '' } })
const { privacyMode } = storeToRefs(usePrivacyStore())
defineEmits(['new', 'select', 'rename', 'delete', 'refresh', 'settings'])
const isRunning = item => props.runningIds.includes(item.id)
const query = ref(''), menu = ref(''), editing = ref(''), deleting = ref(''), title = ref('')
const popup = ref(null)
const menuId = `agent-thread-menu-${getCurrentInstance().uid}`
let trigger = null
let revision = 0
const filtered = computed(() => props.items.filter(item => `${item.title} ${props.descriptionFor ? props.descriptionFor(item) : props.nameFor(item.username)}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
const selected = computed(() => filtered.value.find(item => item.id === menu.value))

function closeMenu(restoreFocus = false) {
  revision++
  const previousTrigger = trigger
  menu.value = ''; editing.value = ''; deleting.value = ''; trigger = null
  document.removeEventListener('pointerdown', onOutside, true)
  document.removeEventListener('scroll', onScroll, true)
  window.removeEventListener('resize', onResize)
  if (restoreFocus && previousTrigger?.isConnected) previousTrigger.focus()
}

function positionMenu() {
  if (!popup.value || !trigger?.isConnected) return
  const rect = trigger.getBoundingClientRect()
  const gap = 4, edge = 8
  const width = Math.min(editing.value || deleting.value ? 260 : 176, window.innerWidth - edge * 2)
  const style = popup.value.style
  style.width = `${width}px`
  style.maxHeight = `${window.innerHeight - edge * 2}px`
  const height = popup.value.getBoundingClientRect().height
  // 浮层进入浏览器顶层，靠近视口底部时向上展开，不改变会话列表布局。
  const below = rect.bottom + gap
  const top = below + height <= window.innerHeight - edge ? below : rect.top - gap - height
  style.left = `${Math.max(edge, Math.min(rect.right - width, window.innerWidth - width - edge))}px`
  style.top = `${Math.max(edge, Math.min(top, window.innerHeight - height - edge))}px`
}

async function openMenu(item, event) {
  if (menu.value === item.id) { closeMenu(true); return }
  closeMenu()
  trigger = event.currentTarget
  menu.value = item.id
  const currentRevision = revision
  await nextTick()
  if (currentRevision !== revision || !popup.value) return
  popup.value.showPopover?.()
  positionMenu()
  popup.value.querySelector('[role="menuitem"]')?.focus()
  document.addEventListener('pointerdown', onOutside, true)
  document.addEventListener('scroll', onScroll, true)
  window.addEventListener('resize', onResize)
}

async function showForm(action) {
  if (action === 'rename') { editing.value = menu.value; title.value = selected.value?.title || '' }
  else deleting.value = menu.value
  const currentRevision = revision
  await nextTick()
  if (currentRevision !== revision || !popup.value) return
  positionMenu()
  // 删除确认默认聚焦取消，避免连续按回车误删。
  const target = editing.value ? popup.value.querySelector('input') : popup.value.querySelector('button:last-child')
  target?.focus()
  if (editing.value) target?.select()
}

function onOutside(event) {
  if (!popup.value?.contains(event.target) && !trigger?.contains(event.target)) closeMenu()
}
function onScroll(event) { if (!popup.value?.contains(event.target)) closeMenu() }
function onResize() { closeMenu() }
function onToggle(event) { if (event.newState === 'closed' && event.target === popup.value) closeMenu(true) }
function onKeydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeMenu(true); return }
  if (!editing.value && !deleting.value) {
    if (event.key === 'Tab') { closeMenu(true); return }
    const items = [...popup.value.querySelectorAll('[role="menuitem"]')]
    const index = items.indexOf(document.activeElement)
    const next = { ArrowDown: (index + 1) % items.length, ArrowUp: (index + items.length - 1) % items.length, Home: 0, End: items.length - 1 }[event.key]
    if (next !== undefined) { event.preventDefault(); items[next]?.focus() }
  } else if (event.key === 'Tab') {
    const items = [...popup.value.querySelectorAll('input:not(:disabled), button:not(:disabled)')]
    if ((!event.shiftKey && document.activeElement === items.at(-1)) || (event.shiftKey && document.activeElement === items[0])) closeMenu(true)
  }
}
watch(() => props.busy, (busy, wasBusy) => { if (wasBusy && !busy && !props.error) closeMenu(true) })
watch(() => props.current, () => closeMenu())
watch(selected, item => { if (menu.value && !item) closeMenu() })
onBeforeUnmount(() => closeMenu())
onDeactivated(() => closeMenu())
</script>
