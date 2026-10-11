import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ref } from "vue";
import LibrarySaveDialog from "../components/library/LibrarySaveDialog.vue";
const { useLibraryApi } = await vi.importActual("../composables/useLibraryApi");
const state = vi.hoisted(() => ({ privacy: null }));
vi.mock("~/stores/privacy", () => ({ usePrivacyStore: () => ({ privacyMode: state.privacy }) }));
vi.mock("pinia", () => ({ storeToRefs: store => store }));
let api, wrapper;
beforeEach(() => {
  state.privacy = ref(false);
  api = {
    listFolders: vi.fn(async () => [{ id: "folder", name: "资料" }]),
    createFolder: vi.fn(),
    createItem: vi.fn(),
  };
  vi.stubGlobal("useLibraryApi", () => api);
});
afterEach(() => {
  wrapper?.unmount();
  vi.unstubAllGlobals();
});
it("failure surfaces and run id is authoritative", async () => {
  api.createItem.mockRejectedValue(new Error("写入失败"));
  wrapper = mount(LibrarySaveDialog, {
    props: {
      open: true,
      account: "a",
      item: { kind: "report", run_id: "run-exact" },
    },
  });
  await flushPromises();
  await wrapper.find("select").setValue("folder");
  await wrapper.find("form").trigger("submit");
  await flushPromises();
  expect(api.createItem).toHaveBeenCalledWith(
    "a",
    expect.objectContaining({ run_id: "run-exact", folder_id: "folder" }),
  );
  expect(wrapper.text()).toContain("写入失败");
  expect(wrapper.emitted("saved")).toBeUndefined();
  api.createItem.mockResolvedValue({ id: "saved" });
  await wrapper.find("form").trigger("submit");
  await flushPromises();
  expect(wrapper.emitted("saved")[0]).toEqual([{ id: "saved" }]);
});
it("account switch discards old folder response", async () => {
  let resolveOld;
  api.listFolders
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValueOnce([{ id: "new", name: "新账号文件夹" }]);
  wrapper = mount(LibrarySaveDialog, {
    props: {
      open: true,
      account: "old",
      item: { kind: "message", source: { anchor: "x" } },
    },
  });
  await wrapper.setProps({ account: "new" });
  await flushPromises();
  resolveOld([{ id: "old", name: "旧账号文件夹" }]);
  await flushPromises();
  expect(wrapper.text()).toContain("新账号文件夹");
  expect(wrapper.text()).not.toContain("旧账号文件夹");
});
it("account switch suppresses stale save success", async () => {
  let resolveSave;
  api.createItem.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveSave = resolve;
      }),
  );
  wrapper = mount(LibrarySaveDialog, {
    props: {
      open: true,
      account: "old",
      item: { kind: "report", run_id: "x" },
    },
  });
  await flushPromises();
  await wrapper.find("select").setValue("folder");
  await wrapper.find("form").trigger("submit");
  await wrapper.setProps({ account: "new" });
  resolveSave({ id: "old-item" });
  await flushPromises();
  expect(wrapper.emitted("saved")).toBeUndefined();
});
it("API preserves persistence failure and account", async () => {
  vi.stubGlobal("useApiBase", () => "/api");
  const fetch = vi.fn().mockRejectedValue(new Error("disk full"));
  vi.stubGlobal("$fetch", fetch);
  await expect(
    useLibraryApi().updateItem("a", "id", { notes: "note" }),
  ).rejects.toThrow("disk full");
  expect(fetch).toHaveBeenCalledWith("/api/library/items/id", {
    method: "PATCH",
    body: { notes: "note" },
    query: { account: "a" },
  });
});
it("saved searches use the agreed plural API and encode record ids", async () => {
  vi.stubGlobal("useApiBase", () => "/api");
  const fetch = vi.fn().mockResolvedValue([]);
  vi.stubGlobal("$fetch", fetch);
  const library = useLibraryApi();
  await library.listSearches("account");
  expect(fetch).toHaveBeenCalledWith("/api/library/searches", {
    query: { account: "account" },
  });
  await library.deleteSearch("account", "id/other");
  expect(fetch).toHaveBeenCalledWith("/api/library/searches/id%2Fother", {
    method: "DELETE",
    query: { account: "account" },
  });
});

vi.mock("~/composables/useLibraryApi", () => ({
  useLibraryApi: () => globalThis.useLibraryApi(),
}));

it("loading dialog owns focus and Escape closes; disabled fieldsets stay outside tab cycle", async () => {
  const opener = document.createElement("button");
  document.body.append(opener);
  opener.focus();
  let resolveFolders;
  api.listFolders.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveFolders = resolve;
      }),
  );
  wrapper = mount(LibrarySaveDialog, {
    attachTo: document.body,
    props: {
      open: true,
      account: "a",
      item: { kind: "report", run_id: "run" },
    },
  });
  await flushPromises();
  expect(wrapper.element.contains(document.activeElement)).toBe(true);
  expect(document.activeElement.matches(":disabled")).toBe(false);
  document.activeElement.dispatchEvent(
    new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
  );
  expect(wrapper.emitted("close")).toHaveLength(1);
  resolveFolders([{ id: "folder", name: "资料" }]);
  await flushPromises();
  await wrapper.find("select").setValue("folder");
  let resolveSave;
  api.createItem.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveSave = resolve;
      }),
  );
  await wrapper.find("form").trigger("submit");
  const cancel = wrapper.find("footer button");
  cancel.element.focus();
  const tab = new KeyboardEvent("keydown", {
    key: "Tab",
    shiftKey: true,
    bubbles: true,
    cancelable: true,
  });
  cancel.element.dispatchEvent(tab);
  expect(tab.defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(cancel.element);
  resolveSave({ id: "saved" });
  await flushPromises();
  wrapper.unmount();
  wrapper = null;
  expect(document.activeElement).toBe(opener);
  opener.remove();
});
it('attachment save explains preservation before submitting and does not fake a missing source success', async () => {
  const missingMessage = '图片未缓存在本地，请先在电脑版微信中打开并下载，再重试。';
  api.createItem.mockRejectedValue({ data: { detail: missingMessage } });
  wrapper = mount(LibrarySaveDialog, { props: { open: true, account: 'a', item: { kind: 'attachment', source: { username: 'room', anchor: 'stable' } } } });
  await flushPromises();
  expect(wrapper.text()).toContain('可能是缩略图，不保证原图');
  const title = wrapper.findAll('label').find(label => label.text() === '标题').find('input');
  const tags = wrapper.findAll('label').find(label => label.text() === '标签').find('input');
  await title.setValue('保留的标题');
  await wrapper.find('textarea').setValue('保留的备注');
  await tags.setValue('项目, 图片');
  await wrapper.find('select').setValue('folder'); await wrapper.find('form').trigger('submit'); await flushPromises();
  expect(api.createItem).toHaveBeenCalledWith('a', expect.objectContaining({ kind: 'attachment', source: { username: 'room', anchor: 'stable' } }));
  expect(wrapper.find('[role=alert]').text()).toBe(missingMessage);
  expect(wrapper.find('select').element.value).toBe('folder');
  expect(title.element.value).toBe('保留的标题');
  expect(wrapper.find('textarea').element.value).toBe('保留的备注');
  expect(tags.element.value).toBe('项目, 图片');
  expect(wrapper.emitted('saved')).toBeUndefined();
});

it('saves extracted text by cache identity and retains the form when the cached result changed', async () => {
  api.createItem.mockRejectedValue({ data: { detail: '提取结果已变化，请重新查看全文后保存。' } });
  wrapper = mount(LibrarySaveDialog, { props: { open: true, account: 'a', item: {
    kind: 'extracted_text', title: '扫描件.pdf', source: { username: 'friend', anchor: 'stable' }, cache_token: 'a'.repeat(64),
  } } });
  await flushPromises();
  expect(wrapper.text()).toContain('保存已识别或提取的全文与出处');
  expect(wrapper.text()).toContain('不包含附件文件');
  expect(wrapper.find('button[type="submit"]').text()).toBe('保存文字');
  const title = wrapper.findAll('label').find(label => label.text() === '标题').find('input');
  await title.setValue('保留的全文标题');
  await wrapper.find('textarea').setValue('需要人工核对');
  await wrapper.find('select').setValue('folder');
  await wrapper.find('form').trigger('submit'); await flushPromises();
  expect(api.createItem).toHaveBeenCalledWith('a', {
    kind: 'extracted_text', source: { username: 'friend', anchor: 'stable' }, cache_token: 'a'.repeat(64),
    folder_id: 'folder', title: '保留的全文标题', notes: '需要人工核对', tags: [],
  });
  expect(wrapper.find('[role=alert]').text()).toBe('提取结果已变化，请重新查看全文后保存。');
  expect(wrapper.find('select').element.value).toBe('folder');
  expect(title.element.value).toBe('保留的全文标题');
  expect(wrapper.find('textarea').element.value).toBe('需要人工核对');
  expect(wrapper.emitted('saved')).toBeUndefined();
});

it('privacy mode covers save fields while keeping save and cancel available', async () => {
  wrapper = mount(LibrarySaveDialog, { props: { open: true, account: 'a', item: { kind: 'report', run_id: 'run' } } });
  await flushPromises();
  await wrapper.find('select').setValue('folder');
  const fields = wrapper.findAll('input, select, textarea');
  for (const field of fields) expect(field.element.closest('.privacy-blur')).toBeNull();
  state.privacy.value = true; await flushPromises();
  for (const field of fields) expect(field.element.closest('.privacy-blur')).not.toBeNull();
  for (const button of wrapper.findAll('footer button')) {
    expect(button.element.closest('.privacy-blur')).toBeNull();
    expect(button.attributes('disabled')).toBeUndefined();
  }
  state.privacy.value = false; await flushPromises();
  for (const field of fields) expect(field.element.closest('.privacy-blur')).toBeNull();
});
