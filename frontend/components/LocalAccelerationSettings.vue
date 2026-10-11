<template>
  <section class="acceleration-settings" aria-label="本地加速">
    <header class="las-heading">
      <div><h3>本地加速</h3><p>为本地模型选择运行设备。安装与验证完成后，从下一个工作单元生效。</p></div>
      <button type="button" :disabled="!!pending || state?.hardware.status === 'detecting'" @click="act('recheck', api.recheck)">{{ state?.hardware.status === 'detecting' ? '正在检测…' : '重新检测显卡' }}</button>
    </header>
    <p v-if="error" class="las-error" role="alert">{{ error }} <button type="button" :disabled="!!pending" @click="refresh">刷新状态</button></p>
    <p v-if="connectionError" class="las-note" role="status">{{ connectionError }} <button type="button" @click="refresh">刷新状态</button></p>
    <p v-if="notice" class="las-notice" role="status">{{ notice }}</p>
    <p v-if="!state && !error" role="status">正在读取本地加速状态…</p>
    <template v-if="state">
      <section class="las-section" aria-label="本机显卡">
        <h4>本机显卡</h4>
        <p v-if="['error', 'unsupported'].includes(state.hardware.status)" role="alert" class="las-error">{{ state.hardware.status === 'unsupported' ? '当前系统不支持' : '检测失败' }}：{{ state.hardware.error }}</p>
        <p v-else-if="state.hardware.status === 'detecting'" role="status">正在检测显卡…</p>
        <p v-else-if="state.hardware.status === 'unverified'">尚未检测显卡。</p>
        <p v-else-if="state.hardware.status === 'ready' && !state.hardware.devices.length">未发现显卡，可使用 CPU。</p>
        <ul class="las-devices">
          <li v-for="device in state.hardware.devices" :key="device.identity || device.luid">
            <div><strong>{{ device.name }}</strong><span v-if="device.eligible" class="las-note"> · {{ bytes(device.dedicated_memory_bytes) }} 专用显存</span></div>
            <p>{{ device.eligible ? `已发现 · 驱动 ${device.driver || '未知'}` : exclusionLabel(device.exclusion_reason) }}</p>
            <p v-if="device.cuda_error" class="las-error" role="alert">CUDA 不可用：{{ device.cuda_error }}</p>
          </li>
        </ul>
      </section>
      <section class="las-section" aria-label="功能与运行设备">
        <h4>功能与运行设备</h4>
        <p class="las-note">自动模式优先使用已验证的 CUDA，再选择 AMD DirectML。显式选择 GPU 时，故障会停止该项任务。</p>
        <article v-for="feature in state.features" :key="feature.id" :data-feature="feature.id" class="las-feature">
          <div class="las-row"><div><h5>{{ feature.name }}</h5><p>{{ feature.model?.name || '尚未选择模型' }}</p></div>
            <label v-if="['search', 'ocr'].includes(feature.id)" class="las-choice">运行偏好
              <select :aria-label="`${feature.name}运行偏好`" :value="selection(feature.settings)" :disabled="!!pending || (feature.id === 'search' && !account)" @change="save(feature, $event.target.value)">
                <option :value="selection({device:'auto'})">自动</option><option :value="selection({device:'cpu'})">CPU</option>
                <option v-for="candidate in candidates(feature)" :key="candidate.value" :value="candidate.value" :data-backend="candidate.backend" :disabled="!passed(feature, candidate)">{{ backendLabel(candidate.backend) }} · {{ candidate.device.name }}{{ passed(feature, candidate) ? '' : '（需验证）' }}</option>
                <option v-if="missingSelection(feature)" :value="selection(feature.settings)" disabled>已选 GPU 当前不可用，请检查设备与验证状态</option>
              </select>
            </label>
          </div>
          <p class="las-runtime">{{ feature.runtime?.active === false && feature.runtime?.actual_device ? '' : '实际运行：' }}{{ accelerationRuntimeText(feature.runtime) }}</p>
          <p v-if="feature.id === 'search' && !account" class="las-note">请先选择微信账号，再设置检索设备。</p>
          <p v-if="['voice', 'laya'].includes(feature.id)" class="las-note">{{ feature.id === 'voice' ? '当前后端未接入 AMD；模型与设备偏好在“语音转文字”中设置。' : '当前本地分析使用 CPU。' }}</p>
          <div v-for="candidate in candidates(feature)" :key="candidate.value" class="las-validation">
            <div><span>{{ backendLabel(candidate.backend) }} · {{ candidate.device.name }}</span><p>{{ validationBlocked(feature, candidate) || validationLabel(feature, candidate) }}</p></div>
            <button type="button" :disabled="!!pending || !!validationBlocked(feature, candidate) || validating(feature)" @click="validate(feature, candidate)">验证 {{ backendLabel(candidate.backend) }}</button>
          </div>
          <div v-for="job in featureJobs(feature)" :key="job.id" class="las-row las-job"><span>正在验证 {{ backendLabel(job.backend) }}</span><button type="button" :disabled="!!pending" @click="act(`cancel:${job.id}`, () => api.cancel(job.id))">取消验证</button></div>
        </article>
      </section>
      <section class="las-section" aria-label="加速组件">
        <h4>加速组件</h4><p class="las-note">检索与 OCR 共用组件。组件安装完成后仍需验证当前模型。</p>
        <article v-for="(component, backend) in state.components" :key="backend" :data-component="backend" class="las-component">
          <div class="las-row"><div><h5>{{ backendLabel(backend) }}</h5><p>{{ component.installed ? '组件已安装' : `组件未安装 · ${bytes(component.size)}` }}<template v-if="component.reason"> · {{ component.reason }}</template></p></div>
            <div class="las-actions"><button v-if="!component.installed && !componentBusy(component)" type="button" :disabled="!!pending || !component.supported" @click="componentAction(backend, 'download')">{{ component.job?.status === 'paused' ? '继续下载' : '下载组件' }}</button>
              <button v-if="componentBusy(component)" type="button" :disabled="!!pending" @click="componentAction(backend, 'pause')">暂停</button>
              <button type="button" :disabled="!!pending || componentBusy(component) || !component.supported" @click="importBackend = importBackend === backend ? '' : backend">离线导入</button></div>
          </div>
          <p v-if="component.error" class="las-error" role="alert">{{ component.error }}</p>
          <div v-if="component.job && component.job.status !== 'done'" class="las-download"><p>{{ jobLabel(component.job) }} · {{ bytes(component.job.bytes) }} / {{ bytes(component.job.total || component.size) }}</p><progress v-if="component.job.total" :value="component.job.bytes" :max="component.job.total" :aria-label="`${backendLabel(backend)}组件下载进度`" /><p v-if="component.job.error" class="las-error" role="alert">{{ component.job.error }}</p></div>
          <form v-if="importBackend === backend" class="las-import" @submit.prevent="componentAction(backend, 'import', {path: importPath})"><label>本机组件文件目录<input v-model.trim="importPath" required placeholder="填写 wheel 文件所在目录的完整路径" /></label><button type="submit" :disabled="!!pending || !importPath || componentBusy(component)">校验并导入</button></form>
        </article>
      </section>
    </template>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useChatAccountsStore } from '~/stores/chatAccounts'
import { useLocalAccelerationApi, accelerationRuntimeText } from '~/composables/useLocalAccelerationApi'
const props = defineProps({ active: { type: Boolean, default: true } })
const accounts = useChatAccountsStore(), api = useLocalAccelerationApi()
const account = computed(() => accounts.selectedAccount?.value ?? accounts.selectedAccount ?? '')
const state = ref(null), error = ref(''), notice = ref(''), pending = ref(''), connectionError = ref('')
const importBackend = ref(''), importPath = ref('')
let scope = 0, stopEvents, pollTimer, refreshTask, refreshQueued = false
const backendLabel = backend => ({ dml: 'AMD DirectML', cuda: 'NVIDIA CUDA', cpu: 'CPU' })[backend] || backend
const bytes = value => value == null ? '大小未知' : value < 1024 ** 2 ? `${Math.ceil(value / 1024)} KB` : value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(1)} MB` : `${(value / 1024 ** 3).toFixed(1)} GB`
const exclusionLabel = reason => ({ virtual_adapter: '虚拟适配器，不参与加速', software_adapter: '软件渲染器，不参与加速', render_unsupported: '不支持模型渲染计算' })[reason] || reason
const selection = settings => JSON.stringify({ device: settings.device, device_identity: ['cuda', 'dml'].includes(settings.device) ? settings.device_identity : null })
const candidates = feature => ['search', 'ocr'].includes(feature.id) ? feature.supported_backends.filter(backend => ['cuda', 'dml'].includes(backend)).flatMap(backend => state.value.hardware.devices.filter(device => device.eligible && device.vendor === (backend === 'cuda' ? 'nvidia' : 'amd')).map(device => ({ backend, device, value: selection({ device: backend, device_identity: device.identity }) }))) : []
const validation = (feature, candidate) => feature.validations.find(item => item.backend === candidate.backend && item.device_identity === candidate.device.identity)
const passed = (feature, candidate) => !validationBlocked(feature, candidate) && validation(feature, candidate)?.status === 'passed'
const validationBlocked = (feature, candidate) => state.value.hardware.status !== 'ready' ? '请先重新检测显卡。'
  : candidate.backend === 'cuda' && candidate.device.cuda_index == null ? candidate.device.cuda_error || '无法确定 NVIDIA CUDA 设备编号，请重新检测。'
    : !feature.model ? '请先选择并安装当前模型。'
    : feature.id === 'search' && !account.value ? '请先选择微信账号。'
      : !state.value.components[candidate.backend]?.installed ? `请先安装 ${backendLabel(candidate.backend)} 组件。` : ''
const validationLabel = (feature, candidate) => {
  const record = validation(feature, candidate)
  return `${({ unverified: '当前模型尚未验证', validating: '当前模型正在验证', passed: '当前模型已通过验证', failed: '当前模型验证失败', cancelled: '验证已取消' })[record?.status || 'unverified']}${record?.reason ? ` · ${record.reason}` : ''}`
}
const missingSelection = feature => ['cuda', 'dml'].includes(feature.settings.device) && !candidates(feature).some(item => item.value === selection(feature.settings))
const featureJobs = feature => state.value.jobs.filter(job => job.feature === feature.id && job.model === feature.model?.id && ['queued', 'running', 'validating'].includes(job.status))
const validating = feature => featureJobs(feature).length > 0
const componentBusy = component => ['queued', 'running', 'pausing'].includes(component.job?.status)
const jobLabel = job => ({ queued: '等待下载', running: '正在处理组件', paused: '已暂停', error: '组件处理失败', cancelled: '已取消' })[job.status] || job.stage || job.status
function refresh() {
  refreshQueued = true
  if (refreshTask) return refreshTask
  const current = account.value, stamp = scope
  const task = Promise.resolve().then(async () => {
    // Merge an event burst; events received during a request require one fresh snapshot.
    while (refreshQueued && stamp === scope) {
      refreshQueued = false
      try {
        const result = await api.status(current)
        if (stamp === scope) { state.value = result; error.value = '' }
      } catch (cause) { if (stamp === scope) error.value = cause.message }
    }
  }).finally(() => { if (refreshTask === task) refreshTask = null })
  refreshTask = task
  return task
}
async function act(key, action, message = '') {
  if (pending.value) return
  const stamp = scope
  pending.value = key; error.value = ''; notice.value = ''
  try { await action(); if (stamp === scope) { await refresh(); if (stamp === scope) notice.value = message } }
  catch (cause) { if (stamp === scope) error.value = cause.message }
  finally { if (stamp === scope) pending.value = '' }
}
function validate(feature, candidate) {
  return act(`validate:${feature.id}`, () => api.validate({ feature: feature.id, backend: candidate.backend, device_identity: candidate.device.identity, ...(feature.id === 'search' ? { account: account.value } : {}) }))
}
function save(feature, value) {
  const choice = JSON.parse(value)
  const fields = ['enabled', 'model', 'usernames', 'days', 'start', 'end', 'device', 'device_id', 'device_identity', 'auto_update', 'read_batch_size', 'agent_global']
  const config = feature.id === 'search' ? Object.fromEntries(fields.filter(key => key in feature.settings).map(key => [key, feature.settings[key]])) : {}
  if (config.agent_global) config.usernames = []
  return act(`save:${feature.id}`, () => api.save(feature.id, account.value, { ...config, ...choice }), '运行偏好已保存，从下一个工作单元生效。')
}
function componentAction(backend, action, body) {
  const stamp = scope
  return act(`${backend}:${action}`, async () => { await api.component(backend, action, body, account.value); if (action === 'import' && stamp === scope) { importBackend.value = ''; importPath.value = '' } })
}
function stopWatching() {
  stopEvents?.(); stopEvents = undefined; clearInterval(pollTimer)
}
watch([account, () => props.active], async ([current, active], [previousAccount], onCleanup) => {
  let stopped = false
  onCleanup(() => { stopped = true; stopWatching() })
  // Scrolling changes the highlighted section; only an account change clears its data.
  if (current !== previousAccount) {
    scope++; refreshTask = null; refreshQueued = false
    state.value = null; pending.value = ''; error.value = ''; notice.value = ''; connectionError.value = ''; importBackend.value = ''; importPath.value = ''
  }
  if (!active) return
  const stamp = scope
  await refresh()
  if (stopped || stamp !== scope) return
  stopEvents = api.events(current, event => { if (!stopped && stamp === scope && (!event.account || event.account === current)) { connectionError.value = ''; refresh() } }, state.value?.event_cursor || 0,
    () => { if (!stopped && stamp === scope) connectionError.value = '状态连接已中断，可点击“刷新状态”重新读取。' })
  // Voice and Laya expose snapshots without acceleration events.
  pollTimer = setInterval(refresh, 5000)
}, { immediate: true })
onBeforeUnmount(() => { scope++; stopWatching() })
</script>

<style scoped>
.acceleration-settings{color:var(--app-text-primary);font-size:12px;line-height:1.6;container-type:inline-size}.acceleration-settings :is(h3,h4,h5,p){margin:0}.acceleration-settings h3{font-size:16px;font-weight:650}.acceleration-settings h4{font-size:14px;margin-bottom:10px}.acceleration-settings h5{font-size:13px}.acceleration-settings p{color:var(--app-text-secondary);margin-top:4px;overflow-wrap:anywhere}.las-heading,.las-row{display:flex;align-items:center;justify-content:space-between;gap:16px}.las-heading{align-items:flex-start;margin-bottom:24px}.las-heading>div{max-width:60ch}.las-section+.las-section{margin-top:28px}.las-devices{margin:0;padding:0;list-style:none}.las-devices li,.las-feature,.las-component{padding:14px 0;border-bottom:1px solid var(--app-border)}.las-devices li:first-child{padding-top:0}.las-note{font-size:11px;color:var(--app-text-secondary)}.las-actions{display:flex;gap:8px;flex-wrap:wrap}.acceleration-settings :is(button,select,input){font:inherit;color:inherit;border:1px solid var(--app-border);border-radius:6px;background:var(--app-surface-bg);padding:7px 10px;min-height:34px}.acceleration-settings button{cursor:pointer;white-space:nowrap;flex:none}.acceleration-settings button:hover{background:var(--app-surface-soft)}.acceleration-settings :disabled{opacity:.5;cursor:not-allowed}.acceleration-settings :is(button,select,input):focus-visible{outline:2px solid var(--app-accent);outline-offset:3px}.las-choice{display:flex;flex-direction:column;gap:4px;min-width:210px;max-width:60%;font-size:11px}.las-choice select{max-width:100%;font-size:12px}.las-validation{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-top:12px;padding-top:12px;border-top:1px solid var(--app-border-soft)}.las-validation p{font-size:11px}.acceleration-settings .las-runtime{margin-top:10px;color:var(--app-text-primary)}.acceleration-settings .las-error{color:var(--danger-color);margin:10px 0}.acceleration-settings .las-notice{color:var(--app-accent);margin:10px 0}.las-job{margin-top:10px}.las-download{margin-top:10px;font-variant-numeric:tabular-nums}.las-download progress{width:100%;height:6px;accent-color:var(--app-accent)}.las-import{display:flex;align-items:flex-end;gap:10px;margin-top:12px}.las-import label{display:flex;flex-direction:column;gap:5px;flex:1;min-width:0}.las-import input{min-width:0;width:100%}.acceleration-settings ::selection{background:var(--app-accent-soft)}.acceleration-settings input{caret-color:var(--app-accent)}@container(max-width:520px){.las-heading,.las-row,.las-validation{align-items:flex-start;flex-wrap:wrap}.las-choice{width:100%;max-width:none}.las-import{flex-wrap:wrap}.las-import label{flex-basis:100%}}
</style>
