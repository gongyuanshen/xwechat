const controllers = new WeakMap()
const fields = value => ({ profile_id: value.profile_id, model_id: value.model_id, reasoning_effort: value.reasoning_effort ?? null,
  ...(value.thinking_mode != null ? { thinking_mode: value.thinking_mode } : {}),
  ...(value.thinking_budget != null ? { thinking_budget: value.thinking_budget } : {}),
})

// 与聊天视图共用应用状态；保存队列不随面板卸载而中断。
export function agentModelSelection(shared, request) {
  shared.modelSelection ||= { choice: {}, version: 0, pending: 0, dirty: false, notice: '' }
  const state = shared.modelSelection
  if (controllers.has(state)) return controllers.get(state)
  let queue = Promise.resolve()
  const controller = {
    state,
    choose(value) {
      const choice = fields(value), version = ++state.version
      state.choice = choice; state.dirty = true; state.notice = ''; state.pending++
      queue = queue.then(async () => {
        try {
          await request('/selected-model', { method: 'PUT', body: choice })
          if (version === state.version) { state.dirty = false; state.notice = '' }
        } catch {
          if (version === state.version) state.notice = '模型选择未保存，当前选择仍可发送；请重试保存。'
        } finally { state.pending-- }
      })
      return queue
    },
    beginLoad() { return { version: state.version, pending: state.pending } },
    loaded(data, ticket) {
      // 加载前后有主动切换或保存请求时，不采纳可能过时的选择快照。
      if (ticket.version !== state.version || ticket.pending || state.pending) return
      if (state.choice.profile_id && !data.profiles.some(p => p.id === state.choice.profile_id)) {
        state.choice = {}; state.dirty = false; state.version++
        state.notice = '已选服务已删除，请重新选择模型。'
        return
      }
      if (state.dirty) return
      const choice = data.selected_model || {}
      state.choice = choice.profile_id ? fields(choice) : {}
      state.notice = !choice.unavailable ? ''
        : choice.reason === 'profile_changed' ? '已选服务的地址或协议已修改，原模型不再可用，请重新选择模型。'
        : '已选服务已删除，请重新选择模型。'
    },
  }
  controllers.set(state, controller)
  return controller
}
