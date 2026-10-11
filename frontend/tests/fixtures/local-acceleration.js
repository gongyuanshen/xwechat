import { createApp, ref, computed } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import LocalAccelerationSettings from '../../components/LocalAccelerationSettings.vue'
import { useChatAccountsStore } from '../../stores/chatAccounts'
import '../../assets/css/tailwind.css'
// 仅供视觉与交互验收的固定样例；不连接账号、下载文件或执行模型。
Object.assign(globalThis, { ref, computed, process: { client: false }, useApiBase: () => '/unused' })
const identity = 'PCI\\VEN_1002&DEV_73FF\\SYNTHETIC'
const snapshot = {
  hardware: { status: 'ready', generation: 1, devices: [
    { identity, name: 'AMD Radeon RX 6750 GRE 10GB', vendor: 'amd', driver: '32.0.21045.9003', dedicated_memory_bytes: 10689413120, dxgi_index: 0, eligible: true },
    { identity: 'ROOT\\DISPLAY\\SAMPLE', name: 'GameViewer Virtual Display Adapter', vendor: 'amd', eligible: false, exclusion_reason: 'virtual_adapter' },
  ] }, components: { dml: { supported: true, installed: true, size: 57300000 }, cuda: { supported: true, installed: false, size: 1671874915 } },
  features: [
    { id: 'search', name: '本地检索', model: { id: 'bge-small-zh', name: 'BGE Small 中文' }, supported_backends: ['cpu', 'cuda', 'dml'], settings: { device: 'auto', model: 'bge-small-zh', enabled: true }, runtime: { actual_device: 'dml', device_name: 'AMD RX 6750 GRE', active: false, evidence: { cpu_nodes: 4 } }, validations: [{ backend: 'dml', device_identity: identity, status: 'passed' }] },
    { id: 'ocr', name: '图片与 PDF OCR', model: { id: 'ocr', name: 'RapidOCR' }, supported_backends: ['cpu', 'dml'], settings: { device: 'cpu' }, runtime: { actual_device: null }, validations: [] },
    { id: 'voice', name: '语音转文字', model: { id: 'zipformer', name: 'Zipformer' }, supported_backends: ['cpu'], settings: { device: 'cpu' }, runtime: { actual_device: null }, validations: [] },
    { id: 'laya', name: 'Laya 本地分析', model: null, supported_backends: ['cpu'], settings: { device: 'cpu' }, runtime: { actual_device: null }, validations: [] },
  ], jobs: [], event_cursor: 0,
}
globalThis.useAiApi = () => ({ request: async (path, options) => {
  if (path.includes('/status')) return structuredClone(snapshot)
  if (options?.method === 'PUT') {
    const feature = snapshot.features.find(item => item.id === (path.includes('/ocr/') ? 'ocr' : 'search'))
    feature.settings = { ...feature.settings, ...options.body }
    return feature.settings
  }
  throw new Error('隔离验收页面不执行此项系统操作')
}, localAccelerationEvents: () => () => {} })
const pinia = createPinia(); setActivePinia(pinia); useChatAccountsStore().selectedAccount = 'synthetic-account'
if (new URLSearchParams(location.search).has('dark')) document.documentElement.dataset.theme = 'dark'
createApp(LocalAccelerationSettings).use(pinia).mount('#app')
