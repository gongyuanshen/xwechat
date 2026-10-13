import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { babelParse, parse } from '@vue/compiler-sfc'
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createAiNavigationConsumer } from '../utils/createAiNavigationConsumer'
import { createEmptySearchContext, useChatSearch } from '../composables/chat/useChatSearch'

// Execute the actual chat-page callback against the real search composable.
// Isolating this callback avoids mounting unrelated video/send/AI systems.
const script = parse(readFileSync(resolve('pages/chat/[[username]].vue'), 'utf8')).descriptor.scriptSetup.content
const declarations = babelParse(script, { sourceType: 'module' }).program.body.flatMap(node => node.declarations || [])
const consumerCall = declarations.find(node => node.id.name === 'consumeAiNavigation').init
const callbackNode = consumerCall.arguments[0].properties.find(node => node.key.name === 'locateSource').value
const createPageCallback = new Function('searchState', 'locateAiSource', `return (${script.slice(callbackNode.start, callbackNode.end)})`)
const target = () => ({ kind: 'source', origin: 'wrapped', account: 'account', username: 'friend', anchor: 'message_0:Msg_real:27' })
const setupConsumer = (locateSource = vi.fn(async () => true), initial = target()) => {
  const navigation = ref(initial), account = ref('account'), active = ref(true)
  const deps = {
    navigation, account, isActive: () => active.value,
    ensureLoaded: vi.fn(async () => {}), selectAccount: next => { account.value = next },
    waitForAccount: vi.fn(async () => {}), showSourceChat: vi.fn(async () => {}),
    openTask: vi.fn(), locateSource, diagnostic: vi.fn(), showError: vi.fn(),
  }
  return { ...deps, active, consume: createAiNavigationConsumer(deps) }
}

let wrapper, originalClient
beforeEach(() => {
  originalClient = process.client; process.client = true
  vi.spyOn(console, 'info').mockImplementation(() => {})
  vi.stubGlobal('useSettingsDialog', () => ({ openDialog: vi.fn() }))
  vi.stubGlobal('useApiBase', () => '/api')
})
afterEach(() => { wrapper?.unmount(); wrapper = null; process.client = originalClient; vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('年度总结跨页来源定位', () => {
  it('年度来源完成后清除目标且从不发送 AI 导航诊断', async () => {
    const state = setupConsumer()
    await state.consume()
    expect(state.navigation.value).toBeNull()
    expect(state.showSourceChat).toHaveBeenCalledOnce()
    expect(state.openTask).not.toHaveBeenCalled()
    expect(state.diagnostic).not.toHaveBeenCalled()
  })

  it.each([false, new Error('数据库读取失败')])('定位失败保留目标并指向年度总结重试：%s', async result => {
    const state = setupConsumer(async () => { if (result instanceof Error) throw result; return result })
    await state.consume()
    expect(state.navigation.value).toEqual(target())
    const message = state.showError.mock.calls[0][0]
    expect(message).toMatch(/未定位到来源消息|数据库读取失败/)
    expect(message).toContain('年度总结')
    expect(message).not.toContain('AI')
    expect(state.diagnostic).not.toHaveBeenCalled()
  })

  it('聊天页回调把真实三段锚点送入通用定位并显示年度来源标签', async () => {
    const message = { id: 'message_0:Msg_real:27', content: 'record' }
    const fetchContext = vi.fn(async () => ({ messages: [message], anchorId: message.id, anchorIndex: 0 }))
    const args = {
      api: { getChatMessagesAround: fetchContext }, contacts: ref([{ username: 'friend', name: '联系人' }]),
      selectedAccount: ref('account'), selectedContact: ref({ username: 'friend', name: '联系人' }), privacyMode: ref(false),
      allMessages: ref({ friend: [] }), messagesMeta: ref({ friend: { hasMore: true } }), messages: ref([]),
      messageContainerRef: ref(null), messagePageSize: 50, hasMoreMessages: ref(false), isLoadingMessages: ref(false),
      normalizeMessage: value => value, updateJumpToBottomState: vi.fn(), scrollToMessageId: vi.fn(async () => true),
      flashMessage: vi.fn(), highlightMessageId: ref(''), searchContext: ref(createEmptySearchContext()),
      selectContact: vi.fn(async () => {}), loadMoreMessages: vi.fn(),
    }
    let search
    wrapper = mount(defineComponent({ setup() { search = useChatSearch(args); return () => h('div') } }))
    const locateAi = vi.fn(async () => { throw new Error('不应进入 AI 来源定位') })
    const state = setupConsumer(createPageCallback(search, locateAi))
    await state.consume()
    expect(state.showError).not.toHaveBeenCalled()
    expect(locateAi).not.toHaveBeenCalled()
    expect(fetchContext).toHaveBeenCalledWith({ account: 'account', username: 'friend', anchor_id: 'message_0:Msg_real:27', before: 35, after: 35, source: 'auto', signal: undefined })
    expect(args.searchContext.value).toMatchObject({ active: true, kind: 'wrapped', label: '年度总结来源', anchorId: message.id })
    expect(args.allMessages.value.friend).toEqual([message])
    expect(args.flashMessage).toHaveBeenCalledWith(message.id)
    expect(state.diagnostic).not.toHaveBeenCalled()
  })

  it('原 AI 来源继续调用 AI 定位与原诊断', async () => {
    const locateAi = vi.fn(async () => true)
    const general = vi.fn(async () => true)
    const input = { ...target() }; delete input.origin
    const state = setupConsumer(createPageCallback({ locateByAnchorId: general }, locateAi), input)
    await state.consume()
    expect(locateAi).toHaveBeenCalledWith(input)
    expect(general).not.toHaveBeenCalled()
    expect(state.diagnostic.mock.calls.map(([event]) => event)).toEqual(['navigation.started', 'navigation.finished'])
  })

  it('统一搜索的真实聊天页回调保留锚点并使用统一搜索来源标签', async () => {
    const locateAi = vi.fn(async () => true)
    const general = vi.fn(async () => true)
    const input = { ...target(), origin: 'search' }
    const state = setupConsumer(createPageCallback({ locateByAnchorId: general }, locateAi), input)
    await state.consume()
    expect(general).toHaveBeenCalledWith({ targetUsername: 'friend', anchorId: 'message_0:Msg_real:27', kind: 'search', label: '搜索来源', throwOnError: true })
    expect(locateAi).not.toHaveBeenCalled()
    expect(state.navigation.value).toBeNull()
  })
})
