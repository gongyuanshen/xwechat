import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import LocalAccelerationSettings from '../components/LocalAccelerationSettings.vue'

const account = ref('account-a'), request = vi.fn(), stopSubscription = vi.fn()
vi.mock('~/stores/chatAccounts', () => ({ useChatAccountsStore: () => ({ selectedAccount: account }) }))
let wrapper, snapshot, onEvent
const identity = 'PCI\\VEN_1002&DEV_73FF\\REAL'
beforeEach(() => {
  account.value = 'account-a'
  snapshot = {
    hardware: { status: 'ready', generation: 1, devices: [
      { identity, name: 'AMD RX 6750 GRE', vendor: 'amd', driver: '32.0.1.2', dedicated_memory_bytes: 10 * 1024 ** 3, dxgi_index: 4, eligible: true },
      { identity: 'ROOT\\DISPLAY\\0000', name: 'GameViewer', vendor: 'amd', dxgi_index: 1, eligible: false, exclusion_reason: 'virtual_adapter' },
    ] },
    components: { dml: { installed: true, supported: true, size: 1000 }, cuda: { installed: false, supported: true, size: 2000 } },
    features: [{ id: 'search', name: '本地检索', model: { id: 'bge-small-zh', name: 'BGE Small' }, supported_backends: ['cpu', 'cuda', 'dml'], settings: { enabled: true, model: 'bge-small-zh', device: 'auto', device_id: 0, device_identity: null, usernames: ['room'], days: 90, start: null, end: null, auto_update: true, read_batch_size: 500, revision: 8, active: { model: 'old' } }, runtime: { actual_device: null }, validations: [] },
      { id: 'ocr', name: '图片与 PDF OCR', model: { id: 'ocr', name: 'RapidOCR' }, supported_backends: ['cpu', 'dml'], settings: { device: 'auto', device_identity: null }, runtime: { actual_device: null }, validations: [] },
      { id: 'voice', name: '语音转文字', model: null, supported_backends: ['cpu'], settings: { device: 'cuda' }, runtime: { actual_device: null }, validations: [] }],
    jobs: [], event_cursor: 1,
  }
  request.mockReset().mockImplementation(async path => path.includes('/status') ? structuredClone(snapshot) : {})
  stopSubscription.mockReset()
  vi.stubGlobal('useAiApi', () => ({ request, localAccelerationEvents: (_account, callback) => { onEvent = callback; return stopSubscription } }))
})
afterEach(() => { wrapper?.unmount(); vi.useRealTimers(); vi.unstubAllGlobals() })
const open = async () => { wrapper = mount(LocalAccelerationSettings); await flushPromises() }
const feature = id => wrapper.find(`[data-feature="${id}"]`)
const button = (scope, text) => scope.findAll('button').find(item => item.text() === text)

it('shows four distinct states and does not present selected voice GPU as executed', async () => {
  await open()
  expect(wrapper.text()).toContain('AMD RX 6750 GRE')
  expect(wrapper.text()).toContain('虚拟适配器，不参与加速')
  expect(feature('search').text()).toContain('尚未运行')
  expect(feature('voice').text()).toContain('尚未运行')
  expect(feature('voice').text()).toContain('当前后端未接入 AMD')
  const dml = feature('search').find('option[data-backend=dml]')
  expect(dml.attributes('disabled')).toBeDefined()
  expect(button(feature('search'), '验证 AMD DirectML').attributes('disabled')).toBeUndefined()
  expect(request.mock.calls.every(([, options]) => !options?.method)).toBe(true)
})

it('saves validated persistent identity and preserves only current account config fields', async () => {
  snapshot.features[0].validations = [{ backend: 'dml', device_identity: identity, status: 'passed' }]
  await open()
  const option = feature('search').find('option[data-backend=dml]')
  expect(option.attributes('disabled')).toBeUndefined()
  await feature('search').find('select').setValue(option.element.value); await flushPromises()
  const [path, options] = request.mock.calls.find(([path, options]) => options?.method === 'PUT')
  expect(path).toBe('/local-search/settings?account=account-a')
  expect(options.body).toMatchObject({ device: 'dml', device_identity: identity, read_batch_size: 500, usernames: ['room'] })
  expect(options.body).not.toHaveProperty('revision')
  expect(options.body).not.toHaveProperty('active')
})

it('validates current model without changing preference and supports cancellation', async () => {
  await open(); await button(feature('ocr'), '验证 AMD DirectML').trigger('click'); await flushPromises()
  expect(request).toHaveBeenCalledWith('/local-acceleration/validate', { method: 'POST', body: { feature: 'ocr', backend: 'dml', device_identity: identity } })
  expect(request.mock.calls.some(([, options]) => options?.method === 'PUT')).toBe(false)
  snapshot.jobs = [{ id: 'job-1', feature: 'ocr', model: 'ocr', status: 'validating', backend: 'dml' }]
  onEvent({ kind: 'local_acceleration_validation', account: '' }); await flushPromises()
  await button(wrapper, '取消验证').trigger('click'); await flushPromises()
  expect(request).toHaveBeenCalledWith('/local-acceleration/validate/job-1/cancel', { method: 'POST' })
})

it('shows previous GPU CPU partition and explicit CPU fault recovery', async () => {
  snapshot.features[0].runtime = { actual_device: 'dml', device_name: 'AMD RX', active: false, evidence: { cpu_nodes: 3 } }
  snapshot.features[1].runtime = { actual_device: 'cpu', using_fallback: true, reason: '设备已移除' }
  await open()
  expect(feature('search').text()).toContain('上次运行')
  expect(feature('search').text()).toContain('GPU + 部分 CPU')
  expect(feature('ocr').text()).toContain('已改用 CPU')
  expect(feature('ocr').text()).toContain('设备已移除')
})

it('explains automatic CPU selection even when it is not fault recovery', async () => {
  snapshot.features[0].runtime = { actual_device: 'cpu', using_fallback: false, reason: '未安装 AMD DirectML 组件' }
  await open()
  expect(feature('search').text()).toContain('CPU · 未安装 AMD DirectML 组件')
  expect(feature('search').text()).not.toContain('已改用 CPU')
})

it('ignores responses and events belonging to the old account', async () => {
  let completeOld
  request.mockImplementationOnce(() => new Promise(resolve => { completeOld = resolve }))
  wrapper = mount(LocalAccelerationSettings); await flushPromises()
  account.value = 'account-b'; await flushPromises()
  const old = structuredClone(snapshot); old.features[0].model.name = '旧账号模型'
  completeOld(old); await flushPromises()
  expect(wrapper.text()).not.toContain('旧账号模型')
  const before = request.mock.calls.length
  onEvent({ account: 'account-a' }); await flushPromises()
  expect(request.mock.calls.length).toBe(before)
})

it('component download pause and import use one shared backend with visible failures', async () => {
  snapshot.components.dml = { installed: false, supported: true, size: 1000, job: { status: 'running', bytes: 500, total: 1000 } }
  await open()
  const dml = wrapper.find('[data-component=dml]')
  expect(dml.find('progress').element.value).toBe(500)
  await button(dml, '暂停').trigger('click'); await flushPromises()
  expect(request).toHaveBeenCalledWith('/local-acceleration/components/dml/pause?account=account-a', { method: 'POST' })
  snapshot.components.dml.job.status = 'paused'; onEvent({ account: '' }); await flushPromises()
  await button(dml, '离线导入').trigger('click')
  await dml.find('input').setValue('G:\\DML')
  request.mockRejectedValueOnce(new Error('组件校验失败'))
  await dml.find('form').trigger('submit'); await flushPromises()
  expect(wrapper.find('[role=alert]').text()).toContain('组件校验失败')
})

it('component loss disables cached GPU success and explains why validation is unavailable', async () => {
  snapshot.features[0].validations = [{ backend: 'dml', device_identity: identity, status: 'passed' }]
  snapshot.components.dml.installed = false
  snapshot.components.dml.error = '组件校验失败：缺少 DLL'
  await open()
  expect(feature('search').find('option[data-backend=dml]').attributes('disabled')).toBeDefined()
  expect(button(feature('search'), '验证 AMD DirectML').attributes('disabled')).toBeDefined()
  expect(feature('search').text()).toContain('请先安装 AMD DirectML 组件')
  expect(wrapper.text()).toContain('组件校验失败：缺少 DLL')
})

it('hardware detection failures never appear as an empty successful inventory', async () => {
  snapshot.hardware = { status: 'error', devices: [], generation: 2, error: '驱动故障 43' }
  await open()
  expect(wrapper.text()).toContain('检测失败：驱动故障 43')
  expect(wrapper.text()).not.toContain('未发现显卡')
})

it('isolates CUDA discovery failures while keeping AMD validation and selection available', async () => {
  const nvidia = { identity: 'PCI\\NVIDIA', name: 'NVIDIA GPU', vendor: 'nvidia', eligible: true, cuda_index: null, cuda_error: 'CUDA cuInit failed (0x00000003)' }
  snapshot.hardware.devices.push(nvidia)
  snapshot.components.cuda.installed = true
  snapshot.features[0].validations = [
    { backend: 'cuda', device_identity: nvidia.identity, status: 'passed' },
    { backend: 'dml', device_identity: identity, status: 'passed' },
  ]
  await open()
  expect(wrapper.find('[aria-label="本机显卡"]').text()).toContain(nvidia.cuda_error)
  expect(feature('search').text()).toContain(nvidia.cuda_error)
  expect(feature('search').find('option[data-backend=cuda]').attributes('disabled')).toBeDefined()
  expect(button(feature('search'), '验证 NVIDIA CUDA').attributes('disabled')).toBeDefined()
  expect(feature('search').find('option[data-backend=dml]').attributes('disabled')).toBeUndefined()
  expect(button(feature('search'), '验证 AMD DirectML').attributes('disabled')).toBeUndefined()
  expect(wrapper.text()).not.toContain('检测失败')
})

it('coalesces event bursts and refreshes once more after an in-flight response', async () => {
  await open()
  request.mockClear()
  onEvent({ account: '' }); onEvent({ account: '' }); onEvent({ account: '' })
  await flushPromises()
  expect(request).toHaveBeenCalledTimes(1)
  let complete
  const previous = structuredClone(snapshot)
  request.mockClear().mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  onEvent({ account: '' }); await flushPromises()
  onEvent({ account: '' }); onEvent({ account: '' })
  snapshot.features[0].runtime = { actual_device: 'dml', active: false, device_name: 'AMD RX' }
  expect(request).toHaveBeenCalledTimes(1)
  complete(previous); await flushPromises()
  expect(request).toHaveBeenCalledTimes(2)
  expect(feature('search').text()).toContain('上次运行：AMD RX')
})

it('refreshes voice and Laya only while the acceleration section is active', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  snapshot.features.push({ id: 'laya', name: 'Laya 本地分析', model: { id: 'laya', name: 'Laya' }, supported_backends: ['cpu'], settings: { device: 'cpu' }, runtime: { actual_device: null }, validations: [] })
  wrapper = mount(LocalAccelerationSettings, { props: { active: false } }); await flushPromises()
  expect(request).not.toHaveBeenCalled()
  await wrapper.setProps({ active: true }); await flushPromises()
  const previousEvent = onEvent
  expect(request).toHaveBeenCalledTimes(1)
  snapshot.features[2].runtime = { actual_device: 'cpu', active: true }
  snapshot.features[3].runtime = { actual_device: 'cpu', active: false }
  await vi.advanceTimersByTimeAsync(5000); await flushPromises()
  expect(request).toHaveBeenCalledTimes(2)
  expect(feature('voice').text()).toContain('实际运行：CPU')
  expect(feature('laya').text()).toContain('上次运行：CPU')
  await wrapper.setProps({ active: false })
  expect(stopSubscription).toHaveBeenCalledTimes(1)
  previousEvent({ account: '' })
  await vi.advanceTimersByTimeAsync(15000); await flushPromises()
  expect(request).toHaveBeenCalledTimes(2)
  await wrapper.setProps({ active: true }); await flushPromises()
  expect(request).toHaveBeenCalledTimes(3)
  wrapper.unmount(); wrapper = null
  await vi.advanceTimersByTimeAsync(15000); await flushPromises()
  expect(request).toHaveBeenCalledTimes(3)
  expect(stopSubscription).toHaveBeenCalledTimes(2)
})

it('keeps loaded content and import input when scrolling changes the active section', async () => {
  await open()
  const dml = wrapper.find('[data-component=dml]')
  await button(dml, '离线导入').trigger('click')
  await dml.find('input').setValue('G:\\DML')
  await wrapper.setProps({ active: false })
  expect(wrapper.text()).toContain('AMD RX 6750 GRE')
  expect(feature('search').text()).toContain('BGE Small')
  expect(wrapper.text()).not.toContain('正在读取本地加速状态')
  expect(dml.find('input').element.value).toBe('G:\\DML')
  expect(request).toHaveBeenCalledTimes(1)

  let complete
  request.mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  await wrapper.setProps({ active: true }); await flushPromises()
  expect(feature('search').text()).toContain('BGE Small')
  expect(wrapper.text()).not.toContain('正在读取本地加速状态')
  snapshot.features[0].runtime = { actual_device: 'cpu', active: true }
  complete(structuredClone(snapshot)); await flushPromises()
  expect(feature('search').text()).toContain('实际运行：CPU')
  expect(dml.find('input').element.value).toBe('G:\\DML')
})

it('keeps an in-flight action pending across scrolling and refreshes its result while inactive', async () => {
  await open()
  let complete
  request.mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  await button(wrapper, '重新检测显卡').trigger('click'); await flushPromises()
  await wrapper.setProps({ active: false })
  expect(button(wrapper, '重新检测显卡').attributes('disabled')).toBeDefined()
  snapshot.hardware.status = 'error'; snapshot.hardware.error = '驱动故障 43'
  complete({}); await flushPromises()
  expect(wrapper.text()).toContain('检测失败：驱动故障 43')
  expect(button(wrapper, '重新检测显卡').attributes('disabled')).toBeUndefined()
  expect(request).toHaveBeenCalledTimes(3)
})

it('refreshes an explicit action when the loaded section remains visible but inactive', async () => {
  await open()
  await wrapper.setProps({ active: false })
  snapshot.hardware.status = 'error'; snapshot.hardware.error = '驱动故障 43'
  await button(wrapper, '重新检测显卡').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('检测失败：驱动故障 43')
  expect(request).toHaveBeenCalledTimes(3)
})

it('clears the old account snapshot while inactive and ignores its in-flight response', async () => {
  await open()
  const previous = structuredClone(snapshot)
  previous.features[0].model.name = '旧账号模型'
  let complete
  request.mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  onEvent({ account: '' }); await flushPromises()
  await wrapper.setProps({ active: false })
  account.value = 'account-b'; await flushPromises()
  expect(feature('search').exists()).toBe(false)
  expect(request).toHaveBeenCalledTimes(2)
  complete(previous); await flushPromises()
  expect(wrapper.text()).not.toContain('旧账号模型')
  snapshot.features[0].model.name = '新账号模型'
  await wrapper.setProps({ active: true }); await flushPromises()
  expect(feature('search').text()).toContain('新账号模型')
})

it('does not publish an old action success after switching accounts during its refresh', async () => {
  await open()
  let complete
  request.mockImplementationOnce(async () => ({}))
    .mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  await feature('search').find('select').setValue(JSON.stringify({ device: 'cpu', device_identity: null }))
  await flushPromises()
  account.value = 'account-b'; snapshot.features[0].model.name = '新账号模型'
  await flushPromises()
  complete(structuredClone(snapshot)); await flushPromises()
  expect(feature('search').text()).toContain('新账号模型')
  expect(wrapper.text()).not.toContain('运行偏好已保存')
})

it('discards an old account refresh and its queued follow-up after switching accounts', async () => {
  await open()
  let complete
  request.mockClear().mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  const oldEvent = onEvent, previous = structuredClone(snapshot)
  oldEvent({ account: '' }); await flushPromises()
  oldEvent({ account: '' })
  account.value = 'account-b'; snapshot.features[0].model.name = '新账号模型'
  await flushPromises()
  expect(request).toHaveBeenCalledTimes(2)
  complete(previous); oldEvent({ account: '' }); await flushPromises()
  expect(request).toHaveBeenCalledTimes(2)
  expect(feature('search').text()).toContain('新账号模型')
})

it('does not block the selected model with a different model validation job', async () => {
  snapshot.jobs = [{ id: 'other-model', feature: 'search', model: 'bge-base-zh', status: 'validating', backend: 'dml' }]
  await open()
  expect(button(feature('search'), '验证 AMD DirectML').attributes('disabled')).toBeUndefined()
  expect(feature('search').text()).not.toContain('正在验证 AMD DirectML')
  snapshot.jobs[0].model = 'bge-small-zh'
  onEvent({ account: '' }); await flushPromises()
  expect(button(feature('search'), '验证 AMD DirectML').attributes('disabled')).toBeDefined()
  expect(feature('search').text()).toContain('正在验证 AMD DirectML')
})

it('labels fallback evidence as previous execution after the worker closes', async () => {
  snapshot.features[0].runtime = { actual_device: 'cpu', active: false, using_fallback: true, reason: '设备已移除' }
  await open()
  expect(feature('search').text()).toContain('上次运行：已改用 CPU · 设备已移除')
})
