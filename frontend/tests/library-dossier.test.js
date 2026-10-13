import { mount, flushPromises } from '@vue/test-utils';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import LibraryDossier from '../components/library/LibraryDossier.vue';

const state = vi.hoisted(() => ({ api: null }));
vi.mock('~/composables/useLibraryApi', () => ({ useLibraryApi: () => state.api }));
const evidence = { id: 'item', kind: 'report', title: '论文结论', content: '新结论', notes: '核对笔记', tags: [], verified: false,
  source: null, report: { answer: '原始结论', citations: [], references: [] } };
const preview = () => ({ token: 'a'.repeat(64), folder_name: '论文投稿', previous_folder_name: '论文投稿', item_count: 1,
  versions: [{ id: 'v1', number: 1, name: '初稿', created_at: 1 }],
  changes: { added: [], removed: [], changed: [{ before: { ...evidence, content: '旧结论' }, after: evidence, fields: ['content'] }] } });
let wrapper;
beforeEach(() => {
  state.api = { getDossier: vi.fn(async () => preview()), saveVersion: vi.fn(), getVersion: vi.fn() };
});
afterEach(() => wrapper?.unmount());

it('compares report bodies and saves explicit human change notes with the reviewed token', async () => {
  state.api.saveVersion.mockResolvedValue({ number: 2 });
  wrapper = mount(LibraryDossier, { props: { account: 'a', folderId: 'folder', privacyMode: false } });
  await flushPromises();
  expect(wrapper.text()).toContain('旧结论');
  expect(wrapper.text()).toContain('新结论');
  await wrapper.find('[name="version-name"]').setValue('修订');
  await wrapper.find('[name="arrangements"]').setValue('延期至周一');
  await wrapper.find('[name="conclusions"]').setValue('修正原截止时间');
  await wrapper.find('form').trigger('submit');
  await flushPromises();
  expect(state.api.saveVersion).toHaveBeenCalledWith('a', 'folder', {
    name: '修订', arrangements: '延期至周一', conclusions: '修正原截止时间', expected_token: 'a'.repeat(64),
  });
  expect(wrapper.text()).toContain('已保存第 2 版');
});

it('keeps draft notes and shows a conflict without automatically retrying or accepting it', async () => {
  state.api.saveVersion.mockRejectedValue({ data: { detail: '资料已变化，请刷新变化对照' } });
  wrapper = mount(LibraryDossier, { props: { account: 'a', folderId: 'folder' } });
  await flushPromises();
  await wrapper.find('[name="version-name"]').setValue('修订');
  await wrapper.find('[name="arrangements"]').setValue('不要丢失');
  await wrapper.find('form').trigger('submit'); await flushPromises();
  expect(wrapper.find('[role="alert"]').text()).toContain('资料已变化');
  expect(wrapper.find('[name="arrangements"]').element.value).toBe('不要丢失');
  expect(state.api.saveVersion).toHaveBeenCalledTimes(1);
});

it('privacy mode masks attachment titles returned in version-save errors', async () => {
  state.api.saveVersion.mockRejectedValue({ data: { detail: '无法保存专题版本：附件副本“私人标题”缺失' } });
  wrapper = mount(LibraryDossier, { props: { account: 'a', folderId: 'folder', privacyMode: true } });
  await flushPromises();
  await wrapper.find('[name="version-name"]').setValue('修订');
  await wrapper.find('form').trigger('submit'); await flushPromises();
  expect(wrapper.find('[role="alert"]').text()).toContain('私人标题');
  expect(wrapper.find('[role="alert"]').classes()).toContain('privacy-blur');
});

it('clears old history and ignores a late response after account and folder switch', async () => {
  let resolveOld;
  state.api.getVersion.mockImplementation(() => new Promise(resolve => { resolveOld = resolve; }));
  wrapper = mount(LibraryDossier, { props: { account: 'a', folderId: 'folder' } });
  await flushPromises();
  await wrapper.find('[name="history-version"]').setValue('v1');
  await wrapper.setProps({ account: 'b', folderId: 'other' }); await flushPromises();
  resolveOld({ id: 'v1', number: 1, name: '私人旧版本', arrangements: '', conclusions: '', items: [evidence], changes: preview().changes });
  await flushPromises();
  expect(wrapper.text()).not.toContain('私人旧版本');
  expect(wrapper.find('[name="history-version"]').element.value).toBe('');
});

it('historical snapshots display their own evidence, notes and masked content with working source controls', async () => {
  state.api.getVersion.mockResolvedValue({ id: 'v1', number: 1, name: '初稿', folder_name: '论文投稿', created_at: 1,
    arrangements: '周五提交', conclusions: '待核对旧结论', previous_id: null,
    items: [{ ...evidence, kind: 'message', report: null, source: { username: 'friend', anchor: 'db:t:1' } }],
    changes: { added: [], removed: [], changed: [] } });
  wrapper = mount(LibraryDossier, { props: { account: 'a', folderId: 'folder', privacyMode: true } });
  await flushPromises();
  await wrapper.find('[name="history-version"]').setValue('v1'); await flushPromises();
  expect(wrapper.text()).toContain('周五提交');
  expect(wrapper.text()).toContain('待核对旧结论');
  await wrapper.findAll('button').find(button => button.text() === '此版本全部资料').trigger('click');
  expect(wrapper.find('.dossier-evidence pre').classes()).toContain('privacy-blur');
  const locate = wrapper.findAll('button').find(button => button.text() === '打开原消息');
  expect(locate.element.closest('.privacy-blur')).toBeNull();
  await locate.trigger('click');
  expect(wrapper.emitted('locate')[0][0]).toEqual({ username: 'friend', anchor: 'db:t:1' });
});

it('downloads from the selected history version and exposes missing-copy errors without a retry', async () => {
  state.api.getVersion.mockResolvedValue({ id: 'v1', number: 1, name: '初稿', folder_name: '论文投稿', created_at: 1,
    arrangements: '', conclusions: '', previous_id: null,
    items: [{ ...evidence, kind: 'attachment', report: null, attachment: { name: '材料.pdf', preservation_note: '' } }],
    changes: { added: [], removed: [], changed: [] } });
  state.api.downloadVersionAttachment = vi.fn().mockRejectedValue(new Error('历史附件副本缺失'));
  wrapper = mount(LibraryDossier, { props: { account: 'a', folderId: 'folder' } });
  await flushPromises();
  await wrapper.find('[name="history-version"]').setValue('v1'); await flushPromises();
  await wrapper.findAll('button').find(button => button.text() === '此版本全部资料').trigger('click');
  await wrapper.findAll('button').find(button => button.text() === '下载附件副本').trigger('click'); await flushPromises();
  expect(state.api.downloadVersionAttachment).toHaveBeenCalledExactlyOnceWith('a', 'folder', 'v1', 'item');
  expect(wrapper.find('[role="alert"]').text()).toContain('历史附件副本缺失');
});
