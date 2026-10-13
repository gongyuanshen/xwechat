import { DOMWrapper, mount, flushPromises } from '@vue/test-utils';
import { ref } from 'vue';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import AttachmentBrowser from '../components/search/AttachmentBrowser.vue';
import { saveAttachmentDownload } from '../utils/attachmentDownload';

const state = vi.hoisted(() => ({ privacy: null, api: null, openAcceleration: vi.fn() }));
vi.mock('~/composables/useSettingsDialog', () => ({ useSettingsDialog: () => ({ openLocalAcceleration: state.openAcceleration }) }));
vi.mock('~/stores/privacy', () => ({ usePrivacyStore: () => state.privacy }));
vi.mock('pinia', () => ({ storeToRefs: store => store }));
vi.mock('~/composables/useAttachmentsApi', () => ({ useAttachmentsApi: () => state.api }));
vi.mock('~/utils/attachmentDownload', () => ({ saveAttachmentDownload: vi.fn() }));
let wrapper, navigation;
const file = (id = 'message_0:Msg_room:1') => ({ id, username: 'room', name: '说明.txt', kind: 'file', conversation_name: '项目组', sender: 'alice', sender_name: 'Alice', create_time: 10, size: 20, extraction: { status: 'not_extracted', text_excerpt: '', text_characters: 0, message: '' } });
const fullText = (extra = {}) => ({ text: '第一行\n第二行\n第三行\n摘录之外的完整第四行', status: 'complete', complete: true, extraction_method: 'local_ocr', ocr_units: 1, ocr_engine: 'RapidOCR', file_hash: 'f'.repeat(64), version: 2, cache_token: 'a'.repeat(64), updated: 1791676800, ...extra });
const recognizedImage = () => ({ ...file(), kind: 'image', name: '凭证图片', extraction: { status: 'complete', extraction_method: 'local_ocr', text_characters: 25, text_excerpt: '仅有摘录' } });
const result = (items = [file()], extra = {}) => ({ status: 'success', items, coverage: { message: '正文搜索仅覆盖已提取内容', total_files: 3, total_images: 2, complete: 0, partial: 0 }, has_more: false, next_offset: 40, ...extra });
const button = text => wrapper.findAll('button').find(row => row.text() === text);
const open = async (props = {}) => {
  const scroll = document.createElement('div'); document.body.appendChild(scroll);
  wrapper = mount(AttachmentBrowser, { props: { account: 'account-a', ...props }, attachTo: scroll, global: { stubs: { LibrarySaveDialog: { name: 'LibrarySaveDialog', props: ['account', 'item'], template: '<div />' } } } });
  await flushPromises();
};
beforeEach(() => {
  state.openAcceleration.mockClear();
  state.privacy = { privacyMode: ref(false) };
  state.api = {
    list: vi.fn(async () => result()),
    filters: vi.fn(async () => ({ status: 'success', conversations: [{ value: 'room', label: '项目组' }], senders: [{ value: 'alice', label: 'Alice' }] })),
    extract: vi.fn(), text: vi.fn(async () => fullText()), download: vi.fn(), contentUrl: vi.fn(() => '/content'),
  };
  navigation = ref(null);
  vi.stubGlobal('useState', () => navigation);
  vi.stubGlobal('navigateTo', vi.fn(async () => true));
  vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn(async () => {}) } });
});
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = ''; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('masks conversation, sender and active extraction filename when privacy mode changes', async () => {
  let finish;
  state.api.extract.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open();
  await button('提取正文').trigger('click');
  const privateFields = [...wrapper.findAll('.attachment-filters select').slice(2), wrapper.get('.attachment-notice')];
  expect(privateFields[2].text()).toContain('说明.txt');
  for (const field of privateFields) expect(field.element.closest('.privacy-blur')).toBeNull();
  state.privacy.privacyMode.value = true; await flushPromises();
  for (const field of privateFields) expect(field.element.closest('.privacy-blur')).not.toBeNull();
  expect(button('停止后续提取').element.closest('.privacy-blur')).toBeNull();
  finish({ status: 'complete', text_characters: 0 }); await flushPromises();
  state.privacy.privacyMode.value = false; await flushPromises();
  for (const field of privateFields) expect(field.element.closest('.privacy-blur')).toBeNull();
});

it.each(['filters', 'list', 'index'])('masks private %s errors without hiding the actual failure', async source => {
  const error = '无法读取 C:/private/account-a/私密合同.txt';
  state.privacy.privacyMode.value = true;
  if (source === 'filters') state.api.filters.mockRejectedValue(new Error(error));
  if (source === 'list') state.api.list.mockRejectedValue(new Error(error));
  if (source === 'index') state.api.list.mockResolvedValue(result([], { status: 'index_error', message: error }));
  await open();
  const alert = wrapper.findAll('[role=alert]').find(node => node.text().includes(error));
  expect(alert.element.closest('.privacy-blur')).not.toBeNull();
  state.privacy.privacyMode.value = false; await flushPromises();
  expect(alert.element.closest('.privacy-blur')).toBeNull();
  expect(alert.text()).toContain(error);
});

it('opens shared acceleration settings and displays recorded OCR partition evidence', async () => {
  const image = recognizedImage();
  image.extraction.runtime = { actual_device: 'dml', device_name: 'AMD RX', evidence: { cpu_nodes: 2 } };
  state.api.list.mockResolvedValue(result([image]));
  await open();
  await button('管理本地加速').trigger('click');
  expect(state.openAcceleration).toHaveBeenCalledOnce();
  expect(wrapper.text()).toContain('GPU + 部分 CPU');
});

it('browses without keywords and applies all filters with local calendar bounds', async () => {
  await open();
  expect(state.api.list).toHaveBeenCalledWith('account-a', expect.objectContaining({ q: '', kind: 'all', offset: 0 }), expect.any(AbortSignal));
  const selects = wrapper.findAll('.attachment-filters select');
  await selects[0].setValue('body'); await selects[1].setValue('file'); await selects[2].setValue('room'); await selects[3].setValue('alice');
  await wrapper.setProps({ q: '正文', start: '2026-10-01', end: '2026-10-09' });
  await wrapper.vm.search(); await flushPromises();
  expect(state.api.list).toHaveBeenLastCalledWith('account-a', expect.objectContaining({ q: '正文', search_in: 'body', kind: 'file', username: 'room', sender: 'alice', start_time: new Date('2026-10-01T00:00:00').getTime() / 1000, end_time: new Date('2026-10-09T23:59:59').getTime() / 1000 }), expect.any(AbortSignal));
  expect(wrapper.text()).toContain('正文搜索仅覆盖已提取内容');
  await wrapper.setProps({ start: '2026-10-10' }); await wrapper.vm.search(); await flushPromises();
  expect(wrapper.text()).toContain('开始日期不能晚于结束日期');
});

it('ignores stale account and filter results and aborts the pending request', async () => {
  let resolveOld;
  state.api.list.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
  await open();
  const oldSignal = state.api.list.mock.calls[0][2];
  await wrapper.setProps({ account: 'account-b' }); await flushPromises();
  resolveOld(result([{ ...file(), name: '旧账号秘密' }])); await flushPromises();
  expect(oldSignal.aborted).toBe(true);
  expect(wrapper.text()).not.toContain('旧账号秘密');
  await wrapper.setProps({ q: '新条件' });
  expect(wrapper.findAll('.attachment-row')).toHaveLength(0);
  expect(wrapper.text()).toContain('筛选条件已改变');
});

it('stops after the active extraction and never calls a model or the next file', async () => {
  let finish;
  state.api.list.mockResolvedValue(result([file(), file('message_0:Msg_room:2')]));
  state.api.extract.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open();
  await button('提取当前页文件正文').trigger('click');
  await button('停止后续提取').trigger('click');
  finish({ status: 'partial', text_excerpt: '已解析文本', text_characters: 5, message: '图片未解析' }); await flushPromises();
  expect(state.api.extract).toHaveBeenCalledTimes(1);
  expect(state.api.extract).toHaveBeenCalledWith('account-a', { username: 'room', anchor: file().id }, expect.any(AbortSignal));
  expect(wrapper.text()).toContain('已停止后续提取');
  expect(wrapper.text()).toContain('正文部分提取');
  expect(wrapper.text()).toContain('图片未解析');
  expect(wrapper.text()).toContain('正文摘录（已提取 5 字）');
});

it('account switch suppresses extraction completion and offers only authoritative save and locate sources', async () => {
  let finish;
  state.api.extract.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open(); await button('提取正文').trigger('click');
  await wrapper.setProps({ account: 'account-b' }); await flushPromises();
  finish({ status: 'complete', text_excerpt: '旧账号正文', text_characters: 5 }); await flushPromises();
  expect(wrapper.text()).not.toContain('旧账号正文');
  await button('加入资料夹').trigger('click');
  expect(wrapper.findComponent({ name: 'LibrarySaveDialog' }).props()).toEqual({ account: 'account-b', item: { kind: 'attachment', source: { username: 'room', anchor: file().id }, title: '说明.txt' } });
  await button('原消息').trigger('click'); await flushPromises();
  expect(navigation.value).toEqual({ kind: 'source', origin: 'search', account: 'account-b', username: 'room', anchor: file().id });
});

it('surfaces structured download and extraction failures without success notices', async () => {
  state.api.extract.mockRejectedValue({ data: { detail: { message: '原始文件缺失' } } });
  state.api.download.mockRejectedValue({ data: { detail: { message: '附件无法读取' } } });
  await open(); await button('提取正文').trigger('click'); await flushPromises();
  expect(wrapper.text()).toContain('原始文件缺失');
  expect(wrapper.text()).toContain('失败 1 项');
  await button('下载').trigger('click'); await flushPromises();
  expect(wrapper.text()).toContain('附件无法读取');
  expect(wrapper.text()).not.toContain('[object Object]');
});

it('requests the next page using the returned offset and does not preload videos', async () => {
  state.api.list.mockResolvedValueOnce(result([{ ...file(), kind: 'video' }], { has_more: true, next_offset: 40 })).mockResolvedValueOnce(result([file('message_0:Msg_room:41')]));
  await open();
  expect(wrapper.find('video').exists()).toBe(false);
  await button('下一页').trigger('click'); await flushPromises();
  expect(state.api.list).toHaveBeenLastCalledWith('account-a', expect.objectContaining({ offset: 40 }), expect.any(AbortSignal));
  expect(wrapper.text()).toContain('第 2 页');
});

it('retries a failed page without replacing the current page or resetting its offset', async () => {
  state.api.list.mockResolvedValueOnce(result([file()], { has_more: true, next_offset: 40 }))
    .mockRejectedValueOnce(new Error('索引读取失败'))
    .mockResolvedValueOnce(result([{ ...file('message_0:Msg_room:41'), name: '下一页.txt' }]));
  await open(); await button('下一页').trigger('click'); await flushPromises();
  expect(wrapper.text()).toContain('索引读取失败');
  expect(wrapper.text()).toContain('说明.txt');
  await button('重试本页').trigger('click'); await flushPromises();
  expect(state.api.list).toHaveBeenLastCalledWith('account-a', expect.objectContaining({ offset: 40 }), expect.any(AbortSignal));
  expect(wrapper.text()).toContain('下一页.txt');
  expect(wrapper.text()).not.toContain('索引读取失败');
});

it('shows the actual index failure and skips terminal extraction states in batch', async () => {
  state.api.list.mockResolvedValueOnce(result([], { status: 'index_error', message: '索引文件损坏', index: { build: { error: '具体故障' } } }))
    .mockResolvedValueOnce(result([
      { ...file(), extraction: { status: 'unsupported' } },
      { ...file('message_0:Msg_room:2'), extraction: { status: 'no_text' } },
    ]));
  await open(); expect(wrapper.text()).toContain('索引文件损坏');
  await wrapper.vm.search(); await flushPromises();
  expect(button('提取当前页文件正文').attributes('disabled')).toBeDefined();
  expect(wrapper.text()).toContain('当前筛选范围内共 3 个文件');
});
it.each(['image', 'video'])('gives conditional download guidance when a %s preview fails', async kind => {
  state.api.list.mockResolvedValue(result([{ ...file(), kind }]));
  await open();
  await button('预览').trigger('click');
  await flushPromises();
  const preview = new DOMWrapper(document.querySelector('.attachment-preview'));
  await preview.find(kind === 'image' ? 'img' : 'video').trigger('error');
  const alert = preview.find('[role=alert]').text();
  expect(alert).toContain('若尚未下载，请先在电脑版微信中打开并下载，再重试');
  expect(alert).toContain('若已下载仍无法预览，请点击“下载”查看具体错误');
  if (kind === 'video') expect(alert).toContain('浏览器也可能不支持此视频编码');
  expect(state.api.download).not.toHaveBeenCalled();
});

it.each(['image', 'video'])('opens %s in a separate modal without scrolling the attachment list', async kind => {
  const item = { ...file(), kind, name: '本机附件' };
  state.api.list.mockResolvedValue(result([item]));
  const scrollIntoView = vi.spyOn(HTMLElement.prototype, 'scrollIntoView');
  await open();
  const scroll = wrapper.element.parentElement, trigger = button('预览');
  scroll.scrollTop = 420;
  trigger.element.focus({ preventScroll: true });
  await trigger.trigger('click'); await flushPromises();
  const dialog = document.querySelector('dialog.attachment-preview');
  expect(dialog).not.toBeNull();
  expect(dialog.parentElement).toBe(document.body);
  expect(dialog.open).toBe(true);
  expect(scroll.contains(dialog)).toBe(false);
  expect(scroll.scrollTop).toBe(420);
  expect(scrollIntoView).not.toHaveBeenCalled();
  expect(dialog.querySelector(kind === 'image' ? 'img' : 'video').getAttribute('src')).toBe('/content');
  expect(state.api.contentUrl).toHaveBeenCalledWith('account-a', expect.objectContaining({ id: item.id, username: item.username }));
  await new DOMWrapper(dialog).find('button').trigger('click'); await flushPromises();
  expect(document.querySelector('.attachment-preview')).toBeNull();
  expect(document.activeElement).toBe(trigger.element);
  expect(scroll.scrollTop).toBe(420);
});

it.each(['cancel', 'backdrop'])('closes the media modal through %s without moving the list', async action => {
  state.api.list.mockResolvedValue(result([recognizedImage()]));
  await open();
  const trigger = button('预览'), scroll = wrapper.element.parentElement;
  scroll.scrollTop = 420;
  await trigger.trigger('click'); await flushPromises();
  const dialog = new DOMWrapper(document.querySelector('dialog.attachment-preview'));
  if (action === 'cancel') await dialog.trigger('cancel');
  else await dialog.trigger('click', { clientX: -1, clientY: -1 });
  await flushPromises();
  expect(document.querySelector('.attachment-preview')).toBeNull();
  expect(document.activeElement).toBe(trigger.element);
  expect(scroll.scrollTop).toBe(420);
});

it('preserves privacy in the modal and closes it when switching accounts', async () => {
  state.api.list.mockResolvedValue(result([recognizedImage()]));
  state.privacy.privacyMode.value = true;
  await open(); await button('预览').trigger('click'); await flushPromises();
  const dialog = document.querySelector('dialog.attachment-preview');
  expect(dialog).not.toBeNull();
  expect(dialog.querySelector('img').classList.contains('privacy-blur')).toBe(true);
  expect(dialog.querySelector('h2').classList.contains('privacy-blur')).toBe(true);
  await wrapper.setProps({ account: 'account-b' }); await flushPromises();
  expect(document.querySelector('.attachment-preview')).toBeNull();
});

it('does not open a preview after its account changes before the next render', async () => {
  state.api.list.mockResolvedValue(result([recognizedImage()]));
  await open();
  button('预览').element.click();
  await wrapper.setProps({ account: 'account-b' }); await flushPromises();
  expect(document.querySelector('.attachment-preview')).toBeNull();
});

it('explicitly requests local OCR and displays recognized image text with its source', async () => {
  state.api.list.mockResolvedValue(result([{ ...file(), kind: 'image', name: '聊天图片' }]));
  state.api.extract.mockResolvedValue({ status: 'complete', extraction_method: 'local_ocr', ocr_units: 1, text_excerpt: '发票编号 12345', text_characters: 10, message: '' });
  await open();
  expect(button('识别图片文字')).toBeDefined();
  await button('识别图片文字').trigger('click'); await flushPromises();
  expect(state.api.extract).toHaveBeenCalledWith('account-a', { username: 'room', anchor: file().id, ocr: true }, expect.any(AbortSignal));
  expect(wrapper.find('.attachment-row').text()).toContain('发票编号 12345');
  expect(wrapper.find('.attachment-row').text()).toContain('本地 OCR');
  expect(wrapper.find('.attachment-row').text()).toContain('请核对原图');
  expect(wrapper.text()).toContain('2 张图片');
});

it('offers OCR for supported images and PDFs and skips previously recognized attachments in batch', async () => {
  const attachments = [
    { ...file(), name: '扫描.PDF', extraction: { status: 'no_text', extraction_method: 'document_text' } },
    { ...file('message_0:Msg_room:2'), name: '发票.TIFF' },
    { ...file('message_0:Msg_room:3'), kind: 'image', name: '聊天图片' },
    { ...file('message_0:Msg_room:4'), name: '已识别.pdf', extraction: { status: 'complete', extraction_method: 'local_ocr', ocr_units: 1 } },
    { ...file('message_0:Msg_room:5'), name: '动画.gif' },
    { ...file('message_0:Msg_room:6'), kind: 'video', name: '视频.mp4' },
    { ...file('message_0:Msg_room:7'), name: '报告.docx' },
  ];
  state.api.list.mockResolvedValue(result(attachments));
  state.api.extract.mockResolvedValue({ status: 'no_text', extraction_method: 'local_ocr', ocr_units: 1, text_excerpt: '', text_characters: 0, message: '未识别出文字' });
  await open();
  const rows = wrapper.findAll('.attachment-row');
  expect(rows[0].text()).toContain('识别 PDF 图片文字');
  expect(rows[1].text()).toContain('识别图片文字');
  for (const index of [4, 5, 6]) expect(rows[index].findAll('button').some(row => row.text().startsWith('识别'))).toBe(false);
  expect(button('识别当前页图片与 PDF 文字')).toBeDefined();
  await button('识别当前页图片与 PDF 文字').trigger('click'); await flushPromises();
  expect(state.api.extract.mock.calls.map(call => call[1])).toEqual([
    { username: 'room', anchor: 'message_0:Msg_room:1', ocr: true },
    { username: 'room', anchor: 'message_0:Msg_room:2', ocr: true },
    { username: 'room', anchor: 'message_0:Msg_room:3', ocr: true },
  ]);
  expect(button('识别当前页图片与 PDF 文字').attributes('disabled')).toBeDefined();
});

it('stops OCR after the active attachment without aborting it or starting the next one', async () => {
  let finish;
  state.api.list.mockResolvedValue(result([{ ...file(), kind: 'image' }, { ...file('message_0:Msg_room:2'), name: '扫描.pdf' }]));
  state.api.extract.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open();
  expect(button('识别当前页图片与 PDF 文字')).toBeDefined();
  await button('识别当前页图片与 PDF 文字').trigger('click');
  const signal = state.api.extract.mock.calls[0][2];
  await button('停止后续识别').trigger('click');
  expect(signal.aborted).toBe(false);
  finish({ status: 'complete', extraction_method: 'local_ocr', ocr_units: 1, text_excerpt: '当前图片文字', text_characters: 6, message: '' }); await flushPromises();
  expect(state.api.extract).toHaveBeenCalledTimes(1);
  expect(wrapper.text()).toContain('当前图片文字');
  expect(wrapper.text()).toContain('已停止后续识别');
});

it.each(['account', 'filter'])('suppresses stale OCR completion after a %s change', async change => {
  let finish;
  state.api.list.mockImplementation(async () => result([{ ...file(), kind: 'image' }]));
  state.api.extract.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open();
  expect(button('识别图片文字')).toBeDefined();
  await button('识别图片文字').trigger('click');
  const signal = state.api.extract.mock.calls[0][2];
  if (change === 'account') await wrapper.setProps({ account: 'account-b' });
  else await wrapper.setProps({ q: '新查询' });
  await flushPromises();
  finish({ status: 'complete', extraction_method: 'local_ocr', ocr_units: 1, text_excerpt: '旧账号图片隐私文字', text_characters: 9, message: '' }); await flushPromises();
  expect(signal.aborted).toBe(true);
  expect(wrapper.text()).not.toContain('旧账号图片隐私文字');
  expect(wrapper.text()).not.toContain('识别结束');
});

it('shows the actual OCR failure without silently requesting document extraction', async () => {
  state.api.list.mockResolvedValue(result([{ ...file(), name: '扫描.pdf' }]));
  state.api.extract.mockRejectedValue({ data: { detail: { message: '本地 OCR 模型文件缺失' } } });
  await open();
  expect(button('识别 PDF 图片文字')).toBeDefined();
  await button('识别 PDF 图片文字').trigger('click'); await flushPromises();
  expect(wrapper.find('.attachment-row [role=alert]').text()).toContain('本地 OCR 模型文件缺失');
  expect(wrapper.text()).toContain('失败 1 项');
  expect(state.api.extract).toHaveBeenCalledTimes(1);
  expect(state.api.extract.mock.calls[0][1]).toEqual({ username: 'room', anchor: file().id, ocr: true });
});

it('keeps image files out of document extraction while allowing their OCR', async () => {
  state.api.list.mockResolvedValue(result([{ ...file(), name: '发票.PNG' }]));
  await open();
  expect(button('提取当前页文件正文').attributes('disabled')).toBeDefined();
  expect(button('提取正文')).toBeUndefined();
  expect(button('识别图片文字').attributes('disabled')).toBeUndefined();
  expect(button('识别当前页图片与 PDF 文字').attributes('disabled')).toBeUndefined();
});

it('opens full cached text beyond the excerpt and copies or exports that full text', async () => {
  state.api.list.mockResolvedValue(result([recognizedImage()]));
  await open();
  expect(button('查看全文')).toBeDefined();
  await button('查看全文').trigger('click'); await flushPromises();
  expect(state.api.text).toHaveBeenCalledWith('account-a', { username: 'room', anchor: file().id }, expect.any(AbortSignal));
  expect(wrapper.find('.attachment-fulltext pre').text()).toBe(fullText().text);
  expect(wrapper.find('.attachment-fulltext').text()).toContain('本地OCR识别文字');
  await button('复制全文').trigger('click'); await flushPromises();
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith(fullText().text);
  expect(wrapper.find('.attachment-fulltext').text()).toContain('全文已复制');
  await button('导出 TXT').trigger('click');
  const download = saveAttachmentDownload.mock.calls.at(-1)[0];
  expect(download.filename).toMatch(/\.txt$/);
  expect(await download.blob.text()).toBe(fullText().text);
  expect(download.blob.type).toBe('text/plain;charset=utf-8');
});

it('opens full text after individual recognition but leaves batch results collapsed', async () => {
  state.api.list.mockImplementation(async () => result([{ ...file(), kind: 'image' }]));
  state.api.extract.mockResolvedValue(recognizedImage().extraction);
  await open();
  await button('识别图片文字').trigger('click'); await flushPromises();
  expect(wrapper.find('.attachment-fulltext pre').exists()).toBe(true);
  expect(wrapper.find('.attachment-fulltext pre').text()).toContain('完整第四行');
  await button('关闭全文').trigger('click');
  state.api.text.mockClear();
  await wrapper.vm.search(); await flushPromises();
  await button('识别当前页图片与 PDF 文字').trigger('click'); await flushPromises();
  expect(state.api.text).not.toHaveBeenCalled();
  expect(wrapper.find('.attachment-fulltext').exists()).toBe(false);
});

it('saves authoritative full text by its source and cache token without sending content', async () => {
  state.api.list.mockResolvedValue(result([recognizedImage()]));
  await open();
  expect(button('查看全文')).toBeDefined();
  await button('查看全文').trigger('click'); await flushPromises();
  await button('保存识别文字到资料夹').trigger('click');
  const dialog = wrapper.findComponent({ name: 'LibrarySaveDialog' });
  expect(dialog.props('item')).toEqual({ kind: 'extracted_text', source: { username: 'room', anchor: file().id }, cache_token: 'a'.repeat(64), title: '凭证图片 · 识别文字' });
  dialog.vm.$emit('saved', { kind: 'extracted_text' }); await flushPromises();
  expect(wrapper.text()).toContain('文字及出处已保存到本地资料夹');
  expect(wrapper.text()).not.toContain('附件副本已保存');
});

it('marks partial full text and disables saving it while allowing copy and TXT export', async () => {
  state.api.list.mockResolvedValue(result([recognizedImage()]));
  state.api.text.mockResolvedValue(fullText({ status: 'partial', complete: false }));
  await open();
  expect(button('查看全文')).toBeDefined();
  await button('查看全文').trigger('click'); await flushPromises();
  expect(wrapper.find('.attachment-fulltext').text()).toContain('部分结果');
  expect(button('保存识别文字到资料夹').attributes('disabled')).toBeDefined();
  expect(button('复制全文').attributes('disabled')).toBeUndefined();
  expect(button('导出 TXT').attributes('disabled')).toBeUndefined();
});

it('reports full text and clipboard failures instead of copying the excerpt or showing success', async () => {
  state.api.list.mockResolvedValue(result([recognizedImage()]));
  state.api.text.mockRejectedValueOnce({ data: { detail: { message: '缓存与原附件不一致，请重新识别' } } });
  await open();
  expect(button('查看全文')).toBeDefined();
  await button('查看全文').trigger('click'); await flushPromises();
  expect(wrapper.find('.attachment-fulltext [role=alert]').text()).toContain('缓存与原附件不一致');
  expect(wrapper.find('.attachment-fulltext pre').exists()).toBe(false);
  expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
  await button('重试读取全文').trigger('click'); await flushPromises();
  navigator.clipboard.writeText.mockRejectedValue(new Error('剪贴板访问被拒绝'));
  await button('复制全文').trigger('click'); await flushPromises();
  expect(wrapper.find('.attachment-fulltext [role=alert]').text()).toContain('剪贴板访问被拒绝');
  expect(wrapper.text()).not.toContain('全文已复制');
});

it.each(['account', 'filter', 'close', 'unmount'])('aborts full text loading and suppresses stale data after %s', async change => {
  let finish;
  state.api.list.mockImplementation(async () => result([recognizedImage()]));
  state.api.text.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open();
  expect(button('查看全文')).toBeDefined();
  await button('查看全文').trigger('click');
  const signal = state.api.text.mock.calls[0][2];
  if (change === 'account') await wrapper.setProps({ account: 'account-b' });
  if (change === 'filter') await wrapper.setProps({ q: '新条件' });
  if (change === 'close') await button('关闭全文').trigger('click');
  if (change === 'unmount') wrapper.unmount();
  await flushPromises();
  expect(signal.aborted).toBe(true);
  finish(fullText({ text: '旧账号全文隐私' })); await flushPromises();
  expect(wrapper.text()).not.toContain('旧账号全文隐私');
  if (change !== 'unmount') expect(wrapper.find('.attachment-fulltext').exists()).toBe(false);
});

it('suppresses a late clipboard notice after switching accounts and keeps full text private', async () => {
  let finish;
  state.api.list.mockImplementation(async () => result([recognizedImage()]));
  navigator.clipboard.writeText.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open();
  expect(button('查看全文')).toBeDefined();
  await button('查看全文').trigger('click'); await flushPromises();
  state.privacy.privacyMode.value = true; await flushPromises();
  expect(wrapper.find('.attachment-fulltext pre').classes()).toContain('privacy-blur');
  expect(button('复制全文').element.closest('.privacy-blur')).toBeNull();
  await button('复制全文').trigger('click');
  await wrapper.setProps({ account: 'account-b' }); await flushPromises();
  finish(); await flushPromises();
  expect(wrapper.text()).not.toContain('全文已复制');
  expect(wrapper.find('.attachment-fulltext').exists()).toBe(false);
});

it('keeps successful OCR separate from a failure to read its full text', async () => {
  state.api.list.mockResolvedValue(result([{ ...file(), kind: 'image' }]));
  state.api.extract.mockResolvedValue(recognizedImage().extraction);
  state.api.text.mockRejectedValue(new Error('全文读取失败'));
  await open();
  await button('识别图片文字').trigger('click'); await flushPromises();
  expect(wrapper.find('.attachment-fulltext [role=alert]').text()).toBe('全文读取失败');
  expect(wrapper.find('.attachment-notice').text()).toContain('处理 1 项，失败 0 项');
  expect(wrapper.find('.attachment-extraction').text()).toContain('文字已识别');
  expect(button('重试读取全文')).toBeDefined();
  expect(state.api.extract).toHaveBeenCalledTimes(1);
});

it('uses the shared search controls without rendering another form or page scroll container', async () => {
  await open({ q: '初始关键词', start: '2026-10-01', end: '2026-10-09' });
  expect(state.api.list).toHaveBeenCalledWith('account-a', expect.objectContaining({
    q: '初始关键词', start_time: new Date('2026-10-01T00:00:00').getTime() / 1000,
    end_time: new Date('2026-10-09T23:59:59').getTime() / 1000,
  }), expect.any(AbortSignal));
  expect(wrapper.find('form, main, input[type=search], input[type=date], .records-page__scroll').exists()).toBe(false);
  expect(wrapper.findAll('.attachment-filters select')).toHaveLength(4);
});

it.each([
  ['keyword', { q: '新关键词' }],
  ['start date', { start: '2026-10-01' }],
  ['end date', { end: '2026-10-09' }],
])('invalidates a pending page when the shared %s changes, without starting another query', async (_, props) => {
  let finish;
  state.api.list.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await open();
  const oldSignal = state.api.list.mock.calls[0][2];
  await wrapper.setProps(props);
  expect(oldSignal.aborted).toBe(true);
  expect(state.api.list).toHaveBeenCalledTimes(1);
  finish(result([{ ...file(), name: '旧条件结果' }])); await flushPromises();
  expect(wrapper.text()).not.toContain('旧条件结果');
  expect(wrapper.text()).toContain('点击上方“查询附件”');
  await wrapper.vm.search(); await flushPromises();
  expect(state.api.list).toHaveBeenCalledTimes(2);
  expect(wrapper.text()).toContain('说明.txt');
});

it('resets account-specific filters and suppresses late filter choices after changing account', async () => {
  let finish;
  await open();
  const selects = wrapper.findAll('.attachment-filters select');
  await selects[0].setValue('body'); await selects[1].setValue('file');
  await selects[2].setValue('room'); await selects[3].setValue('alice');
  state.api.filters.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await wrapper.vm.search(); await flushPromises();
  const oldSignal = state.api.filters.mock.calls.at(-1)[1];
  state.api.filters.mockResolvedValue({ status: 'success', conversations: [{ value: 'new-room', label: '新账号会话' }], senders: [] });
  await wrapper.setProps({ account: 'account-b' }); await flushPromises();
  expect(oldSignal.aborted).toBe(true);
  expect(state.api.list).toHaveBeenLastCalledWith('account-b', expect.objectContaining({ search_in: 'all', kind: 'all', username: '', sender: '' }), expect.any(AbortSignal));
  finish({ status: 'success', conversations: [{ value: 'secret', label: '旧账号筛选秘密' }], senders: [] }); await flushPromises();
  expect(wrapper.text()).toContain('新账号会话');
  expect(wrapper.text()).not.toContain('旧账号筛选秘密');
});

it('refreshes restored account data on revision and retains its detailed filters', async () => {
  let finish;
  await open();
  await wrapper.findAll('.attachment-filters select')[1].setValue('file');
  state.api.list.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const pending = wrapper.vm.search(); await flushPromises();
  const oldSignal = state.api.list.mock.calls.at(-1)[2];
  state.api.list.mockResolvedValue(result([{ ...file(), name: '恢复后的附件.txt' }]));
  await wrapper.setProps({ revision: 1 }); await flushPromises();
  expect(oldSignal.aborted).toBe(true);
  expect(state.api.list).toHaveBeenLastCalledWith('account-a', expect.objectContaining({ kind: 'file' }), expect.any(AbortSignal));
  finish(result([{ ...file(), name: '恢复前的附件.txt' }])); await pending; await flushPromises();
  expect(wrapper.text()).toContain('恢复后的附件.txt');
  expect(wrapper.text()).not.toContain('恢复前的附件.txt');
});

it.each(['account', 'revision', 'query', 'unmount'])('cancels an active download and ignores its response after %s changes', async change => {
  let finish;
  state.api.download.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  saveAttachmentDownload.mockClear();
  await open(); await button('下载').trigger('click');
  const signal = state.api.download.mock.calls[0][2];
  if (change === 'account') await wrapper.setProps({ account: 'account-b' });
  if (change === 'revision') await wrapper.setProps({ revision: 1 });
  if (change === 'query') await wrapper.setProps({ q: '新关键词' });
  if (change === 'unmount') wrapper.unmount();
  expect(signal.aborted).toBe(true);
  finish({ blob: new Blob(['旧附件']), filename: '旧附件.txt' }); await flushPromises();
  expect(saveAttachmentDownload).not.toHaveBeenCalled();
});

it('aborts pending attachment and filter requests when its mode is unmounted', async () => {
  let finishList, finishFilters;
  state.api.list.mockImplementation(() => new Promise(resolve => { finishList = resolve; }));
  state.api.filters.mockImplementation(() => new Promise(resolve => { finishFilters = resolve; }));
  await open();
  const listSignal = state.api.list.mock.calls[0][2], filterSignal = state.api.filters.mock.calls[0][1];
  wrapper.unmount();
  expect(listSignal.aborted).toBe(true);
  expect(filterSignal.aborted).toBe(true);
  finishList(result()); finishFilters({ status: 'success', conversations: [], senders: [] }); await flushPromises();
  expect(document.querySelector('.attachment-browser')).toBeNull();
});
