const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const { createAiNotifications, validTarget } = require('../src/ai-notifications.cjs');

test('通知持久去重并保留点击目标', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wda-ai-notification-'));
  const shown = [], navigated = [];
  class Notification extends EventEmitter {
    static isSupported() { return true; }
    show() { shown.push(this); }
  }
  const options = { Notification, getPort: () => 1, dataDir: root, navigate: target => navigated.push(target) };
  const service = createAiNotifications(options);
  const target = { account: 'account', task_id: 'a'.repeat(32), username: 'group', anchor: 'db:table:1' };
  const event = { id: 1, kind: 'notification', unique_key: 'unique', body: { title: '项目群', body: '讨论延期', target } };
  service.dispatch(event); service.dispatch(event);
  assert.equal(shown.length, 1);
  shown[0].emit('click');
  assert.deepEqual(navigated, [target]);
  assert.deepEqual(service.takeTarget(), target);
  assert.equal(service.takeTarget(), null);
  const restarted = createAiNotifications(options);
  restarted.dispatch(event);
  assert.equal(shown.length, 1);
  service.stop(); restarted.stop();
  fs.unlinkSync(path.join(root, 'ai-notifications.json')); fs.rmdirSync(root);
});

test('不接受任意跳转 URL 或无效任务标识', () => {
  assert.equal(Boolean(validTarget({ account: 'a', task_id: 'https://example.com' })), false);
  assert.equal(Boolean(validTarget({ account: 'a', matter_id: 'b'.repeat(32) })), true);
  assert.equal(Boolean(validTarget({ account: 'a', matter_id: 'b'.repeat(32), task_id: 'a'.repeat(32) })), false);
  assert.equal(Boolean(validTarget({ account: 'a', matter_id: 'https://example.com' })), false);
});

test('事项到期通知保留独立的账号与事项点击目标', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wda-matter-notification-'));
  const shown = [], navigated = [];
  class Notification extends EventEmitter {
    static isSupported() { return true; }
    show() { shown.push(this); }
  }
  const service = createAiNotifications({ Notification, getPort: () => 1, dataDir: root, navigate: target => navigated.push(target) });
  const target = { account: 'a', matter_id: 'b'.repeat(32) };
  const event = { id: 9, kind: 'notification', unique_key: 'matter:unique:1', body: { title: '事项已到期', body: '打开事项面板查看并更新状态', target } };
  service.dispatch(event); service.dispatch(event);
  assert.equal(shown.length, 1);
  shown[0].emit('click');
  assert.deepEqual(navigated, [target]);
  assert.deepEqual(service.takeTarget(), target);
  service.stop();
  fs.unlinkSync(path.join(root, 'ai-notifications.json')); fs.rmdirSync(root);
});
