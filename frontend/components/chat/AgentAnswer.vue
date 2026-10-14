<template>
  <div ref="answer" class="agent-answer">
    <div class="agent-markdown" v-html="rendered" @error.capture="hideMissingAvatar" @click="onReference" @pointerover="hoverReference" @pointerout="leaveReference" @focusin="hoverReference" @focusout="leaveReference" />
    <div v-if="selected" :key="selected.source" :id="previewId" ref="preview" popover="manual" class="agent-citation-preview" role="dialog" aria-label="消息来源预览" @pointerenter="cancelClose" @pointerleave="leaveReference" @keydown.esc.stop.prevent="closePreview(true)">
      <header><AgentAvatar v-if="selected.kind !== 'library'" :class="{ 'privacy-blur': privacyMode }" :path="selected.sender_avatar_path" :name="selected.sender" /><div :class="{ 'privacy-blur': privacyMode }"><template v-if="selected.kind === 'library'"><strong>{{ selected.title }}</strong></template><template v-else><strong>{{ selected.sender }}</strong><small>{{ selected.name || selected.username }} · {{ selected.time ? new Date(selected.time * 1000).toLocaleString() : '未知时间' }}</small></template></div><button type="button" aria-label="关闭来源预览" @click="closePreview(true)"><X :size="16" :stroke-width="1.8" aria-hidden="true" /></button></header>
      <AgentLibraryScopeNotice v-if="selected.kind === 'library'" :scope="selected.library_scope" />
      <AgentLibraryMaterial v-if="selected.kind === 'library'" :source="selected" :read="pinned ? navigation?.readMaterial : undefined" />
      <template v-else><p class="agent-citation-text" :class="{ 'privacy-blur': privacyMode }">{{ selected.text }}</p><small v-if="selected.excerpt">此处为原文节选，可定位查看完整消息。</small></template>
      <p v-if="locateError" class="agent-citation-error" role="alert">{{ locateError }}</p>
      <button v-if="selected.kind !== 'library'" type="button" class="agent-citation-locate" :disabled="locating" :aria-busy="locating" :aria-label="locating ? '正在定位原消息' : locateError ? '重试定位原消息' : '定位原消息'" @click="locateSelected">
        <LoaderCircle v-if="locating" class="agent-icon-spin" :size="16" :stroke-width="1.8" aria-hidden="true" />
        <Check v-else-if="located" :size="16" :stroke-width="1.8" aria-hidden="true" />
        <ExternalLink v-else :size="16" :stroke-width="1.8" aria-hidden="true" />
        <span role="status">{{ locating ? '正在定位…' : located ? '已定位原消息' : locateError ? '重试定位' : '定位原消息' }}</span>
      </button>
    </div>
    <AgentImageViewer v-if="selectedImage" :selected="selectedImage" :images="answerImages" :citations="citations" :api-base="apiBase" :locating="locatingImage" :locate-error="imageLocateError" @close="selectedImage = null" @locate="locateImage" />
    <Teleport to="body">
      <div v-if="personProfileOpen" ref="personProfileHost" class="agent-person-profile" :style="personProfileStyle">
        <ContactProfileCard :state="profileState" />
      </div>
    </Teleport>
  </div>
</template>
<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onDeactivated, ref, unref, useId, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { usePrivacyStore } from '~/stores/privacy'
import { renderAgentMarkdown, referenceUrl } from '~/utils/agentMarkdown'
import { useApiBase } from '~/composables/useApiBase'
import { Check, ExternalLink, LoaderCircle, X } from '@lucide/vue'
import AgentImageViewer from './AgentImageViewer.vue'
import AgentAvatar from './AgentAvatar.vue'
import AgentLibraryScopeNotice from './AgentLibraryScopeNotice.vue'
import AgentLibraryMaterial from './AgentLibraryMaterial.vue'
import ContactProfileCard from './ContactProfileCard.vue'
const props = defineProps({ text: { type: String, default: '' }, citations: { type: Array, default: () => [] }, streaming: Boolean, references: { type: Array, default: () => [] } })
const emit = defineEmits(['locate'])
const { privacyMode } = storeToRefs(usePrivacyStore())
const apiBase = useApiBase(), selectedImage = ref(null)
const profileState = inject('chatContactProfileState', null)
const componentId = useId()
const personCardPrefix = `mention:${componentId}:`
const answerReferences = computed(() => {
  const references = (props.references || []).map(item => ({ ...item }))
  const people = new Map(references.filter(item => item.kind === 'person' && item.username).map(item => [item.username, item]))
  for (const source of (props.citations || [])) {
    const username = String(source?.sender_id || '').trim()
    const name = String(source?.sender || '').trim()
    const id = String(source?.source || '').toLowerCase()
    if (!username || name.length < 2 || !/^[a-f0-9]{24}$/.test(id)) continue
    const current = people.get(username)
    if (current) {
      current.sources = [...new Set([...(current.sources || []), id])]
      current.mentioned_sources = [...new Set([...(current.mentioned_sources || []), id])]
      if (!current.avatar_path && source.sender_avatar_path) current.avatar_path = source.sender_avatar_path
      continue
    }
    const person = { id, kind: 'person', username, name, avatar_path: source.sender_avatar_path || '', sources: [id], mentioned_sources: [id] }
    references.push(person)
    people.set(username, person)
  }
  return references
})
// 缺少头像时保留人名和编号，避免将浏览器破图图标显示为人物头像。
const hideMissingAvatar = event => { if (event.target?.tagName === 'IMG') event.target.style.display = 'none' }
const locatingImage = ref(false), imageLocateError = ref('')
const answerImages = computed(() => [...new Set([...props.text.matchAll(/\[\[image:([a-f0-9]{24})\]\]/gi)].map(m => m[1].toLowerCase()))].map(id => (props.references || []).find(r => r.kind === 'image' && r.id === id)).filter(Boolean))
const locateImage = async source => {
  if (locatingImage.value) return
  locatingImage.value = true; imageLocateError.value = ''
  try {
    if (navigation?.locate) {
      if (await navigation.locate(source) === false) throw new Error('定位未完成，请重试')
    } else emit('locate', source)
    selectedImage.value = null
  } catch (error) { imageLocateError.value = error?.message || '暂时无法定位，请重试' }
  finally { locatingImage.value = false }
}
const pinned = ref(false)
let closeTimer
const cancelClose = () => clearTimeout(closeTimer)
const personDetails = button => answerReferences.value.find(item => item.kind === 'person' && item.id === button?.dataset.person)
const openPersonProfile = button => {
  const person = personDetails(button)
  if (!profileState || !person?.username) return
  const cardId = personCardPrefix + person.username
  if (String(unref(profileState.contactProfileCardMessageId) || '') !== cardId) profileState.closeContactProfileCard?.()
  personAnchor = button
  profileState.onMentionMouseEnter?.({ id: componentId }, {
    username: person.username,
    displayName: person.name,
    avatar: referenceUrl(person.avatar_path, apiBase)
  })
}
const leaveReference = event => {
  const person = event.target.closest('button[data-person]')
  if (person) {
    if (!person.contains(event.relatedTarget)) profileState?.onMentionMouseLeave?.()
    return
  }
  if (!pinned.value && !preview.value?.contains(event.relatedTarget)) { cancelClose(); closeTimer = setTimeout(() => closePreview(), 180) }
}
const hoverReference = event => {
  const person = event.target.closest('button[data-person]')
  if (person) {
    if (!person.contains(event.relatedTarget)) openPersonProfile(person)
    return
  }
  cancelClose(); if (!pinned.value && event.target.closest('button[data-source]') !== trigger) void onCitation(event, false)
}
const onReference = event => {
  const image = event.target.closest('button[data-image]')
  if (image && answerImages.value.some(r => r.id === image.dataset.image)) { imageLocateError.value = ''; selectedImage.value = image.dataset.image; return }
  const person = event.target.closest('button[data-person]')
  if (person) {
    closePersonProfile()
    const ref = answerReferences.value.find(r => r.kind === 'person' && r.id === person.dataset.person)
    const mentioned = ref?.mentioned_sources || [], related = ref?.sources || []
    const cited = [...props.text.matchAll(/\[\[([a-f0-9]{24})\]\]/gi)].map(m => m[1].toLowerCase())
    // 先用这条回答实际引用的证据，避免任务里的无关旧消息抢在当前出处前面。
    const source = [...cited.filter(id => mentioned.includes(id)), ...cited.filter(id => related.includes(id)),
      ...mentioned, ...related].map(id => (props.citations || []).find(c => c.source === id)).find(Boolean)
    if (source) void onCitation({ target: person }, true, source)
    return
  }
  void onCitation(event, true)
}
const navigation = inject('agentSourceNavigation', null)
const answer = ref(null), preview = ref(null), selected = ref(null)
const previewId = `agent-source-${componentId}`
const personProfileHost = ref(null), personProfileStyle = ref({})
let personAnchor = null, personProfileObserver = null
const personProfileOpen = computed(() => !!profileState
  && !!unref(profileState.contactProfileCardOpen)
  && String(unref(profileState.contactProfileCardMessageId) || '').startsWith(personCardPrefix))
const closePersonProfile = () => {
  if (String(unref(profileState?.contactProfileCardMessageId) || '').startsWith(personCardPrefix)) profileState?.closeContactProfileCard?.()
}
const positionPersonProfile = () => {
  const host = personProfileHost.value
  if (!host || !personAnchor?.isConnected) return
  const anchor = personAnchor.getBoundingClientRect()
  const card = host.getBoundingClientRect()
  const width = card.width || Math.min(400, window.innerWidth - 16)
  const height = card.height || 0
  const right = anchor.right + 8
  const left = right + width <= window.innerWidth - 8 ? right : anchor.left - width - 8
  const next = {
    left: `${Math.max(8, Math.min(left, window.innerWidth - width - 8))}px`,
    top: `${Math.max(8, Math.min(anchor.top, window.innerHeight - height - 8))}px`
  }
  if (personProfileStyle.value.left !== next.left || personProfileStyle.value.top !== next.top) personProfileStyle.value = next
}
const stopPersonProfilePositioning = () => {
  personProfileObserver?.disconnect(); personProfileObserver = null
  window.removeEventListener('scroll', positionPersonProfile, true)
  window.removeEventListener('resize', positionPersonProfile)
}
watch(personProfileHost, element => {
  stopPersonProfilePositioning()
  if (!element) return
  positionPersonProfile()
  if (typeof ResizeObserver === 'function') {
    personProfileObserver = new ResizeObserver(positionPersonProfile)
    personProfileObserver.observe(element)
  }
  window.addEventListener('scroll', positionPersonProfile, true)
  window.addEventListener('resize', positionPersonProfile)
})
const selectedNumber = ref(0), locating = ref(false), located = ref(false), locateError = ref('')
let trigger = null, observer = null, revision = 0
// 原始 HTML、远程图片和自动链接均禁用；只渲染本地已核验的来源按钮。
const rendered = computed(() => renderAgentMarkdown(props.text, props.citations || [], props.streaming, answerReferences.value, apiBase))
const closePreview = (restoreFocus = false) => {
  ++revision; cancelClose(); pinned.value = false
  observer?.disconnect(); observer = null
  window.removeEventListener('scroll', positionPreview, true)
  window.removeEventListener('resize', positionPreview)
  window.removeEventListener('pointerdown', dismissOutside, true)
  window.removeEventListener('keydown', dismissEscape)
  trigger?.setAttribute('aria-expanded', 'false')
  trigger?.removeAttribute('aria-controls')
  if (restoreFocus && trigger?.isConnected) trigger.focus({ preventScroll: true })
  selected.value = null
  trigger = null
}
const positionPreview = () => {
  const el = preview.value
  if (!el || !trigger) return
  if (!trigger.isConnected) { closePreview(); return }
  const rect = trigger.getBoundingClientRect()
  const container = answer.value.closest('.agent-conversation')?.getBoundingClientRect()
  const top = Math.max(8, container?.top ?? 8), bottom = Math.min(window.innerHeight - 8, container?.bottom ?? window.innerHeight - 8)
  const left = Math.max(8, container?.left ?? 8), right = Math.min(window.innerWidth - 8, container?.right ?? window.innerWidth - 8)
  if (rect.bottom < top || rect.top > bottom || rect.right < left || rect.left > right) { closePreview(); return }
  // 浏览器顶层浮层不受表格、滚动容器裁切；根据编号附近的空间向上或向下展开。
  el.style.width = `${Math.min(320, right - left)}px`
  const below = bottom - rect.bottom - 8, above = rect.top - top - 8
  const text = el.querySelector('.agent-citation-text')
  const naturalHeight = el.scrollHeight + Math.max(0, (text?.scrollHeight || 0) - (text?.clientHeight || 0))
  const showBelow = below >= Math.min(naturalHeight, 320) || below >= above
  el.style.maxHeight = `${Math.max(0, showBelow ? below : above)}px`
  el.style.left = `${Math.max(left, Math.min(rect.left, right - el.offsetWidth))}px`
  el.style.top = `${showBelow ? rect.bottom + 8 : Math.max(top, rect.top - el.offsetHeight - 8)}px`
}
// 悬停先打开、点击再固定；手动控制顶层浮层，避免原生轻触关闭与点击事件互相竞争。
const dismissOutside = event => { if (!preview.value?.contains(event.target) && !trigger?.contains(event.target)) closePreview() }
const dismissEscape = event => { if (event.key === 'Escape') { event.preventDefault(); closePreview(true) } }
const onCitation = async (event, pin = true, personSource = null) => {
  const button = event.target.closest('button[data-source], button[data-person]')
  const source = personSource || (props.citations || []).find(c => c.source === button?.dataset.source)
  if (!source) return
  // 人物文字不是来源序号；按实际渲染的编号查找，未编号的关联原文用 0 表示。
  const numbered = [...new Set([...answer.value.querySelectorAll('button[data-source]')].map(el => el.dataset.source))]
  const sourceNumber = personSource ? numbered.indexOf(source.source) + 1 : Number(button.textContent)
  // 大视图使用统一的出处栏，窄侧栏继续使用编号旁的浮层。
  if (pin && navigation?.inspect?.(source, sourceNumber, button)) {
    closePreview()
    return
  }
  if (trigger === button && selected.value) {
    if (pinned.value && pin) closePreview(true)
    else {
      pinned.value = pin
      if (pin && source.kind !== 'library') Promise.resolve().then(() => navigation?.prepare?.(source)).catch(() => {})
    }
    return
  }
  closePreview()
  trigger = button; pinned.value = pin
  selected.value = source; selectedNumber.value = sourceNumber
  locating.value = false; located.value = false; locateError.value = ''
  const current = revision
  // 悬停只展示已随回答返回的摘要；用户点击固定后才读取原消息上下文，避免扫过编号时形成请求风暴。
  if (pin && source.kind !== 'library') Promise.resolve().then(() => navigation?.prepare?.(source)).catch(() => {})
  await nextTick()
  if (current !== revision || !preview.value) return
  button.setAttribute('aria-expanded', 'true'); button.setAttribute('aria-controls', previewId)
  preview.value.showPopover?.()
  positionPreview()
  if (!selected.value) return
  window.addEventListener('scroll', positionPreview, true)
  window.addEventListener('resize', positionPreview)
  window.addEventListener('pointerdown', dismissOutside, true)
  window.addEventListener('keydown', dismissEscape)
  observer = new ResizeObserver(positionPreview)
  observer.observe(preview.value)
  const container = answer.value.closest('.agent-conversation')
  if (container) observer.observe(container)
  if (pin) preview.value.querySelector('button')?.focus({ preventScroll: true })
}
const locateSelected = async () => {
  if (locating.value || !selected.value) return
  const current = revision, source = selected.value
  locating.value = true; locateError.value = ''
  try {
    if (navigation?.locate) {
      const result = await navigation.locate(source)
      if (result === false) throw new Error('定位未完成，请重试')
      if (current === revision) located.value = true
    } else emit('locate', source)
  } catch (error) {
    if (current === revision) locateError.value = error?.message || '暂时无法定位，请重试'
  } finally {
    if (current === revision) { locating.value = false; await nextTick(); positionPreview() }
  }
}
watch(() => props.text, async () => {
  if (!selected.value) return
  const key = trigger?.dataset.source, person = trigger?.dataset.person
  await nextTick()
  trigger = answer.value?.querySelector(key ? `button[data-source="${key}"]` : `button[data-person="${person}"]`)
  if (!trigger) closePreview(); else { trigger.setAttribute('aria-expanded', 'true'); trigger.setAttribute('aria-controls', previewId); positionPreview() }
})
const closeTransientViews = () => { closePreview(); closePersonProfile(); stopPersonProfilePositioning(); selectedImage.value = null }
onDeactivated(closeTransientViews)
onBeforeUnmount(closeTransientViews)
</script>
