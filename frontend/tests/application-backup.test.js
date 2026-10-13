import { mount, shallowMount, flushPromises } from '@vue/test-utils';
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, toRefs, watch } from 'vue';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { compileStyle, parse } from '@vue/compiler-sfc';
import { createMemoryHistory, createRouter } from 'vue-router';
import ApplicationBackupSettings from '../components/ApplicationBackupSettings.vue';
import SettingsDialog from '../components/SettingsDialog.vue';

const state = vi.hoisted(() => ({ accounts: null, privacy: null }));
vi.mock('~/stores/chatAccounts', () => ({ useChatAccountsStore: () => state.accounts }));
vi.mock('~/stores/applicationAccounts', () => ({ useApplicationAccountsStore: () => state.accounts }));
vi.mock('~/stores/privacy', () => ({ usePrivacyStore: () => state.privacy }));
vi.mock('pinia', () => ({ storeToRefs: store => toRefs(store) }));
let wrapper, request, click, createObjectURL, revokeObjectURL, closeDialog;
const report = () => ({ status: 'restored', account: 'backup-account', data_path: 'D:\\output\\ai',
  added: { matters:1, folders:1, items:2, versions:5, searches:3, attachments:4, pending_reminders:2 },
  kept: { matters:0, folders:1, items:1, versions:3, searches:2, attachments:3, pending_reminders:2 },
  skipped:[{kind:'library_item',id:'local-copy',reason:'local_record_kept'}],
  counts: { matters: 1, folders: 2, items: 3, versions: 8, searches: 5, notes: 2, reports: 1, attachments: 7, pending_reminders: 4 },
  attachment_bytes: 2048, content_sha256: 'a'.repeat(64) });
const button = label => wrapper.findAll('button').find(item => item.text() === label);
const open = async () => { wrapper = mount(ApplicationBackupSettings); await flushPromises(); };
const chooseFile = async (name = 'backup.zip') => {
  const input = wrapper.get('input[type="file"]');
  const file = new File(['archive'], name, { type: 'application/zip' });
  Object.defineProperty(input.element, 'files', { configurable: true, value: [file] });
  await input.trigger('change');
  return file;
};
beforeEach(() => {
  state.accounts = reactive({ accounts: ['account-a', 'account-b'], chatAccounts:['account-a','account-b'], selectedAccount: 'account-a', error: '', revisions:{}, ensureLoaded:vi.fn(async()=>true), notifyRestored:vi.fn(), setSelectedAccount:vi.fn(account=>{state.accounts.selectedAccount=account}) });
  state.privacy = reactive({ privacyMode: false });
  request = vi.fn();
  vi.stubGlobal('useApiBase', () => '/api'); vi.stubGlobal('$fetch', request);
  closeDialog=vi.fn(); vi.stubGlobal('useSettingsDialog',()=>({closeDialog})); vi.stubGlobal('navigateTo',vi.fn(async()=>true));
  createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:backup');
  revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
});
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('exports only the selected account on demand and reports download initiation rather than saved success', async () => {
  await open(); expect(request).not.toHaveBeenCalled();
  const blob = new Blob(['zip'], { type: 'application/zip' }); request.mockResolvedValueOnce(blob);
  await button('下载资料备份').trigger('click'); await flushPromises();
  expect(request).toHaveBeenCalledWith('/api/data-backup/export', expect.objectContaining({ method: 'POST', query: { account: 'account-a' }, responseType: 'blob', retry: 0 }));
  expect(createObjectURL).toHaveBeenCalledWith(blob); expect(click).toHaveBeenCalledTimes(1);
  expect(click.mock.instances[0].download).toBe('xwechat-application-data.zip');
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:backup');
  expect(wrapper.text()).toContain('已发起下载'); expect(wrapper.text()).not.toContain('已保存到');
});

it('restores a ZIP without imported accounts and shows complete verified results', async () => {
  state.accounts.accounts = []; state.accounts.selectedAccount = null; await open();
  expect(button('下载资料备份').element.disabled).toBe(true);
  expect(button('恢复缺失资料，保留本机版本').element.disabled).toBe(true);
  const file = await chooseFile(); request.mockResolvedValueOnce(report());
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  const [url, options] = request.mock.calls[0];
  expect(url).toBe('/api/data-backup/restore'); expect(options).toMatchObject({ method: 'POST', retry: 0 });
  expect(options.query).toBeUndefined(); expect(options.body.get('file').name).toBe(file.name);
  expect(wrapper.text()).toContain('资料已恢复'); expect(wrapper.text()).toContain('发生冲突的已有资料保留本机版本');
  expect(wrapper.text()).toContain(report().data_path); expect(wrapper.text()).toContain(report().account);
  expect(wrapper.text()).toContain(report().content_sha256); expect(wrapper.text()).toContain('2.00 KB');
  const counts = Object.fromEntries(wrapper.findAll('.backup-counts > div').map(row => [row.get('dt').text(), row.get('dd').text()]));
  expect(counts).toEqual({ '事项': '1', '资料夹': '2', '当前资料': '3', '历史版本': '8', '保存的搜索': '5', '当前笔记': '2', '当前报告': '1', '附件': '7', '未投递提醒': '4' });
  expect(wrapper.text()).toContain('笔记、报告只统计当前资料'); expect(wrapper.text()).toContain('历史内容计入版本');
});

it('does not submit files until requested and blocks non-ZIP files at the picker boundary', async () => {
  await open(); expect(wrapper.get('input[type="file"]').attributes('accept')).toContain('.zip');
  await chooseFile('private.txt'); expect(request).not.toHaveBeenCalled();
  expect(wrapper.get('[role="alert"]').text()).toContain('ZIP');
  expect(button('恢复缺失资料，保留本机版本').element.disabled).toBe(true);
});

it('does not download a stale export after the global account changes', async () => {
  let finish; request.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open(); await button('下载资料备份').trigger('click');
  const signal = request.mock.calls[0][1].signal;
  state.accounts.selectedAccount = 'account-b'; await flushPromises();
  finish(new Blob(['old'])); await flushPromises();
  expect(signal.aborted).toBe(true); expect(click).not.toHaveBeenCalled();
  expect(wrapper.get('select').element.value).toBe('account-b'); expect(wrapper.text()).not.toContain('已发起下载');
});

it('preserves a restore report across account changes and its own account refresh', async () => {
  let finish; request.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open(); await chooseFile(); await button('恢复缺失资料，保留本机版本').trigger('click');
  state.accounts.selectedAccount = 'account-b'; await flushPromises();
  finish(report()); await flushPromises();
  expect(wrapper.text()).toContain(report().data_path); expect(wrapper.text()).toContain('资料已恢复');
  expect(state.accounts.notifyRestored).toHaveBeenCalledWith('backup-account');
  expect(state.accounts.ensureLoaded).toHaveBeenCalledWith({force:true});
});

it('discards an old blob error decoded after a new account request succeeds', async () => {
  let finishReading;
  const errorBlob = new Blob(['error'], { type: 'application/json' });
  errorBlob.text = () => new Promise(resolve => { finishReading = resolve; });
  await open(); request.mockRejectedValueOnce({ data: errorBlob });
  await button('下载资料备份').trigger('click'); await flushPromises();
  state.accounts.selectedAccount = 'account-b'; await flushPromises();
  request.mockResolvedValueOnce(new Blob(['new-account']));
  await button('下载资料备份').trigger('click'); await flushPromises();
  finishReading(JSON.stringify({ detail: 'old-account-error' })); await flushPromises();
  expect(wrapper.text()).not.toContain('old-account-error'); expect(wrapper.text()).toContain('已发起下载');
  expect(click).toHaveBeenCalledTimes(1);
});

it('invalidates an in-flight export if its account is removed', async () => {
  let finish; request.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await open(); await button('下载资料备份').trigger('click');
  const signal = request.mock.calls[0][1].signal;
  state.accounts.accounts = []; state.accounts.selectedAccount = ''; await flushPromises();
  finish(new Blob(['removed-account'])); await flushPromises();
  expect(signal.aborted).toBe(true); expect(click).not.toHaveBeenCalled();
  expect(button('下载资料备份').element.disabled).toBe(true);
});

it('aborts on unmount and never triggers a late download', async () => {
  let finish; request.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open(); await button('下载资料备份').trigger('click');
  const signal = request.mock.calls[0][1].signal; wrapper.unmount(); wrapper = null;
  finish(new Blob(['old'])); await flushPromises();
  expect(signal.aborted).toBe(true); expect(click).not.toHaveBeenCalled();
});

it('exposes server validation errors without retry or a verified result', async () => {
  await open(); await chooseFile(); request.mockRejectedValueOnce({ data: { detail: '附件校验失败：private-name' } });
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain('附件校验失败：private-name');
  expect(request).toHaveBeenCalledTimes(1); expect(wrapper.text()).not.toContain('资料已恢复');
});

it('exposes ZIP export JSON errors even when the requested response type is blob', async () => {
  await open(); request.mockRejectedValueOnce({ data: new Blob([JSON.stringify({ detail: '备份文件读取失败' })], { type: 'application/json' }) });
  await button('下载资料备份').trigger('click'); await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain('备份文件读取失败');
  expect(click).not.toHaveBeenCalled(); expect(request).toHaveBeenCalledTimes(1);
});

it('keeps uncertain restore outcomes explicit and does not repeat the upload', async () => {
  await open(); await chooseFile(); request.mockRejectedValueOnce(new Error('连接中断'));
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain('恢复结果未确认');
  expect(request).toHaveBeenCalledTimes(1); expect(wrapper.text()).not.toContain('资料已恢复');
});

it('does not accept an unverified response as a successful restore', async () => {
  await open(); await chooseFile(); request.mockResolvedValueOnce({ ...report(), status: 'failed' });
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain('恢复');
  expect(wrapper.text()).not.toContain('资料已恢复');
});

it('clears old verification when a different archive is chosen', async () => {
  await open(); await chooseFile(); request.mockResolvedValueOnce(report());
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  await chooseFile('another.zip'); expect(wrapper.text()).not.toContain(report().data_path);
});

it('keeps committed restore results visible when account refresh fails, without retrying restoration', async () => {
  await open(); await chooseFile(); request.mockResolvedValueOnce(report());
  state.accounts.ensureLoaded.mockImplementation(async () => { state.accounts.error = '刷新账号失败'; return false; });
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  expect(wrapper.text()).toContain('资料已恢复'); expect(wrapper.text()).toContain('刷新账号失败');
  expect(wrapper.text()).not.toContain('恢复失败'); expect(request).toHaveBeenCalledTimes(1);
  expect(wrapper.findAll('.backup-merge-counts tbody tr').map(row => row.text())).toContain('当前资料21');
  expect(wrapper.text()).toContain('已保留本机已有资料');
});

it('opens restored application data without replacing the report or pretending it is a chat account', async () => {
  await open(); await chooseFile(); request.mockResolvedValueOnce(report());
  state.accounts.ensureLoaded.mockImplementation(async () => { state.accounts.accounts=['account-a','backup-account']; state.accounts.selectedAccount='backup-account'; return true; });
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  expect(wrapper.text()).toContain('资料已恢复');
  await button('查看资料').trigger('click'); await flushPromises();
  expect(navigateTo).toHaveBeenCalledWith('/library'); expect(state.accounts.setSelectedAccount).toHaveBeenCalledWith('backup-account');
  expect(state.accounts.chatAccounts).not.toContain('backup-account'); expect(wrapper.text()).toContain('资料已恢复');
});

it('keeps the completed report open if navigation to restored data is rejected', async () => {
  state.accounts.accounts.push('backup-account'); await open(); await chooseFile(); request.mockResolvedValueOnce(report());
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  vi.spyOn(window,'confirm').mockReturnValue(true);
  navigateTo.mockResolvedValue(false); await button('查看资料').trigger('click'); await flushPromises();
  expect(closeDialog).not.toHaveBeenCalled(); expect(wrapper.text()).toContain('资料已恢复');
  expect(wrapper.get('[role="alert"]').text()).toContain('打开页面失败');
});

it.each(['duplicated', 'aborted', 'cancelled'])('handles a real memory-router %s navigation after restoration', async outcome => {
  const router = createRouter({ history: createMemoryHistory(), routes: ['/library', '/matters'].map(path => ({ path, component: { template: '<div />' } })) });
  await router.push(outcome === 'duplicated' ? '/library' : '/matters');
  let release;
  if (outcome === 'aborted') router.beforeEach(() => false);
  if (outcome === 'cancelled') router.beforeEach(to => to.path === '/library' ? new Promise(resolve => { release = resolve; }) : true);
  navigateTo.mockImplementation(path => router.push(path));
  state.accounts.accounts.push('backup-account'); state.accounts.selectedAccount = 'backup-account';
  await open(); await chooseFile(); request.mockResolvedValueOnce(report());
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  await button('查看资料').trigger('click'); await flushPromises();
  if (outcome === 'cancelled') { await router.push('/matters'); release(true); await flushPromises(); }
  if (outcome === 'duplicated') {
    expect(closeDialog).toHaveBeenCalledTimes(1); expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  } else {
    expect(closeDialog).not.toHaveBeenCalled(); expect(wrapper.get('[role="alert"]').text()).toContain('打开页面失败');
  }
  expect(wrapper.text()).toContain('资料已恢复');
});

it('preserves committed data when successful restoration reports temporary-file cleanup failure', async () => {
  await open(); await chooseFile(); request.mockResolvedValueOnce({...report(),cleanup_error:'暂存目录被占用'});
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  expect(wrapper.text()).toContain('资料已恢复，但暂存文件清理失败：暂存目录被占用');
  expect(state.accounts.notifyRestored).toHaveBeenCalledWith('backup-account'); expect(request).toHaveBeenCalledTimes(1);
});

it('accepts an authoritative restored report attached to an HTTP cleanup failure', async () => {
  await open(); await chooseFile(); state.accounts.accounts.push('backup-account');
  request.mockRejectedValueOnce({data:{detail:{code:'restore_cleanup_failed',restored:report(),message:'资料已恢复，上传暂存目录清理失败'}}});
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  expect(wrapper.text()).toContain('资料已恢复，上传暂存目录清理失败');
  expect(wrapper.text()).toContain(report().data_path); expect(button('查看资料').element.disabled).toBe(false);
  expect(state.accounts.notifyRestored).toHaveBeenCalledWith('backup-account'); expect(request).toHaveBeenCalledTimes(1);
  expect(wrapper.text()).not.toContain('恢复失败：');
});

it('does not discard another account draft by switching accounts without the user choosing to proceed', async () => {
  await open(); await chooseFile(); state.accounts.accounts.push('backup-account'); request.mockResolvedValueOnce(report());
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  vi.spyOn(window,'confirm').mockReturnValue(false); await button('查看资料').trigger('click'); await flushPromises();
  expect(state.accounts.setSelectedAccount).not.toHaveBeenCalled(); expect(navigateTo).not.toHaveBeenCalled();
  expect(wrapper.text()).toContain('资料已恢复');
});

it('masks account, archive name, restored path and errors in privacy mode', async () => {
  state.privacy.privacyMode = true; await open(); await chooseFile('private-name.zip');
  request.mockResolvedValueOnce(report()); await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  for (const selector of ['select', 'input[type="file"]', '.backup-result-account', '.backup-result-path']) {
    expect(wrapper.get(selector).classes()).toContain('privacy-blur');
  }
  request.mockRejectedValueOnce({ data: { detail: 'private-error' } });
  await button('恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  expect(wrapper.get('[role="alert"]').classes()).toContain('privacy-blur');
});

it('releases the generated URL if starting the browser download fails', async () => {
  await open(); request.mockResolvedValueOnce(new Blob(['zip'])); click.mockImplementationOnce(() => { throw new Error('下载被拒绝'); });
  await button('下载资料备份').trigger('click'); await flushPromises();
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:backup');
  expect(wrapper.get('[role="alert"]').text()).toContain('下载被拒绝'); expect(wrapper.text()).not.toContain('已发起下载');
});

it('makes backups reachable from settings navigation and the focus target', async () => {
  for (const [name, value] of Object.entries({ computed, nextTick, onBeforeUnmount, onMounted, ref, watch, useApi: () => ({}) })) vi.stubGlobal(name, value);
  wrapper = shallowMount(SettingsDialog, { props: { open: true }, global: { stubs: { ErrorNotice: true, AiSettings: true, LocalAccelerationSettings: true } } }); await flushPromises();
  await button('资料备份').trigger('click');
  expect(wrapper.get('header h2').text()).toBe('资料备份');
  expect(wrapper.findComponent(ApplicationBackupSettings).exists()).toBe(true);
  await wrapper.setProps({ focusTarget: 'storage' }); await flushPromises();
  expect(wrapper.get('header h2').text()).toBe('磁盘占用');
  await wrapper.setProps({ focusTarget: 'data-backup' }); await flushPromises();
  expect(wrapper.get('header h2').text()).toBe('资料备份');
  await wrapper.setProps({ open: false }); expect(wrapper.findComponent(ApplicationBackupSettings).exists()).toBe(false);
});

it('uses declared theme tokens and readable dark-mode body and error colors', async () => {
  const globalCss = readFileSync(resolve(process.cwd(), 'assets/css/tailwind.css'), 'utf8');
  const source = readFileSync(resolve(process.cwd(), 'components/ApplicationBackupSettings.vue'), 'utf8');
  const css = parse(source).descriptor.styles[0].content;
  const declaredTokens = new Set([...globalCss.matchAll(/(--[\w-]+)\s*:/g)].map(match => match[1]));
  const usedTokens = [...css.matchAll(/var\((--[\w-]+)/g)].map(match => match[1]);
  expect(usedTokens.filter(token => !declaredTokens.has(token))).toEqual([]);
  const style = document.createElement('style');
  style.textContent = globalCss.match(/:root\s*\{[^}]+\}/)[0]
    + globalCss.match(/html\[data-theme='dark'\]\s*\{[^}]+\}/)[0]
    + compileStyle({ source: css, filename: 'ApplicationBackupSettings.vue', id: ApplicationBackupSettings.__scopeId, scoped: true }).code;
  document.head.append(style); document.documentElement.dataset.theme = 'dark';
  try {
    wrapper = mount(ApplicationBackupSettings, { attachTo: document.body });
    await chooseFile('invalid.txt');
    expect(getComputedStyle(wrapper.element).color).toBe('#f5f5f5');
    expect(getComputedStyle(wrapper.get('[role="alert"]').element).color).toBe('#e98b8b');
  } finally { style.remove(); delete document.documentElement.dataset.theme; }
});
