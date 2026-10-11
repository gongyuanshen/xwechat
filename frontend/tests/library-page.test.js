import { mount, flushPromises } from "@vue/test-utils";
import { ref } from "vue";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import LibraryPage from "../pages/library.vue";
const state = vi.hoisted(() => ({ store: null, privacy: null }));
vi.mock("~/stores/chatAccounts", () => ({
  useChatAccountsStore: () => state.store,
}));
vi.mock("pinia", () => ({
  storeToRefs: (store) => store,
}));
vi.mock("~/stores/privacy", () => ({
  usePrivacyStore: () => ({ privacyMode: state.privacy }),
}));
let api, wrapper;
const item = {
  id: "item",
  folder_id: "folder",
  kind: "report",
  title: "报告",
  content: "原始回答",
  notes: "",
  tags: [],
  verified: false,
  updated_at: 1,
  report: {
    answer: "原始回答",
    citations: [{ username: "friend", anchor: "stable", text: "证据" }],
  },
};
beforeEach(() => {
  state.privacy = ref(false);
  state.store = { selectedAccount: ref("a"), ensureLoaded: vi.fn(), error: "" };
  api = {
    listFolders: vi.fn(async () => [{ id: "folder", name: "资料" }]),
    listItems: vi.fn(async () => [item]),
    updateItem: vi.fn(),
  };
  vi.stubGlobal("useHead", vi.fn());
  vi.stubGlobal("useLibraryApi", () => api);
  vi.stubGlobal("useState", () => ref(null));
  vi.stubGlobal(
    "navigateTo",
    vi.fn(async () => true),
  );
});
afterEach(() => {
  wrapper?.unmount();
  vi.unstubAllGlobals();
});
it("page write failure preserves editable content and original evidence", async () => {
  api.updateItem.mockRejectedValue(new Error("磁盘不可写"));
  wrapper = mount(LibraryPage);
  await flushPromises();
  await wrapper.find(".item").trigger("click");
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "编辑正文")
    .trigger("click");
  await wrapper.findAll("textarea")[1].setValue("改写回答");
  await wrapper.find(".detail form").trigger("submit");
  await flushPromises();
  expect(wrapper.text()).toContain("磁盘不可写");
  expect(wrapper.findAll("textarea")[1].element.value).toBe("改写回答");
  expect(wrapper.find(".detail details .agent-answer").text()).toContain(
    "原始回答",
  );
  expect(api.updateItem).toHaveBeenCalledWith(
    "a",
    "item",
    expect.objectContaining({
      content: "改写回答",
      verified: false,
      folder_id: "folder",
    }),
  );
});
it("page account switch clears old selection and ignores stale list result", async () => {
  let resolveOld;
  api.listItems
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValueOnce([{ ...item, id: "new", title: "新账号资料" }]);
  wrapper = mount(LibraryPage);
  state.store.selectedAccount.value = "b";
  await flushPromises();
  resolveOld([item]);
  await flushPromises();
  expect(wrapper.text()).toContain("新账号资料");
  expect(wrapper.find(".detail form").exists()).toBe(false);
  expect(wrapper.find(".item strong").text()).toBe("新账号资料");
});

vi.mock("~/composables/useLibraryApi", () => ({
  useLibraryApi: () => globalThis.useLibraryApi(),
}));
it("pending save disables editing and dirty content blocks stale export", async () => {
  let resolveSave;
  api.updateItem.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveSave = resolve;
      }),
  );
  wrapper = mount(LibraryPage);
  await flushPromises();
  await wrapper.find(".item").trigger("click");
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "编辑正文")
    .trigger("click");
  await wrapper.findAll("textarea")[1].setValue("编辑后的回答");
  expect(wrapper.text()).toContain("请先保存修改，再导出当前资料");
  const exportButton = wrapper
    .findAll("button")
    .find((button) => button.text() === "导出 Markdown");
  expect(exportButton.attributes("disabled")).toBeDefined();
  await wrapper.find(".detail form").trigger("submit");
  expect(wrapper.find("fieldset").attributes("disabled")).toBeDefined();
  resolveSave({ ...item, content: "编辑后的回答", user_edited: true });
  await flushPromises();
  expect(wrapper.find("fieldset").attributes("disabled")).toBeUndefined();
  expect(wrapper.find(".detail form .agent-answer").text()).toContain(
    "编辑后的回答",
  );
});

it("saved reports render copied citations and readable coverage without internal ids", async () => {
  const source = "1234567890abcdef12345678";
  api.listItems.mockResolvedValue([
    {
      ...item,
      content: `回答 [[${source}]]`,
      report: {
        answer: `原始回答 [[${source}]]`,
        citations: [
          {
            source,
            username: "friend",
            anchor: "stable",
            text: "证据",
            sender: "好友",
          },
        ],
        references: [],
        query_scope: ["friend"],
        time_range: { start: 100, end: 200 },
        coverage_state: "partial",
        read_count: 5,
        timezone_offset: 28800,
        model_metadata: { profile: { model: "test-model" } },
        coverage_warnings: ["部分范围仍未读取"],
      },
    },
  ]);
  wrapper = mount(LibraryPage);
  await flushPromises();
  await wrapper.find(".item").trigger("click");
  expect(wrapper.find(".detail form .agent-answer").text()).not.toContain(
    source,
  );
  expect(wrapper.find(".detail details .agent-answer").text()).not.toContain(
    source,
  );
  expect(wrapper.find(".report-summary").text()).toContain(
    "仅覆盖部分范围 · 已读取 5 条",
  );
  expect(wrapper.find(".report-summary").text()).toContain("test-model");
  expect(wrapper.find(".report-summary").text()).toContain(
    "1970/01/01 08:01 至 1970/01/01 08:03",
  );
  expect(wrapper.text()).toContain("部分范围仍未读取");
  expect(wrapper.findAll("textarea")).toHaveLength(1);
  expect(wrapper.find(".library-layout").classes()).toContain("has-detail");
  await wrapper.find(".detail-back").trigger("click");
  expect(wrapper.find(".library-layout").classes()).not.toContain("has-detail");
});
it("back to list retains dirty report when discard is declined", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  wrapper = mount(LibraryPage);
  await flushPromises();
  await wrapper.find(".item").trigger("click");
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "编辑正文")
    .trigger("click");
  await wrapper.findAll("textarea")[1].setValue("未保存内容");
  await wrapper.find(".detail-back").trigger("click");
  expect(wrapper.find(".library-layout").classes()).toContain("has-detail");
  expect(wrapper.findAll("textarea")[1].element.value).toBe("未保存内容");
  confirm.mockRestore();
});
it('shows a saved attachment and its preservation boundary, with source-independent download errors', async () => {
  api.listItems.mockResolvedValue([{ ...item, kind: 'attachment', report: null, source: { username: 'friend', anchor: 'stable' }, attachment: { name: '备份.png', kind: 'image', size: 42, preservation_note: '可能为缩略图，不保证原图。' } }]);
  api.attachmentUrl = vi.fn(() => '/saved-image');
  api.downloadAttachment = vi.fn().mockRejectedValue(new Error('副本文件读取失败'));
  wrapper = mount(LibraryPage); await flushPromises();
  await wrapper.find('.item').trigger('click');
  expect(wrapper.text()).toContain('附件副本');
  expect(wrapper.text()).toContain('可能为缩略图，不保证原图');
  expect(wrapper.text()).toContain('附件文件请单独下载');
  await wrapper.findAll('button').find(button => button.text() === '预览附件副本').trigger('click');
  expect(wrapper.find('.saved-attachment img').attributes('src')).toBe('/saved-image');
  await wrapper.findAll('button').find(button => button.text() === '下载附件副本').trigger('click'); await flushPromises();
  expect(api.downloadAttachment).toHaveBeenCalledWith('a', 'item');
  expect(wrapper.text()).toContain('副本文件读取失败');
});

it.each([
  ['local_ocr', '本地 OCR 识别文字', 'paddleocr', 2],
  ['document_text', '本地文档提取文字', null, 0],
])('shows saved %s full text and provenance while keeping the body read-only', async (method, label, engine, units) => {
  const record = { ...item, kind: 'extracted_text', title: '扫描件.pdf', content: `全文开头\n${'已识别文字'.repeat(300)}\n全文结尾`, report: null,
    source: { username: 'friend', anchor: 'stable', text: '扫描件.pdf', sender: '好友', create_time: 1, render_type: 'file' },
    extraction: { status: 'complete', complete: true, extraction_method: method, ocr_engine: engine, ocr_units: units,
      file_hash: 'b'.repeat(64), version: 2, cache_token: 'a'.repeat(64), updated: 1 } };
  api.listItems.mockResolvedValue([record]);
  api.updateItem.mockResolvedValue({ ...record, notes: '核对后备注' });
  api.exportItem = vi.fn().mockRejectedValue(new Error('导出文件写入失败'));
  wrapper = mount(LibraryPage); await flushPromises();
  expect(wrapper.find('.item').text()).toContain('识别/提取文字');
  await wrapper.find('.item').trigger('click');
  expect(wrapper.find('.detail h2').text()).toBe('识别/提取文字');
  expect(wrapper.find('.detail pre').text()).toBe(record.content);
  expect(wrapper.find('.extraction-summary').text()).toContain(label);
  expect(wrapper.find('.extraction-summary').text()).toContain('提取流程已完成');
  expect(wrapper.find('.extraction-source').text()).toBe('原消息：扫描件.pdf');
  if (engine) {
    expect(wrapper.find('.extraction-summary').text()).toContain('paddleocr');
    expect(wrapper.find('.extraction-summary').text()).toContain('识别 2 个图片部件');
  } else expect(wrapper.find('.extraction-summary').text()).not.toContain('OCR 引擎');
  expect(wrapper.findAll('textarea')).toHaveLength(1);
  expect(wrapper.findAll('button').some(button => button.text() === '编辑正文')).toBe(false);
  await wrapper.find('textarea').setValue('核对后备注');
  await wrapper.find('.detail form').trigger('submit'); await flushPromises();
  expect(api.updateItem).toHaveBeenCalledWith('a', 'item', { folder_id: 'folder', verified: false, title: '扫描件.pdf', notes: '核对后备注', tags: [] });
  state.privacy.value = true; await flushPromises();
  expect(wrapper.find('.detail pre').classes()).toContain('privacy-blur');
  expect(wrapper.find('.extraction-source').classes()).toContain('privacy-blur');
  for (const [text, format] of [['导出 Markdown', 'markdown'], ['导出 HTML', 'html']]) {
    const button = wrapper.findAll('button').find(button => button.text() === text);
    expect(button.element.closest('.privacy-blur')).toBeNull();
    await button.trigger('click'); await flushPromises();
    expect(api.exportItem).toHaveBeenLastCalledWith('a', 'item', format);
    expect(wrapper.find('[role=alert]').text()).toContain('导出文件写入失败');
  }
  await wrapper.findAll('button').find(button => button.text() === '打开原消息').trigger('click'); await flushPromises();
  expect(globalThis.navigateTo).toHaveBeenCalledWith('/chat');
});
it.each(['item', 'folder'])('removes the deleted %s from the interface while preserving the cleanup failure', async kind => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const failure = { data: { detail: { code: 'library_cleanup_failed', record_deleted: true, message: '记录已删除，但附件文件清理失败' } } };
  api.deleteItem = vi.fn().mockRejectedValue(failure);
  api.deleteFolder = vi.fn().mockRejectedValue(failure);
  wrapper = mount(LibraryPage); await flushPromises();
  await wrapper.findAll('.folder').find(button => button.text() === '资料').trigger('click');
  await wrapper.find('.item').trigger('click');
  await wrapper.findAll('button').find(button => button.text() === (kind === 'item' ? '删除副本' : '删除资料夹')).trigger('click'); await flushPromises();
  expect(wrapper.find('.item').exists()).toBe(false);
  expect(wrapper.find('.detail form').exists()).toBe(false);
  expect(wrapper.text()).toContain('记录已删除，但附件文件清理失败');
  if (kind === 'folder') expect(wrapper.findAll('.folder')).toHaveLength(1);
});

it.each(['report', 'message', 'image', 'video'])('privacy mode covers saved %s content without obscuring its operations', async kind => {
  const record = { ...item, notes: '私人备注', tags: ['私人标签'], source: { username: 'friend', anchor: 'stable' } };
  if (kind === 'message') Object.assign(record, { kind: 'message', report: null });
  if (['image', 'video'].includes(kind)) Object.assign(record, { kind: 'attachment', report: null,
    attachment: { name: `私人附件.${kind === 'image' ? 'png' : 'mp4'}`, kind, size: 42 } });
  api.listItems.mockResolvedValue([record]);
  api.attachmentUrl = vi.fn(() => '/saved-media');
  wrapper = mount(LibraryPage); await flushPromises();
  await wrapper.find('.item').trigger('click');
  if (['image', 'video'].includes(kind)) await wrapper.findAll('button').find(button => button.text() === '预览附件副本').trigger('click');
  const sensitive = ['.item strong', '.item small:last-child', '.detail input:not([type="checkbox"])', '.detail textarea',
    ...(kind === 'report' ? ['.detail form .agent-answer', '.detail details .agent-answer']
      : ['.detail pre', ...(kind === 'message' ? [] : ['.saved-attachment strong', `.saved-attachment ${kind === 'image' ? 'img' : 'video'}`])])];
  const sensitiveElements = () => {
    const folder = wrapper.findAll('.folder').at(-1).element;
    const citation = kind === 'report' ? wrapper.findAll('button').find(button => button.text().includes('原文 1')).element : null;
    return [...sensitive.map(selector => wrapper.find(selector).element), folder.querySelector('span') || folder,
      ...(citation ? [citation.querySelector('span') || citation] : [])];
  };
  for (const element of sensitiveElements()) expect(element.closest('.privacy-blur')).toBeNull();
  state.privacy.value = true; await flushPromises();
  for (const element of sensitiveElements()) expect(element.closest('.privacy-blur'), element.outerHTML).not.toBeNull();
  for (const control of [wrapper.find('.folder'), wrapper.find('.detail-back'), wrapper.findAll('button').find(button => button.text() === '导出 Markdown')]) {
    expect(control.element.closest('.privacy-blur')).toBeNull();
    expect(control.attributes('disabled')).toBeUndefined();
  }
  state.privacy.value = false; await flushPromises();
  for (const element of sensitiveElements()) expect(element.closest('.privacy-blur')).toBeNull();
});
