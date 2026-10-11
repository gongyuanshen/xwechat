import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import LocalSearchSettings from '../components/LocalSearchSettings.vue'

const account=ref('test-account'),request=vi.fn()
vi.mock('~/stores/chatAccounts',()=>({useChatAccountsStore:()=>({selectedAccount:account})}))
let wrapper,config,models,jobs,gpu,onEvent,indexStats,conversations,messageTotal
beforeEach(()=>{
  account.value='test-account'
  models=[{id:'bge-small-zh',name:'BGE Small 中文',description:'适合中文聊天',recommended:true,repo:'Xenova/bge-small-zh-v1.5',revision:'a'.repeat(40),license:'MIT',downloaded:false,size:95000000}]
  jobs=[]
  indexStats=undefined
  messageTotal=undefined
  conversations=[{username:'current',name:'当前聊天'},{username:'other',name:'其他聊天'}]
  gpu={supported:true,size:1000}
  config={enabled:false,model:null,usernames:[],days:90,start:null,end:null,device:'auto',device_id:0,auto_update:true,revision:1}
  request.mockReset().mockImplementation(async(path,options)=>{
    if(path.includes('/status'))return {config:{...config},models,device:{actual_device:null},jobs,gpu,index_stats:indexStats,message_total:messageTotal}
    if(path.includes('/conversations'))return conversations
    if(path.includes('/settings')){config={...options.body,revision:config.revision+1};return config}
    return []
  })
  onEvent=null
  vi.stubGlobal('useAiApi',()=>({request,localSearchEvents:(_account,callback)=>{onEvent=callback;return ()=>{}}}))
  vi.stubGlobal('useRoute',()=>({params:{username:'current'}}))
})
afterEach(()=>{wrapper?.unmount();vi.unstubAllGlobals();vi.useRealTimers()})
async function open(props={}){wrapper=mount(LocalSearchSettings,{props,attachTo:document.body,global:{stubs:{teleport:true}}});await flushPromises()}
const button=text=>wrapper.findAll('button').find(b=>b.text()===text)
const scopeButton=(category,text)=>wrapper.find(`[data-category="${category}"]`).findAll('button').find(b=>b.text()===text)
const primary=()=>wrapper.find('.lss-start button')
function ready(){models[0].downloaded=true;config.model=models[0].id;config.usernames=['current']}
async function selectScope(){await button('选择聊天').trigger('click');await flushPromises();await button('确认选择').trigger('click');await flushPromises()}

it('全账号入口不要求选择聊天，直接启用全部历史渐进索引',async()=>{
  models[0].downloaded=true;config.model=models[0].id
  await open({accountWide:true})
  expect(wrapper.text()).toContain('当前账号全部群聊和私聊')
  expect(button('选择聊天')).toBeUndefined()
  expect(wrapper.text()).not.toContain('不会自动扩大范围')
  expect(primary().attributes('disabled')).toBeUndefined()
  await primary().trigger('click');await flushPromises()
  const body=request.mock.calls.find(([path,options])=>path.includes('/settings') && options?.method==='PUT')[1].body
  expect(body).toMatchObject({agent_global:true,usernames:[],days:0,start:0,end:null,auto_update:true,enabled:true})
  expect(request.mock.calls.some(([path])=>path.includes('/index/build'))).toBe(true)
})
it('部分索引按实际时间段显示覆盖，不称全量完成',async()=>{
  config.active={partial:true,coverage:{recent:{username:'a',complete:true},history:{username:'a',complete:false}}}
  await open({accountWide:true})
  expect(wrapper.find('#lss-global-title').element.parentElement.parentElement.parentElement.textContent).toContain('部分覆盖 · 1 个聊天 · 1 个时间段已读完')
})
it('全账号旧任务必须先迁移范围，不能直接继续旧聊天筛选',async()=>{
  ready();config.enabled=true;jobs=[{id:'old',status:'paused',config:{revision:1}}]
  await open({accountWide:true})
  expect(primary().text()).toBe('保存并开始整理')
})
it('全账号超过 2000 个聊天时高级设置不回传目录，保留全局模式',async()=>{
  ready();config.enabled=true;config.agent_global=true;config.days=0;config.start=0
  config.usernames=Array.from({length:2001},(_,i)=>`chat-${i}`)
  const catalog=[...config.usernames],previous=request.getMockImplementation()
  request.mockImplementation(async(path,options)=>{
    if(path.includes('/settings')){
      if(options.body.usernames.length>2000)throw new Error('会话列表超过接口上限')
      config={...options.body,usernames:catalog,revision:config.revision+1}
      return config
    }
    return previous(path,options)
  })
  await open({accountWide:true})
  await button('保存高级设置').trigger('click');await flushPromises()
  const saved=request.mock.calls.find(([path,options])=>path.includes('/settings') && options?.method)[1].body
  expect(saved).toMatchObject({agent_global:true,usernames:[],enabled:true})
  expect(wrapper.text()).toContain('高级设置已保存')
  expect(config.usernames).toHaveLength(2001)
})
it('首次打开没有启用开关，不下载、不保存、不扫描，直接说明下一步',async()=>{
  await open()
  expect(wrapper.find('[role=switch]').exists()).toBe(false)
  expect(request.mock.calls.every(([,o])=>!o?.method)).toBe(true)
  expect(wrapper.find('#lss-start-hint').text()).toContain('请先下载并使用')
  expect(wrapper.find('.lss-advanced').attributes('open')).toBeUndefined()
})
it('主页面下载只创建下载任务，不选择或开启索引',async()=>{
  await open();await button('下载模型').trigger('click');await flushPromises()
  expect(request.mock.calls.some(([p])=>p.includes('/models/bge-small-zh/download'))).toBe(true)
  expect(request.mock.calls.some(([p])=>p.includes('/index/') || p.includes('/settings'))).toBe(false)
})
it('下载完成后明确选择，作为草稿等待一次提交',async()=>{
  models[0].downloaded=true
  await open();await button('使用此模型').trigger('click');await flushPromises()
  expect(wrapper.text()).toContain('已选择')
  expect(wrapper.find('#lss-start-hint').text()).toContain('请选择至少一个聊天')
  expect(request.mock.calls.some(([p])=>p.includes('/settings'))).toBe(false)
})
it('选择聊天预选当前会话，取消不写入草稿或后端',async()=>{
  await open();await button('选择聊天').trigger('click');await flushPromises()
  expect(wrapper.find('.lss-chat-list input').element.checked).toBe(true)
  await wrapper.find('[aria-label=关闭]').trigger('click')
  expect(wrapper.text()).toContain('还没有选择聊天')
  expect(request.mock.calls.some(([p])=>p.includes('/settings'))).toBe(false)
})
it('未开启的账号选好聊天即可一次开启并建立索引',async()=>{
  models[0].downloaded=true;config.model=models[0].id
  await open();await selectScope()
  expect(primary().text()).toBe('开启并开始整理')
  expect(primary().attributes('disabled')).toBeUndefined()
  await primary().trigger('click');await flushPromises()
  const writes=request.mock.calls.filter(([,o])=>o?.method)
  expect(writes.map(([p])=>p.split('?')[0])).toEqual(['/local-search/settings','/local-search/index/build'])
  expect(writes[0][1].body).toMatchObject({enabled:true,usernames:['current']})
})
it('未选择账号可下载模型，主按钮旁明确说明限制',async()=>{
  account.value=null;await open()
  expect(button('下载模型').attributes('disabled')).toBeUndefined()
  expect(primary().attributes('disabled')).toBeDefined()
  expect(wrapper.find('#lss-start-hint').text()).toContain('选择账号')
})
it('保存失败恢复未开启状态，并在主按钮旁展示错误',async()=>{
  ready();await open()
  const previous=request.getMockImplementation()
  request.mockImplementation((p,o)=>p.includes('/settings')?Promise.reject(new Error('保存失败，请重试')):previous(p,o))
  await primary().trigger('click');await flushPromises()
  expect(primary().text()).toBe('开启并开始整理')
  expect(wrapper.find('[role=alert]').text()).toContain('保存失败')
  expect(request.mock.calls.some(([p])=>p.includes('/index/build'))).toBe(false)
})
it('建立任务失败保留已保存配置，仍可重试',async()=>{
  ready();await open()
  const previous=request.getMockImplementation()
  request.mockImplementation((p,o)=>p.includes('/index/build')?Promise.reject(new Error('任务提交失败')):previous(p,o))
  await primary().trigger('click');await flushPromises()
  expect(primary().attributes('disabled')).toBeUndefined()
  expect(primary().text()).toBe('保存并开始整理')
  expect(wrapper.find('[role=alert]').text()).toContain('任务提交失败')
})
it('运行中展示计时和实际进度，禁止重复提交与更改范围',async()=>{
  ready();config.enabled=true;jobs=[{id:'run',status:'running',stage:'embedding',processed:100,embedded:8,started:Date.now()/1000-30}]
  await open()
  expect(wrapper.find('.lss-status').text()).toContain('已读取 100 条消息')
  expect(wrapper.find('.lss-status').text()).toContain('用时')
  expect(primary().attributes('disabled')).toBeDefined()
  expect(button('调整聊天').attributes('disabled')).toBeDefined()
  await button('暂停整理').trigger('click');await flushPromises()
  expect(request.mock.calls.some(([p])=>p.includes('/index/pause'))).toBe(true)
})
it('重复检查零新增时展示现有索引总数，说明复用',async()=>{
  ready();config.enabled=true;indexStats={messages:6601,chunks:601}
  jobs=[{id:'done',status:'done',processed:6601,embedded:0,started:1,finished:13}]
  await open()
  expect(wrapper.find('.lss-status-title').text()).toContain('搜索数据已是最新')
  expect(wrapper.find('[data-metric=indexed]').text()).toContain('601覆盖 6,601 条消息')
  expect(wrapper.find('.lss-status').text()).toContain('已复用现有搜索数据')
  expect(wrapper.find('[data-metric=generated]').text()).toBe('本次已保存片段0个片段')
})
it('没有可用片段时不能显示智能搜索已准备好',async()=>{
  ready();config.enabled=true;indexStats={messages:10,chunks:0}
  jobs=[{id:'empty',status:'done',processed:10,embedded:0,started:1,finished:13}]
  await open()
  expect(wrapper.find('.lss-status-title').text()).toContain('暂无可搜索内容')
  expect(wrapper.find('.lss-status').text()).not.toContain('可以智能搜索了')
  expect(wrapper.find('.lss-status').text()).toContain('请调整聊天或时间范围')
})
it('进度按任务时间段计算，实时读取量不冒充已保存进度',async()=>{
  ready();config.enabled=true
  jobs=[{id:'run',status:'running',stage:'reading',chat_index:1,segments:[{},{},{},{}],read_count:1680,processed:1000,embedded:80,embedded_count:92,started:1}]
  await open({accountWide:true})
  expect(wrapper.find('.lss-index-progress').attributes('value')).toBe('25')
  expect(wrapper.find('.lss-index-progress').attributes('aria-label')).toBe('会话时间段整理进度')
  expect(wrapper.find('[data-metric=read] strong').text()).toBe('1,680')
  expect(wrapper.find('[data-metric=saved] dd').text()).toBe('1,000条消息')
  expect(wrapper.find('[data-metric=generated] dd').text()).toBe('92个片段')
  expect(wrapper.find('[data-metric=indexed]').text()).toContain('等待索引统计')
  expect(wrapper.find('.lss-status-details').attributes('open')).toBeUndefined()
})
it('未知任务范围不使用当前表单推算百分比，空范围不回退到聊天总数',async()=>{
  ready();config.enabled=true
  jobs=[{id:'run',status:'queued',stage:'queued',started:1,updated:1}]
  await open()
  expect(wrapper.find('.lss-index-progress').attributes('value')).toBeUndefined()
  onEvent({kind:'local_search_index',account:'test-account',body:{...jobs[0],updated:2,segments:[],config:{usernames:['current']}}})
  await flushPromises()
  expect(wrapper.find('.lss-index-progress').attributes('value')).toBeUndefined()
})
it('首次零进度与完成进度明确区分',async()=>{
  ready();config.enabled=true
  jobs=[{id:'run',status:'running',stage:'reading',chat_index:0,segments:[{},{}],started:1,updated:1}]
  await open()
  expect(wrapper.find('.lss-index-progress').attributes('value')).toBe('0')
  jobs=[{...jobs[0],status:'done',chat_index:2,updated:2,finished:2}]
  onEvent({kind:'local_search_index',account:'test-account',body:jobs[0]})
  await flushPromises()
  expect(wrapper.find('.lss-index-progress').attributes('value')).toBe('100')
  expect(button('暂停整理')).toBeUndefined()
})
it('失败保留真实进度并直接显示错误，不藏进详情',async()=>{
  ready();config.enabled=true
  jobs=[{id:'fail',status:'error',stage:'embedding',chat_index:1,segments:[{},{}],processed:100,embedded:8,error:'模型运行中断',started:1,finished:2}]
  await open()
  expect(wrapper.find('.lss-index-progress').attributes('value')).toBe('50')
  expect(wrapper.find('.lss-status-title svg.lucide').exists()).toBe(true)
  expect(wrapper.find('.lss-status > [role=alert]').text()).toBe('模型运行中断')
  expect(wrapper.find('[data-metric=saved] dd').text()).toBe('100条消息')
})
it('暂停后的片段数量以事务保存结果为准，手动暂停不是错误',async()=>{
  ready();config.enabled=true;config.agent_global=true
  jobs=[{id:'paused',status:'paused',stage:'embedding',processed:1617,read_count:2117,
    embedded:244,embedded_count:308,error:'处理已暂停',started:1,finished:37,config:{revision:1}}]
  indexStats={messages:1617,chunks:244}
  await open({accountWide:true})
  expect(wrapper.find('[data-metric=generated]').text()).toBe('本次已保存片段244个片段')
  expect(wrapper.find('[data-metric=generated]').text()).not.toContain('308')
  expect(wrapper.find('.lss-status .lss-error').exists()).toBe(false)
  expect(wrapper.find('.lss-status').text()).toContain('已保存 1,617 条消息的进度')
  expect(primary().text()).toBe('继续整理')
})
it('大批量未保存时实时显示读取增长，旧事件不能倒退数量',async()=>{
  ready();config.enabled=true
  jobs=[{id:'run',status:'running',stage:'reading',processed:1000,read_count:1000,embedded:80,updated:1,started:1}]
  await open()
  const requests=request.mock.calls.length
  for(const [updated,read_count] of [[2,1250],[3,1680],[2,1100]]){
    onEvent({kind:'local_search_index',account:'test-account',body:{...jobs[0],updated,read_count}})
    await flushPromises()
  }
  expect(wrapper.find('[data-metric=read] strong').text()).toBe('1,680')
  expect(wrapper.find('.lss-status').text()).toContain('已保存 1,000 条消息的进度')
  expect(request.mock.calls).toHaveLength(requests)
})
it('轮询旧响应不会覆盖读取中的实时数量',async()=>{
  vi.useFakeTimers()
  ready();config.enabled=true
  jobs=[{id:'run',status:'running',stage:'reading',processed:0,read_count:0,updated:1,started:1}]
  await open()
  onEvent({kind:'local_search_index',account:'test-account',body:{...jobs[0],updated:3,read_count:700}})
  await vi.advanceTimersByTimeAsync(1500);await flushPromises()
  expect(wrapper.find('[data-metric=read] strong').text()).toBe('700')
})
it('高级设置可选择更大批量并保存，不提交聊天草稿',async()=>{
  ready();config.usernames=[];await open();await selectScope()
  const picker=wrapper.findAll('[role=combobox]').find(x=>x.attributes('aria-label')==='每批读取消息数')
  await picker.trigger('click')
  await wrapper.findAll('[role=option]').find(x=>x.text()==='2,000 条 · 大批量').trigger('click')
  await button('保存高级设置').trigger('click');await flushPromises()
  expect(request.mock.calls.find(([p])=>p.includes('/settings'))[1].body).toMatchObject({read_batch_size:2000,usernames:[]})
})
it('暂停且配置未变时直接继续，不保存配置或重建',async()=>{
  ready();config.enabled=true;jobs=[{id:'run',status:'paused',stage:'reading',config:{revision:1},started:1,updated:10}]
  await open()
  expect(primary().text()).toBe('继续整理')
  await primary().trigger('click');await flushPromises()
  expect(request.mock.calls.filter(([,o])=>o?.method).map(([p])=>p.split('?')[0])).toEqual(['/local-search/index/resume'])
})
it('暂停后修改范围则按新配置整理，不继续失效任务',async()=>{
  ready();config.enabled=true;jobs=[{id:'run',status:'paused',config:{revision:1},started:1,updated:10}]
  await open();await button('调整聊天').trigger('click');await flushPromises()
  await wrapper.findAll('.lss-chat-list input')[1].setValue(true);await button('确认选择').trigger('click')
  expect(primary().text()).toBe('保存并开始整理')
})
it('自定义日期未填写时解释原因，补全后允许开始',async()=>{
  ready();await open()
  await wrapper.find('[role=combobox]').trigger('click')
  await wrapper.findAll('[role=option]').find(x=>x.text()==='自定义时间').trigger('click')
  expect(primary().attributes('disabled')).toBeDefined()
  expect(wrapper.find('#lss-start-hint').text()).toContain('有效的开始和结束日期')
  const dates=wrapper.findAll('input[type=date]')
  await dates[0].setValue('2026-08-01');await dates[1].setValue('2026-08-31')
  expect(primary().attributes('disabled')).toBeUndefined()
})
it('模型管理使用二级弹窗并恢复焦点',async()=>{
  await open();button('更换模型').element.focus();await button('更换模型').trigger('click');await flushPromises()
  expect(wrapper.find('[role=dialog]').attributes('aria-label')).toBe('选择检索模型')
  expect(wrapper.find('.lss-models').exists()).toBe(true)
  await wrapper.find('[aria-label=关闭]').trigger('click');await flushPromises()
  expect(document.activeElement).toBe(button('更换模型').element)
})
it('切换账号清空草稿和提示，不把旧任务提交到新账号',async()=>{
  ready();await open()
  const previous=request.getMockImplementation()
  let finish
  request.mockImplementation((p,o)=>p.includes('/settings')?new Promise(resolve=>{finish=resolve}):previous(p,o))
  await primary().trigger('click')
  account.value='another';await flushPromises();finish({});await flushPromises()
  expect(request.mock.calls.some(([p])=>p.includes('/index/build'))).toBe(false)
})

it('高级设置保存批量偏好，不提交尚未确认的聊天草稿',async()=>{
  ready();config.usernames=[];await open();await selectScope()
  await button('保存高级设置').trigger('click');await flushPromises()
  const saved=request.mock.calls.find(([p])=>p.includes('/settings'))[1].body
  expect(saved).toMatchObject({usernames:[],device:'auto',enabled:false})
  expect(wrapper.text()).toContain('已选择 1 个聊天')
})

it('全选与清空只修改弹窗草稿，确认后才应用范围',async()=>{
  await open();await button('选择聊天').trigger('click');await flushPromises()
  await scopeButton('people','全选').trigger('click')
  expect(wrapper.findAll('.lss-chat-list input').every(x=>x.element.checked)).toBe(true)
  expect(scopeButton('people','全选').attributes('disabled')).toBeDefined()
  await scopeButton('people','清除').trigger('click')
  expect(wrapper.findAll('.lss-chat-list input').every(x=>!x.element.checked)).toBe(true)
  await scopeButton('people','全选').trigger('click');await button('取消').trigger('click')
  expect(wrapper.text()).toContain('还没有选择聊天')
  await button('选择聊天').trigger('click');await flushPromises()
  await scopeButton('people','全选').trigger('click');await button('确认选择').trigger('click')
  expect(wrapper.text()).toContain('已选择 2 个聊天')
  expect(request.mock.calls.some(([p])=>p.includes('/settings') || p.includes('/index/'))).toBe(false)
})

it('搜索结果全选保留之前的选择，无结果时禁用全选',async()=>{
  await open();await button('选择聊天').trigger('click');await flushPromises()
  const search=wrapper.find('[aria-label="搜索群聊或个人聊天"]')
  await search.setValue('其他');await scopeButton('people','全选结果').trigger('click')
  expect(wrapper.find('[role=dialog] footer').text()).toContain('已选择 2 个聊天')
  await search.setValue('不存在')
  expect(scopeButton('people','全选结果').attributes('disabled')).toBeDefined()
  await search.setValue('  ')
  expect(wrapper.findAll('.lss-chat-list input').every(x=>x.element.checked)).toBe(true)
})
it('从头整理在主操作区，保存当前选择后全量重建',async()=>{
  ready();config.enabled=true
  jobs=[{id:'run',status:'paused',config:{revision:1},started:1,updated:10}]
  vi.stubGlobal('confirm',vi.fn(()=>true))
  await open()
  expect(wrapper.find('.lss-start').text()).toContain('从头整理')
  await button('调整聊天').trigger('click');await flushPromises()
  await wrapper.findAll('.lss-chat-list input')[1].setValue(true)
  await button('确认选择').trigger('click')
  await button('从头整理').trigger('click');await flushPromises()
  const writes=request.mock.calls.filter(([,o])=>o?.method)
  expect(writes.map(([p])=>p.split('?')[0])).toEqual(['/local-search/settings','/local-search/index/rebuild'])
  expect(writes[0][1].body.usernames).toEqual(['current','other'])
})
it('取消从头整理不保存设置或启动任务',async()=>{
  ready();config.enabled=true;config.active={model:config.model,generation:'old'}
  vi.stubGlobal('confirm',vi.fn(()=>false))
  await open();await button('从头整理').trigger('click');await flushPromises()
  expect(request.mock.calls.some(([,o])=>o?.method)).toBe(false)
})
it('换模型明确提示全部向量重算，并提交新模型',async()=>{
  ready();config.enabled=true;config.active={model:config.model,generation:'old'}
  models.push({...models[0],id:'bge-base-zh',name:'BGE Base 中文',recommended:false})
  await open();await button('更换模型').trigger('click');await flushPromises()
  await button('使用此模型').trigger('click')
  expect(primary().text()).toBe('使用新模型从头整理')
  expect(wrapper.text()).toContain('全部内容都需要重新生成向量')
  await primary().trigger('click');await flushPromises()
  const writes=request.mock.calls.filter(([,o])=>o?.method)
  expect(writes[0][1].body.model).toBe('bge-base-zh')
  expect(writes[1][0]).toContain('/index/build')
})
it('展示增量与历史校对原因和已复用数量',async()=>{
  ready();config.enabled=true
  jobs=[{id:'run',status:'running',stage:'reading',mode:'incremental',unchanged:120,processed:120,started:1,updated:1}]
  await open()
  expect(wrapper.find('.lss-status').text()).toContain('增量整理：读取新增消息')
  expect(wrapper.find('.lss-status').text()).toContain('已复用 120 条未变化消息')
  onEvent({kind:'local_search_index',account:'test-account',body:{...jobs[0],mode:'reconcile',updated:2}})
  await flushPromises()
  expect(wrapper.find('.lss-status').text()).toContain('每日历史校对')
})

it('分类全选和清除独立，搜索清除保留隐藏结果，确认后提交正确范围',async()=>{
  ready()
  conversations=[...conversations,
    {username:'room@chatroom',name:'项目讨论',isGroup:true},
    {username:'legacy@chatroom',name:'周末活动'},
    {username:'explicit-group',name:'其他项目',isGroup:true},
    {username:'person',name:'名字里有群的个人',isGroup:false}]
  await open();await button('调整聊天').trigger('click');await flushPromises()
  const group=()=>wrapper.find('[data-category="groups"]')
  const people=()=>wrapper.find('[data-category="people"]')
  const checked=()=>wrapper.findAll('.lss-chat-list input').filter(x=>x.element.checked).map(x=>x.element.value)
  expect(group().findAll('input')).toHaveLength(3)
  expect(people().findAll('input')).toHaveLength(3)
  await scopeButton('groups','全选').trigger('click')
  expect(checked()).toEqual(['room@chatroom','legacy@chatroom','explicit-group','current'])
  expect(wrapper.find('.lss-scope-footer').text()).toContain('群聊 3 · 个人聊天 1')
  const search=wrapper.find('input[type=search]')
  await search.setValue('项目')
  await scopeButton('groups','清除').trigger('click')
  expect(scopeButton('people','清除').attributes('disabled')).toBeDefined()
  await search.setValue('')
  expect(checked()).toEqual(['legacy@chatroom','current'])
  await scopeButton('people','全选').trigger('click')
  await scopeButton('groups','清除').trigger('click')
  expect(checked()).toEqual(['current','other','person'])
  await button('确认选择').trigger('click')
  expect(request.mock.calls.some(([p])=>p.includes('/settings'))).toBe(false)
  await primary().trigger('click');await flushPromises()
  expect(request.mock.calls.find(([p])=>p.includes('/settings'))[1].body.usernames).toEqual(['current','other','person'])
})

it('账号切换关闭分类选择，重新打开不带入上个账号的草稿',async()=>{
  await open();await button('选择聊天').trigger('click');await flushPromises()
  await scopeButton('people','全选').trigger('click')
  account.value='another';conversations=[{username:'new',name:'新账号聊天',isGroup:false}]
  await flushPromises()
  expect(wrapper.find('.lss-scope-dialog').exists()).toBe(false)
  await button('选择聊天').trigger('click');await flushPromises()
  expect(wrapper.find('.lss-scope-footer').text()).toContain('已选择 0 个聊天')
  expect(wrapper.find('.lss-chat-list input').element.checked).toBe(false)
})


it('设备和组件操作统一打开本地加速，不调用旧硬件探测或组件接口',async()=>{
  await open()
  await button('管理本地加速').trigger('click')
  expect(wrapper.emitted('manage-acceleration')).toHaveLength(1)
  expect(request.mock.calls.some(([path])=>path.includes('/gpu/') || path.includes('/device/'))).toBe(false)
  expect(wrapper.text()).toContain('尚未运行')
})
it('模型下载期间禁止离线导入，暂停请求中禁止再次暂停和关闭弹窗',async()=>{
  models[0].job={status:'running',stage:'downloading'};await open()
  await button('更换模型').trigger('click');await flushPromises()
  const card=wrapper.find('.lss-model-actions')
  expect(card.findAll('button').find(b=>b.text()==='离线导入').attributes('disabled')).toBeDefined()
  const previous=request.getMockImplementation();let finish
  request.mockImplementation((p,o)=>p.includes('/models/') && p.endsWith('/pause') ? new Promise(resolve=>{finish=resolve}) : previous(p,o))
  const pause=card.findAll('button').find(b=>b.text()==='暂停')
  await pause.trigger('click');await pause.trigger('click')
  expect(wrapper.find('.lss-model-actions button').attributes('disabled')).toBeDefined()
  expect(wrapper.find('.lss-dialog-heading button').attributes('disabled')).toBeDefined()
  expect(request.mock.calls.filter(([p])=>p.includes('/models/') && p.endsWith('/pause'))).toHaveLength(1)
  models=[{...models[0],job:{status:'paused'}}];finish({ok:true});await flushPromises()
  expect(wrapper.find('.lss-model-actions').findAll('button').find(b=>b.text()==='离线导入').attributes('disabled')).toBeUndefined()
})


it('消息分母固定，进度条按已保存消息占总量计算',async()=>{
  ready();config.enabled=true
  jobs=[{id:'run',status:'running',stage:'reading',read_count:26286,processed:25000,chat_index:1,segments:[{},{}],started:1}]
  messageTotal={job_id:'run',status:'ready',value:58735,fixed:true}
  await open()
  expect(wrapper.find('[data-metric=read] strong').text()).toBe('26,286/ 58,735')
  expect(wrapper.find('[data-metric=read] dt').text()).toContain('本轮总量')
  expect(wrapper.find('.lss-index-progress').attributes('value')).toBe('42')
})
it.each([
  [{job_id:'old',status:'ready',value:100},'总量统计中'],
  [{job_id:'run',status:'counting'},'总量统计中'],
  [{job_id:'run',status:'unavailable'},'总量统计未完成'],
  [{job_id:'run',status:'ready',value:5,estimated:true},'总量统计中'],
])('缺失或失效总量不伪造消息分母：%j',async(total,hint)=>{
  ready();config.enabled=true
  jobs=[{id:'run',status:'running',stage:'reading',processed:10,started:1}]
  messageTotal=total
  await open()
  expect(wrapper.find('.lss-total-denominator').exists()).toBe(false)
  expect(wrapper.find('[data-metric=read]').text()).toContain(hint)
})
it('历史完成任务缺少固定清单时兼容实际读取数，包括零消息',async()=>{
  ready();config.enabled=true
  jobs=[{id:'done',status:'done',stage:'done',processed:0,started:1,finished:2}]
  messageTotal={job_id:'done',status:'ready',value:20,estimated:true}
  await open()
  expect(wrapper.find('[data-metric=read] strong').text()).toBe('0/ 0')
  expect(wrapper.find('[data-metric=read] dt').text()).toContain('本轮总量')
})


it('统计阶段不展示临时总量与假百分比',async()=>{
  ready();config.enabled=true
  jobs=[{id:'count',status:'running',stage:'counting',processed:0,started:1,segments:[{},{}]}]
  messageTotal={job_id:'count',status:'counting',value:10000}
  await open()
  expect(wrapper.find('.lss-status-title').text()).toBe('正在统计消息总量')
  expect(wrapper.find('.lss-index-progress').attributes('value')).toBeUndefined()
  expect(wrapper.find('.lss-total-denominator').exists()).toBe(false)
  expect(wrapper.find('.lss-status-footer').text()).toContain('统计完成后开始整理')
})
it('固定分母在暂停和完成时保持一致，不按结果重写',async()=>{
  ready();config.enabled=true
  jobs=[{id:'run',status:'paused',stage:'embedding',processed:100,started:1,updated:1}]
  messageTotal={job_id:'run',status:'ready',value:500,fixed:true}
  await open()
  expect(wrapper.find('.lss-total-denominator').text()).toBe('/ 500')
  jobs=[{...jobs[0],status:'done',processed:499,updated:2,finished:2}]
  onEvent({kind:'local_search_index',account:'test-account',body:jobs[0]})
  await flushPromises()
  // 即便旧接口给了不一致的完成数，界面也不偷偷降低分母或补成 100%。
  expect(wrapper.find('.lss-total-denominator').text()).toBe('/ 500')
  expect(wrapper.find('.lss-index-progress').attributes('value')).toBe('99')
})
