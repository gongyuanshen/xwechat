import { aiDiagnostics, aiTrace } from '../utils/aiDiagnostics'

export const useAiApi = () => {
  const base = useApiBase()
  const diagnostics = aiDiagnostics(base)
  const request = async (path, options = {}) => {
    const trace = aiTrace(), started = Date.now()
    try {
      return await $fetch(`${base}/ai${path}`, { ...options, headers: { ...options.headers, 'X-WCDA-AI-Trace': trace } })
    } catch (error) {
      const detail = error?.data?.detail
      const result = new Error(typeof detail === 'string' ? detail : 'AI 服务请求失败')
      result.status = error?.status || error?.statusCode || error?.response?.status
      result.statusCode = result.status
      result.diagnostic_id = error?.data?.diagnostic_id || error?.response?.headers?.get?.('X-WCDA-AI-Diagnostic')
      result.trace_id = trace
      diagnostics.record('request.failed', { origin: 'frontend', trace_id: trace, diagnostic_id: result.diagnostic_id,
        http_status: result.status, duration_ms: Date.now() - started, method: options.method || 'GET',
        component: path.startsWith('/agent') ? 'agent' : path.startsWith('/local-search') ? 'search' : 'ai', reason_code: result.status ? 'http' : 'network' })
      throw result
    }
  }
  /**
   * 建立高韧性 EventSource 流式连接。
   * 
   * 韧性与重连特性：
   * 1. 自动追踪 Last-Event-ID：在收到包含 id 的 SSE 事件帧时记录游标，
   *    重连时作为 `after` 查询参数回传给后端，实现断点增量补齐。
   * 2. 状态机与确定性指数退避重试：当原生 EventSource 进入 CLOSED (2) 状态时，
   *    按确定性指数退避算法进行有序重连（初始 1s，最大 16s，最大重试 8 次，无随机抖动）。
   * 3. 遵从 RULE[user_global] 的非静默降级规范：
   *    当超出最大重试次数时，显式记录 console.warn 与诊断日志，
   *    通知调用层转入保底 HTTP 轮询模式，并允许通过配置控制重试行为。
   */
  const stream = (path, component, onEvent, onReady, onDisconnected, options = {}) => {
    const {
      maxRetries = 8,
      initialBackoffMs = 1000,
      maxBackoffMs = 16000,
      enableFallback = true,
      initialAfter = null
    } = options

    let lastEventId = initialAfter
    let retryAttempt = 0
    let retryTimer = null
    let disconnected = false
    let closed = false
    let source = null
    let activeTrace = null

    const buildUrl = (afterCursor) => {
      activeTrace = aiTrace()
      const delimiter = path.includes('?') ? '&' : '?'
      let url = `${base}/ai${path}${delimiter}ai_trace=${activeTrace}`
      if (afterCursor != null) {
        if (/([?&])after=[^&]*/.test(url)) {
          url = url.replace(/([?&])after=[^&]*/, `$1after=${encodeURIComponent(afterCursor)}`)
        } else {
          url += `&after=${encodeURIComponent(afterCursor)}`
        }
      }
      return url
    }

    const scheduleReconnect = (metadata) => {
      if (closed || retryTimer) return
      if (retryAttempt >= maxRetries) {
        // 遵从 RULE[user_global]：显式记录 Warn 日志，严禁静默吞掉降级
        console.warn(`[AI Stream] EventSource 重试已达上限 (${maxRetries} 次)，连接已转入保底模式。`, {
          component,
          lastEventId,
          attempts: retryAttempt
        })
        diagnostics.record('sse.max_retries', {
          ...metadata,
          max_retries: maxRetries,
          last_event_id: lastEventId,
          reason_code: 'max_retries_exceeded'
        })
        onDisconnected?.({ permanent: true, fallback: enableFallback, attempts: retryAttempt })
        return
      }

      const delay = Math.min(maxBackoffMs, initialBackoffMs * (2 ** retryAttempt++))
      onDisconnected?.({ attempt: retryAttempt, delay, permanent: false, fallback: enableFallback })

      retryTimer = setTimeout(() => {
        retryTimer = null
        if (!closed) {
          connect()
        }
      }, delay)
    }

    const connect = () => {
      if (closed) return
      const url = buildUrl(lastEventId)
      const metadata = { origin: 'frontend', component, trace_id: activeTrace }

      try {
        source = new EventSource(url)
      } catch (err) {
        console.warn('[AI Stream] 创建 EventSource 失败:', err)
        scheduleReconnect(metadata)
        return
      }

      source.onopen = () => {
        if (closed) return
        const reconnected = disconnected
        retryAttempt = 0
        if (retryTimer) {
          clearTimeout(retryTimer)
          retryTimer = null
        }
        diagnostics.record(reconnected ? 'sse.recovered' : 'sse.open', metadata)
        disconnected = false
        onReady?.({ reconnected })
      }

      source.onerror = () => {
        if (closed) return
        if (!disconnected) {
          diagnostics.record('sse.disconnected', metadata)
          disconnected = true
          onDisconnected?.({ attempt: retryAttempt, permanent: false, fallback: enableFallback })
        }
        // 当原生 EventSource 进入 CLOSED (2) 状态，说明浏览器已停止自动重连，必须由客户端调度重连
        if (source.readyState === 2) {
          try { source.close() } catch {}
          scheduleReconnect(metadata)
        }
      }

      source.onmessage = event => {
        if (closed) return
        if (event.lastEventId) {
          lastEventId = event.lastEventId
        }
        let value
        try {
          value = JSON.parse(event.data)
        } catch {
          diagnostics.record('sse.invalid', { ...metadata, reason_code: 'parse' })
          return
        }
        try {
          onEvent(value, { eventId: event.lastEventId || lastEventId || '' })
        } catch {
          diagnostics.record('sse.invalid', { ...metadata, reason_code: 'rejected' })
        }
      }
    }

    connect()

    return () => {
      closed = true
      if (retryTimer) {
        clearTimeout(retryTimer)
        retryTimer = null
      }
      if (source) {
        try { source.close() } catch {}
        source = null
      }
    }
  }

  const events = (account, onEvent, options) =>
    stream(`/events?account=${encodeURIComponent(account)}`, 'ai', onEvent, undefined, undefined, options)

  const agentEvents = (account, onEvent, onReady, onDisconnected, options) =>
    stream(`/agent/events?account=${encodeURIComponent(account)}`, 'agent', onEvent, onReady, onDisconnected, options)

  const localSearchEvents = (account, onEvent, after = 0, options) =>
    stream(`/local-search/events?after=${encodeURIComponent(after)}${account ? `&account=${encodeURIComponent(account)}` : ''}`, 'search', onEvent, undefined, undefined, options)

  const localAccelerationEvents = (account, onEvent, after = 0, onDisconnected) =>
    stream(`/local-acceleration/events${account ? `?account=${encodeURIComponent(account)}` : ''}`, 'acceleration', onEvent, undefined, onDisconnected, { initialAfter: after, enableFallback: false })

  return { request, events, agentEvents, localSearchEvents, localAccelerationEvents, diagnostic: (event, metadata = {}) => diagnostics.record(event, { origin: 'frontend', ...metadata }) }
}
