export const useLocalAccelerationApi = () => {
  const api = useAiApi()
  return {
    status: account => api.request(`/local-acceleration/status${account ? `?account=${encodeURIComponent(account)}` : ''}`),
    recheck: () => api.request('/local-acceleration/recheck', { method: 'POST' }),
    validate: body => api.request('/local-acceleration/validate', { method: 'POST', body }),
    cancel: id => api.request(`/local-acceleration/validate/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
    component: (backend, action, body, account) => api.request(`/local-acceleration/components/${backend}/${action}${account ? `?account=${encodeURIComponent(account)}` : ''}`, { method: 'POST', ...(body ? { body } : {}) }),
    save: (feature, account, body) => api.request(feature === 'ocr' ? '/local-acceleration/ocr/settings' : `/local-search/settings?account=${encodeURIComponent(account)}`, { method: 'PUT', body }),
    events: (account, callback, after, onDisconnected) => api.localAccelerationEvents(account, callback, after, onDisconnected),
  }
}

export const accelerationRuntimeText = runtime => {
  if (!runtime?.actual_device) return '尚未运行'
  const previous = runtime.active === false ? '上次运行：' : ''
  if (runtime.using_fallback) return `${previous}已改用 CPU${runtime.reason ? ` · ${runtime.reason}` : ''}`
  const device = runtime.device_name || ({ cpu: 'CPU', cuda: 'NVIDIA GPU', dml: 'AMD DirectML' })[runtime.actual_device] || runtime.actual_device
  const partition = runtime.actual_device !== 'cpu' && runtime.evidence?.cpu_nodes > 0 ? ' · GPU + 部分 CPU' : ''
  return `${previous}${device}${partition}${runtime.reason ? ` · ${runtime.reason}` : ''}`
}
