// 协调跨页面消息来源和已有任务通知；账号加载、聊天定位复用聊天页的现有流程。
export const createAiNavigationConsumer = ({ navigation, account, isActive, ensureLoaded, selectAccount, waitForAccount, showSourceChat, openTask, locateSource, diagnostic, showError }) => {
  let pendingTarget = null
  return async () => {
    const target = navigation.value
    if (!isActive() || !target || target.matter_id || pendingTarget === target) return
    pendingTarget = target
    const isWrapped = target.origin === 'wrapped'
    const component = target.kind === 'source' ? 'source' : 'notification'
    const currentTarget = () => isActive() && navigation.value === target
    const currentAccount = () => currentTarget() && account.value === target.account
    if (!isWrapped) diagnostic('navigation.started', { task_id: target.task_id, component })
    try {
      await ensureLoaded()
      if (!currentTarget()) return
      if (target.account !== account.value) selectAccount(target.account)
      await waitForAccount()
      if (!currentAccount()) return
      if (target.kind === 'source') await showSourceChat()
      else openTask(target.task_id || '')
      if (!currentAccount()) return
      if (target.username && target.anchor) {
        const found = await locateSource(target)
        if (!currentAccount()) return
        if (found === false) throw new Error('未定位到来源消息')
      }
      if (!currentAccount()) return
      navigation.value = null
      if (!isWrapped) diagnostic('navigation.finished', { task_id: target.task_id, component })
    } catch (error) {
      if (!currentAccount()) return
      if (!isWrapped) diagnostic('navigation.failed', { task_id: target.task_id, component })
      const retryMessage = isWrapped ? '请返回年度总结后重新点击来源定位。' : target.origin === 'search' ? '请返回搜索后重新点击来源定位。' : '请返回 AI 助手后重新点击来源定位。'
      showError(`${error?.message || (isWrapped ? '年度总结来源定位失败' : 'AI 来源定位失败')}。${retryMessage}`)
    } finally {
      if (pendingTarget === target) pendingTarget = null
    }
  }
}
