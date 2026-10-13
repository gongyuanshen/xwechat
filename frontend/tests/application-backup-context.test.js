import { flushPromises, mount } from '@vue/test-utils';
import { reactive, ref } from 'vue';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ApplicationBackupSettings from '../components/ApplicationBackupSettings.vue';
import LibraryPage from '../pages/library.vue';
import { useApplicationAccountsStore } from '../stores/applicationAccounts';

const state = vi.hoisted(() => ({ chat:null, api:null }));
vi.mock('~/stores/chatAccounts', () => ({ useChatAccountsStore: () => state.chat }));
vi.mock('~/composables/useLibraryApi', () => ({ useLibraryApi: () => state.api }));
let library, backup;
const counts = { matters:0, folders:0, items:0, versions:0, searches:0, attachments:0, pending_reminders:0 };
beforeEach(() => {
  state.chat=reactive({selectedAccount:'a',accounts:['a','b']});
  state.api={listFolders:vi.fn(async()=>[{id:'folder',name:'资料'}]),listItems:vi.fn(async()=>[{id:'item',folder_id:'folder',kind:'report',title:'报告',content:'原始正文',notes:'',tags:[],verified:false,report:{answer:'原始正文',citations:[]}}]),listSearches:vi.fn(async()=>[])};
  for (const [name,value] of Object.entries({useApiBase:()=>'/api',useHead:vi.fn(),useState:()=>ref(null),useSettingsDialog:()=>({closeDialog:vi.fn()}),navigateTo:vi.fn(async()=>true)})) vi.stubGlobal(name,value);
  vi.stubGlobal('$fetch',vi.fn(async url=>url.endsWith('/accounts')?{accounts:['a','b'],chat_accounts:['a','b']}:{status:'restored',account:'b',added:counts,kept:counts,skipped:[],counts:{...counts,notes:0,reports:0},attachment_bytes:0,content_sha256:'a'.repeat(64),data_path:'D:\\output\\ai'}));
});
afterEach(()=>{library?.unmount();backup?.unmount();library=null;backup=null;vi.restoreAllMocks();vi.unstubAllGlobals();});

it('changing only the export account never changes the real application store or erases a library draft', async () => {
  library=mount(LibraryPage); await flushPromises();
  await library.get('.item').trigger('click'); await library.get('textarea').setValue('未保存的本机备注');
  backup=mount(ApplicationBackupSettings); await flushPromises();
  await backup.get('select').setValue('b'); await flushPromises();
  expect(useApplicationAccountsStore().selectedAccount).toBe('a');
  expect(library.get('textarea').element.value).toBe('未保存的本机备注');
  const input=backup.get('input[type="file"]'); Object.defineProperty(input.element,'files',{value:[new File(['zip'],'backup.zip')]}); await input.trigger('change');
  await backup.findAll('button').find(button=>button.text()==='恢复缺失资料，保留本机版本').trigger('click'); await flushPromises();
  const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
  await backup.findAll('button').find(button=>button.text()==='查看资料').trigger('click'); await flushPromises();
  expect(confirm).toHaveBeenCalledTimes(1); expect(useApplicationAccountsStore().selectedAccount).toBe('a');
  expect(library.get('textarea').element.value).toBe('未保存的本机备注'); expect(navigateTo).not.toHaveBeenCalled();
});

it('still invalidates an old export when a genuine chat selection changes the shared application context', async () => {
  backup=mount(ApplicationBackupSettings); await flushPromises();
  let finish; $fetch.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
  await backup.findAll('button').find(button=>button.text()==='下载资料备份').trigger('click');
  const signal=$fetch.mock.calls.at(-1)[1].signal;
  state.chat.selectedAccount='b'; await flushPromises(); finish(new Blob(['old'])); await flushPromises();
  expect(useApplicationAccountsStore().selectedAccount).toBe('b'); expect(signal.aborted).toBe(true); expect(click).not.toHaveBeenCalled();
});
