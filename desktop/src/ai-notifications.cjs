const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

function validTarget(value) {
  return value && typeof value.account === 'string'
    && ((/^[a-f0-9]{32}$/.test(value.task_id || '') && value.matter_id === undefined)
      || (/^[a-f0-9]{32}$/.test(value.matter_id || '') && value.task_id === undefined))
    && typeof (value.username || '') === 'string' && typeof (value.anchor || '') === 'string';
}

function createAiNotifications({ Notification, getPort, dataDir, navigate, diagnostics = { record() {} } }) {
  let stopped = false, request = null, reconnectTimer = null, pendingTarget = null, disconnected = false;
  const statePath = path.join(dataDir, 'ai-notifications.json');
  const delivered = new Set();
  // 保留原生通知对象，直到点击或关闭；macOS 通知中心可能稍后才回调。
  const activeNotifications = new Set();
  try { for (const id of JSON.parse(fs.readFileSync(statePath, 'utf8'))) delivered.add(String(id)); } catch {}
  const save = () => {
    fs.mkdirSync(dataDir, { recursive: true });
    const temp = `${statePath}.tmp`;
    fs.writeFileSync(temp, JSON.stringify([...delivered].slice(-10000)));
    fs.renameSync(temp, statePath);
  };
  const acknowledge = (id, metadata) => {
    const req = http.request({ hostname: '127.0.0.1', port: getPort(), path: `/api/ai/events/${id}/ack`, method: 'POST', timeout: 5000 }, res => {
      res.resume(); diagnostics.record(res.statusCode >= 200 && res.statusCode < 300 ? 'notification.ack' : 'notification.ack_failed', { ...metadata, http_status: res.statusCode });
    });
    req.on('error', () => diagnostics.record('notification.ack_failed', { ...metadata, reason_code: 'network' })); req.on('timeout', () => req.destroy(new Error('timeout'))); req.end();
  };
  const dispatch = event => {
    if (event.kind !== 'notification' || !Number.isSafeInteger(event.id) || !validTarget(event.body?.target)) return;
    // 唯一键包含任务 ID；后端数据库重建后不会与旧递增 ID 冲突。
    const key = event.unique_key || `${event.body.target.matter_id || event.body.target.task_id}:${event.id}`;
    const metadata = { event_id: event.id, task_id: event.body.target.task_id };
    if (delivered.has(key)) { diagnostics.record('notification.duplicate', metadata); acknowledge(event.id, metadata); return; }
    if (!Notification.isSupported()) { diagnostics.record('notification.unsupported', metadata); acknowledge(event.id, metadata); return; }
    delivered.add(key);
    try { save(); } catch { diagnostics.record('notification.failed', metadata); delivered.delete(key); return; }
    try {
    const notification = new Notification({ title: String(event.body.title || 'AI 提醒').slice(0, 120), body: String(event.body.body || '').slice(0, 240) });
    activeNotifications.add(notification);
    notification.on('click', () => {
      diagnostics.record('notification.clicked', metadata); pendingTarget = event.body.target;
      try { Promise.resolve(navigate(pendingTarget)).catch(() => diagnostics.record('navigation.failed', metadata)); }
      catch { diagnostics.record('navigation.failed', metadata); }
      activeNotifications.delete(notification);
    });
    notification.on('close', () => activeNotifications.delete(notification));
    notification.on('show', () => diagnostics.record('notification.shown', metadata));
    notification.on('failed', () => { diagnostics.record('notification.failed', metadata); activeNotifications.delete(notification); });
    diagnostics.record('notification.requested', metadata);
    notification.show();
    acknowledge(event.id, metadata);
    } catch { diagnostics.record('notification.failed', metadata); }
  };
  const reconnect = () => {
    if (stopped) return;
    if (!disconnected) diagnostics.record('sse.disconnected', { component: 'notification' });
    disconnected = true;
    if (!reconnectTimer) reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, 3000);
  };
  const connect = () => {
    if (stopped) return;
    let buffer = '';
    request = http.get({ hostname: '127.0.0.1', port: getPort(), path: '/api/ai/events?notifications=true', headers: { Accept: 'text/event-stream' } }, response => {
      if (response.statusCode !== 200) { response.resume(); reconnect(); return; }
      diagnostics.record(disconnected ? 'sse.recovered' : 'sse.open', { component: 'notification' }); disconnected = false;
      response.setEncoding('utf8');
      response.on('data', chunk => {
        buffer += chunk.replace(/\r\n/g, '\n');
        if (buffer.length > 1024 * 1024) { request.destroy(); return; }
        let end;
        while ((end = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
          const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
          if (data) { try { dispatch(JSON.parse(data)); } catch { diagnostics.record('sse.invalid', { component: 'notification', reason_code: 'parse' }); } }
        }
      });
      response.on('end', reconnect); response.on('error', reconnect);
    });
    request.on('error', reconnect);
    request.on('close', reconnect);
  };
  return { start: connect, stop: () => { stopped = true; clearTimeout(reconnectTimer); request?.destroy(); for (const item of activeNotifications) item.close?.(); activeNotifications.clear(); },
    takeTarget: () => { const value = pendingTarget; pendingTarget = null; return value; }, dispatch };
}
module.exports = { createAiNotifications, validTarget };
