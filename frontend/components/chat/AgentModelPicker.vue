<script setup>
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { Check, ChevronDown, ChevronLeft, ChevronRight, RotateCcw } from '@lucide/vue'

const props = defineProps({ modelValue: { type: Object, default: () => ({}) }, profiles: { type: Array, default: () => [] }, profilesLoading: Boolean, profilesError: String })
const emit = defineEmits(['update:modelValue', 'refresh'])
const api = useAiApi()
const menu = ref(null), center = ref(null), back = ref(null), view = ref('strength')
const catalogs = ref({}), errors = ref({}), loading = ref({}), manual = ref(''), manualProfile = ref(''), manualOpen = ref(false)
const capabilityCache = ref({}), capabilityLoading = ref(false), capabilityError = ref(''), draft = ref(null)
const profile = computed(() => props.profiles.find(p => p.id === props.modelValue.profile_id))
const modelId = computed(() => props.modelValue.model_id || profile.value?.model || '')
const identity = computed(() => JSON.stringify([profile.value?.id, profile.value?.revision, profile.value?.base_url, profile.value?.protocol, modelId.value]))
// 当前配置已合并手动能力；上游的简略模型列表不能覆盖已确认的能力。
const metadata = computed(() => capabilityCache.value[identity.value] || (modelId.value === profile.value?.model ? profile.value?.model_metadata : catalogs.value[profile.value?.id]?.find(m => m.id === modelId.value)) || {})
const modelName = computed(() => profile.value ? metadata.value.name || modelId.value : '选择模型')
const labels = { none: '直接回答', minimal: '极低', low: '低', medium: '中', high: '高', xhigh: '极高', max: '最高' }
const capability = computed(() => metadata.value.reasoning_controls || {
  efforts: metadata.value.reasoning_efforts || metadata.value.reasoning_options?.find(item => item.type === 'effort')?.values || [], toggle: false, budget: null,
})
const steps = computed(() => {
  const values = (capability.value.efforts || []).map(value => ({ value, label: labels[value] || value, selection: { reasoning_effort: value } }))
  if (capability.value.toggle) {
    if (!values.some(item => item.value === 'none')) values.unshift({ value: 'disabled', label: labels.none, selection: { thinking_mode: 'disabled' } })
    if (!capability.value.efforts?.length) values.push({ value: 'enabled', label: '深度思考', selection: { thinking_mode: 'enabled' } })
  }
  if (!capability.value.efforts?.length && capability.value.budget) return []
  return values
})
const budget = computed(() => !steps.value.length && capability.value.budget)
const isDefault = computed(() => ['reasoning_effort', 'thinking_mode', 'thinking_budget'].every(key => props.modelValue[key] == null))
const selectedIndex = computed(() => steps.value.findIndex(item => item.value === (props.modelValue.reasoning_effort ?? props.modelValue.thinking_mode)))
const minimum = computed(() => budget.value ? budget.value.min - (capability.value.toggle ? 1 : 0) : 0)
const maximum = computed(() => budget.value ? budget.value.max : Math.max(0, steps.value.length - 1))
const value = computed(() => draft.value ?? (budget.value ? props.modelValue.thinking_budget ?? minimum.value : Math.max(0, selectedIndex.value)))
const label = computed(() => {
  if (draft.value != null) return budget.value ? (draft.value < budget.value.min ? labels.none : `${draft.value.toLocaleString()} tokens`) : steps.value[draft.value]?.label || '跟随模型默认'
  if (props.modelValue.thinking_budget != null) return `${props.modelValue.thinking_budget.toLocaleString()} tokens`
  return labels[props.modelValue.reasoning_effort] || props.modelValue.reasoning_effort || ({ enabled: '深度思考', disabled: labels.none })[props.modelValue.thinking_mode] || '跟随模型默认'
})
const progress = computed(() => (isDefault.value && draft.value == null) || maximum.value === minimum.value ? 0 : 100 * (value.value - minimum.value) / (maximum.value - minimum.value))
const fillWidth = computed(() => `calc(${progress.value}% + ${13 - progress.value * .26}px)`)
const adjustable = computed(() => steps.value.length > 0 || !!budget.value)
const choices = p => [{ ...p.model_metadata, id: p.model }, ...(catalogs.value[p.id] || []).filter(m => m.id !== p.model)]
let capabilityRequest = 0
const fetchCapability = async () => {
  const token = ++capabilityRequest, key = identity.value, p = profile.value, id = modelId.value
  capabilityError.value = ''; capabilityLoading.value = false
  if (!p || !id || capabilityCache.value[key]) return
  capabilityLoading.value = true
  try {
    const data = await api.request(`/profiles/${encodeURIComponent(p.id)}/model-capabilities?model_id=${encodeURIComponent(id)}`)
    if (data?.metadata) capabilityCache.value[key] = data.metadata
  } catch { if (token === capabilityRequest) capabilityError.value = '模型能力暂时未更新' }
  finally { if (token === capabilityRequest) capabilityLoading.value = false }
}
watch(identity, () => { draft.value = null; fetchCapability() }, { immediate: true })
const fetchModels = async p => {
  if (loading.value[p.id]) return
  loading.value[p.id] = true; errors.value[p.id] = ''
  try {
    const data = await api.request(`/profiles/${p.id}/models`)
    catalogs.value[p.id] = data.model_details || (data.models || []).map(id => ({ id }))
    // 列表刷新可能补齐原本未知的能力，不能继续被旧的空缓存挡住。
    if (p.id === profile.value?.id) { delete capabilityCache.value[identity.value]; await fetchCapability() }
  }
  catch { errors.value[p.id] = '列表加载失败，仍可使用已保存模型或手动输入' }
  finally { loading.value[p.id] = false }
}
const chooseStrength = selection => {
  emit('update:modelValue', { profile_id: profile.value.id, model_id: modelId.value, reasoning_effort: null, ...selection })
  draft.value = null
}
const commitStrength = event => {
  const chosen = Number(event.target.value)
  chooseStrength(budget.value ? (chosen < budget.value.min ? { thinking_mode: 'disabled' } : { thinking_budget: chosen }) : steps.value[chosen].selection)
}
const showModels = async () => { view.value = 'models'; await nextTick(); back.value?.focus() }
const showStrength = async () => { view.value = 'strength'; await nextTick(); center.value?.focus() }
const select = async (p, id) => {
  if (p.id !== profile.value?.id || id !== modelId.value) emit('update:modelValue', { profile_id: p.id, model_id: id, reasoning_effort: null })
  draft.value = null; await showStrength()
}
const submitManual = () => {
  const p = props.profiles.find(p => p.id === manualProfile.value) || profile.value || props.profiles[0]
  if (p && manual.value.trim()) select(p, manual.value.trim())
}
const opening = () => {
  if (!menu.value?.open) { view.value = profile.value ? 'strength' : 'models'; draft.value = null }
  if (props.profilesError) emit('refresh')
}
const closeOutside = event => { if (menu.value?.open && !menu.value.contains(event.target)) menu.value.open = false }
const closeOnEscape = event => {
  if (event.key !== 'Escape' || !menu.value?.open) return
  const focusInside = menu.value.contains(document.activeElement)
  menu.value.open = false
  // 同一次 Esc 只关闭此浮层，不能再折叠 AI 视图。
  event.preventDefault(); event.stopPropagation()
  if (focusInside) menu.value.querySelector('summary')?.focus()
}
const bindMenuEvents = () => {
  document.addEventListener('pointerdown', closeOutside, true)
  document.addEventListener('keydown', closeOnEscape, true)
}
const suspendMenu = () => {
  if (menu.value) menu.value.open = false
  document.removeEventListener('pointerdown', closeOutside, true)
  document.removeEventListener('keydown', closeOnEscape, true)
}
onMounted(bindMenuEvents)
onActivated(bindMenuEvents)
onDeactivated(suspendMenu)
onBeforeUnmount(() => { capabilityRequest++; suspendMenu() })
</script>

<template>
  <div class="agent-model-controls">
    <details ref="menu" class="agent-model-menu">
      <summary @click="opening" :title="profile ? `${profile.name} · ${modelName} · ${label}` : '选择模型'" aria-label="选择模型与思考强度"><span>{{ modelName }}<template v-if="profile"> · {{ label }}</template></span><ChevronDown :size="14" :stroke-width="1.8" aria-hidden="true" /></summary>
      <div class="agent-selection-popover" :aria-busy="profilesLoading">
        <template v-if="view === 'strength' && profile">
          <header class="strength-header">
            <button ref="center" type="button" class="strength-center" aria-label="切换模型" @click="showModels"><strong>{{ label }} <ChevronRight :size="14" :stroke-width="1.8" aria-hidden="true" /></strong><span>{{ modelName }}</span></button>
            <button type="button" class="strength-reset" aria-label="恢复模型默认" title="恢复模型默认" :disabled="isDefault" @click="chooseStrength({})"><RotateCcw :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
          </header>
          <div v-if="adjustable" class="strength-control">
            <div class="strength-slider" :class="{ 'is-default': isDefault && draft == null }">
              <div class="strength-track" aria-hidden="true"><div class="strength-fill" :style="{ width: fillWidth }" /></div>
              <div v-if="steps.length > 1" class="strength-stops" aria-hidden="true"><span v-for="(item, index) in steps" :key="item.value" :class="{ passed: index <= value && (!isDefault || draft != null) }" /></div>
              <input type="range" :min="minimum" :max="maximum" step="1" :value="value" :disabled="minimum === maximum" :aria-label="budget ? '思考预算' : '思考强度'" :aria-valuetext="label" @input="draft = Number($event.target.value)" @change="commitStrength" @click="isDefault && commitStrength($event)" @keydown.home.prevent="commitStrength({ target: { value: minimum } })" @keydown.end.prevent="commitStrength({ target: { value: maximum } })" />
            </div>
            <p v-if="budget" class="strength-note">思考预算 · {{ budget.min.toLocaleString() }}–{{ budget.max.toLocaleString() }} tokens</p>
            <button v-else-if="steps.length === 1" type="button" class="single-strength" @click="chooseStrength(steps[0].selection)">使用{{ steps[0].label }}档</button>
          </div>
          <p v-else class="strength-note" role="status">{{ capabilityLoading ? '正在读取模型能力…' : metadata.reasoning === false ? '此模型不支持调节思考强度' : `此模型暂未声明可调档位，${isDefault ? '使用默认设置' : '可恢复默认后使用'}` }}</p>
          <p v-if="capabilityError" class="strength-note" role="status">{{ capabilityError }}<button type="button" class="text-action" @click="fetchCapability">重试</button></p>
        </template>
        <div v-else class="model-list">
          <header class="model-list-heading"><button ref="back" type="button" aria-label="返回思考强度" :disabled="!profile" @click="showStrength"><ChevronLeft :size="14" :stroke-width="1.8" aria-hidden="true" /> 返回</button><strong>选择模型</strong></header>
          <p v-if="profilesLoading" role="status">正在加载模型配置…</p>
          <p v-if="profilesError" role="alert">{{ profilesError }}<button type="button" class="text-action" @click="emit('refresh')">重试</button></p>
          <p v-if="!profilesLoading && !profilesError && !profiles.length">请先在 AI 设置中添加服务配置。</p>
          <section v-for="p in profiles" :key="p.id">
            <header><span>{{ p.name }}</span><button type="button" class="text-action" :disabled="loading[p.id]" @click="fetchModels(p)">{{ loading[p.id] ? '加载中…' : '获取模型' }}</button></header>
            <p v-if="errors[p.id]" role="status">{{ errors[p.id] }}</p>
            <button v-for="m in choices(p)" :key="m.id" type="button" class="model-row" :aria-pressed="profile?.id === p.id && modelId === m.id" @click="select(p, m.id)"><span>{{ m.name || m.id }}</span><Check v-if="profile?.id === p.id && modelId === m.id" :size="16" :stroke-width="1.8" aria-hidden="true" /></button>
          </section>
          <button type="button" class="manual-toggle" :aria-expanded="manualOpen" @click="manualOpen = !manualOpen">手动输入模型 <ChevronDown :size="14" :stroke-width="1.8" aria-hidden="true" /></button>
          <form v-if="manualOpen" @submit.prevent="submitManual"><select v-model="manualProfile" aria-label="手动模型的服务配置"><option value="">当前服务</option><option v-for="p in profiles" :key="p.id" :value="p.id">{{ p.name }}</option></select><input v-model="manual" aria-label="手动模型 ID" placeholder="输入模型 ID" maxlength="200" /><button type="submit" class="text-action" :disabled="!manual.trim() || !profiles.length">使用</button></form>
        </div>
      </div>
    </details>
  </div>
</template>

<style scoped>
/* 浮层锚定模型入口，覆盖输入区域，不以整个输入框顶部为定位基准。 */
.agent-model-controls { position: relative; }
.agent-model-menu>summary { padding: 0 9px; border-radius: 18px; background: var(--app-surface-soft, #f5f5f5); }
html[data-theme="dark"] .agent-selection-popover { --strength-green: #3eb575; }
.agent-selection-popover { --strength-green: #079b57; position: absolute; right: 0; bottom: calc(100% + 6px); z-index: 25; width: min(240px, calc(100vw - 64px)); padding: 8px 10px; box-sizing: border-box; border: 1px solid var(--app-border, #e5e5e5); border-radius: 14px; background: var(--app-surface-bg, #fff); color: var(--app-text-primary, #222); box-shadow: 0 5px 18px #00000012; font-size: 13px; line-height: 1.5; }
.agent-selection-popover button { font: inherit; cursor: pointer; }
.agent-selection-popover button:disabled { cursor: default; opacity: .45; }
.agent-selection-popover button:focus-visible, .agent-selection-popover input:focus-visible, .agent-selection-popover select:focus-visible { outline: 2px solid var(--strength-green); outline-offset: 3px; }
.strength-header { display: grid; grid-template-columns: 24px minmax(0, 1fr) 24px; align-items: start; gap: 4px; margin-bottom: 6px; }
.strength-center { grid-column: 2; display: flex; min-width: min(80px, 100%); max-width: 100%; justify-self: center; flex-direction: column; align-items: center; gap: 0; padding: 0 6px; border: 0; border-radius: 7px; background: var(--app-surface-soft, #f5f5f5); }
.strength-center:hover { background: var(--app-surface-soft, #f5f5f5); }
.strength-center strong { display:inline-flex; align-items:center; color: var(--strength-green); font-size: 14px; font-weight: 600; }
.strength-center strong :is(i,svg) { color: var(--app-text-muted, #999); font-size: 10px; margin-left: 3px; vertical-align: 1px; }
.strength-center>span { max-width: 100%; color: var(--app-text-secondary, #777); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }
.strength-reset { grid-column: 3; height: 24px; width: 24px; padding: 0; border: 0; border-radius: 7px; background: transparent; color: var(--app-text-secondary, #888); }
.strength-reset:hover:not(:disabled) { background: var(--app-surface-soft, #f5f5f5); }
.strength-slider { position: relative; height: 28px; }
.strength-track { position: absolute; top: 3px; height: 22px; width: 100%; overflow: hidden; border-radius: 20px; background: var(--app-border, #e9e9e9); }
.strength-fill { height: 100%; background: var(--strength-green); }
.is-default .strength-fill { visibility: hidden; }
.strength-stops { position: absolute; inset: 0 13px; display: flex; justify-content: space-between; align-items: center; pointer-events: none; }
.strength-stops span { width: 4px; height: 4px; border-radius: 50%; background: #aeb3b0; }
.strength-stops span.passed { background: #ffffff80; }
.strength-slider input { position: relative; display: block; appearance: none; width: 100%; height: 28px; margin: 0; background: transparent; cursor: pointer; border: 0; padding: 0; border-radius: 20px; }
.strength-slider input::-webkit-slider-thumb { appearance: none; width: 26px; height: 26px; border-radius: 50%; background: #fff; border: 1px solid #e7e7e7; box-shadow: 0 1px 4px #00000020; }
.strength-slider input::-moz-range-thumb { width: 24px; height: 24px; border-radius: 50%; background: #fff; border: 1px solid #e7e7e7; box-shadow: 0 1px 4px #00000020; }
.strength-note { margin: 9px 0 2px; color: var(--app-text-secondary, #777); font-size: 12px; text-align: center; }
.single-strength { display: block; margin: 4px auto 0; border: 0; background: transparent; color: var(--strength-green); }
.model-list { max-height: min(380px, 55vh); overflow-y: auto; margin: -4px -6px; scrollbar-width: thin; }
.model-list-heading { display: grid; grid-template-columns: 54px 1fr 54px; align-items: center; gap: 4px; padding: 2px 2px 8px; border-bottom: 1px solid var(--app-border, #eee); }
.model-list-heading>button { border: 0; height: 28px; padding: 0; gap: 2px; white-space: nowrap; border-radius: 6px; color: var(--app-text-secondary, #777); background: transparent; }
.model-list-heading>strong { font-weight: 500; text-align: center; }
.model-list section { padding-top: 6px; }
.model-list section>header { display: flex; justify-content: space-between; align-items: center; gap: 8px; color: var(--app-text-secondary, #888); padding: 3px 8px; font-size: 11px; }
.model-list section>header>span { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.text-action { flex-shrink: 0; border: 0; background: transparent; color: var(--strength-green); padding: 2px 4px; }
.model-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; padding: 9px 10px; border: 0; border-radius: 8px; background: transparent; color: inherit; text-align: left; }
.model-row>span { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.model-row:hover { background: var(--app-surface-soft, #f5f5f5); }
.model-row[aria-pressed=true] { background: color-mix(in srgb, var(--strength-green) 8%, transparent); }
.model-row>:is(i,svg) { color: var(--strength-green); }
.model-list p { color: var(--app-text-secondary, #777); font-size: 12px; margin: 8px; }
.manual-toggle { width: 100%; display: flex; align-items: center; justify-content: space-between; padding: 10px; margin-top: 8px; border: 0; border-top: 1px solid var(--app-border, #eee); background: transparent; color: var(--app-text-secondary, #777); text-align: left; }
.manual-toggle :is(i,svg) { font-size: 9px; }
.model-list form { display: grid; grid-template-columns: 1fr auto; gap: 8px; padding: 4px 8px 8px; }
.model-list form select { grid-column: 1 / -1; }
.model-list form input, .model-list form select { width: 100%; min-width: 0; box-sizing: border-box; border: 1px solid var(--app-border, #ddd); border-radius: 6px; padding: 6px 8px; background: var(--app-surface-bg, #fff); color: inherit; font: inherit; }
</style>
