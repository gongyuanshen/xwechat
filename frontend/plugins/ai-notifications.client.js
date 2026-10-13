export default defineNuxtPlugin(() => {
  const target = useState('ai-navigation-target', () => null)
  const desktop = window.wechatDesktop
  const diagnostics = useAiApi()
  const open = async (value) => {
    if (!value?.account || (!value?.task_id && !value?.matter_id)) return
    target.value = value
    try { await navigateTo(value.matter_id ? '/matters' : '/chat') }
    catch { diagnostics.diagnostic('navigation.failed', { task_id: value.task_id, component: 'notification' }) }
  }
  desktop?.onAiNavigate?.(value => { desktop.takeAiNavigation?.()?.catch?.(() => diagnostics.diagnostic('navigation.failed', { component: 'notification' })); void open(value) })
  desktop?.takeAiNavigation?.()?.then(value => { if (value) void open(value) }).catch(() => diagnostics.diagnostic('navigation.failed', { component: 'notification' }))
})
