import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, reactive, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import MessageInputWorkspace from '../components/chat/MessageInputWorkspace.vue'

vi.mock('../components/chat/AutoReplyControl.vue', () => ({ default: { template: '<span />' } }))
import { useApi } from '../composables/useApi'

const image = {
  kind: 'image',
  sizeBytes: 512,
  path: 'C:\\照片\\测试.png',
  name: '测试.png',
  previewDataUrl: 'data:image/png;base64,aW1hZ2U='
}

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const picked = (...attachments) => ({ canceled: false, attachments })

describe('图片选择与独立发送', () => {
  let state
  let api
  let wrapper

  beforeEach(() => {
    vi.stubGlobal('ref', ref)
    vi.stubGlobal('computed', computed)
    vi.stubGlobal('useState', () => ref({ selected: {}, drafts: {}, pinned: {} }))
    state = reactive({
      selectedAccount: 'wx_account',
      selectedContact: { username: 'wx_peer', name: '甲' },
      refreshSelectedMessages: vi.fn()
    })
    api = {
      sendChatImage: vi.fn().mockResolvedValue({ success: true, session: '甲', image_name: image.name,
        image_format: 'PNG', image_size_bytes: 512, duration_ms: 10, timestamp: 1 }),
      sendChatMessage: vi.fn().mockResolvedValue({ success: true })
    }
    window.wechatDesktop = { platform: 'win32', chooseImage: vi.fn().mockResolvedValue(picked(image)) }
    wrapper = mount(MessageInputWorkspace, { props: { state, api } })
  })

  afterEach(() => {
    wrapper.unmount()
    delete window.wechatDesktop
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  const pick = async () => {
    expect(wrapper.find('.chat-input-btn-image').exists()).toBe(true)
    await wrapper.get('.chat-input-btn-image').trigger('click')
    await flushPromises()
  }

  const send = async () => {
    await wrapper.get('.chat-input-btn-send-attachments').trigger('click')
    await flushPromises()
  }

  it('选图只准备附件，点击后独立发送图片并保留文字', async () => {
    await wrapper.get('textarea').setValue('保留文字')
    await pick()
    expect(wrapper.get('.chat-input-image-preview').attributes('src')).toBe(image.previewDataUrl)
    expect(wrapper.get('.chat-input-attachments').text()).toContain('发送图片会短暂激活微信，随后自动核对本地发送记录。')
    expect(wrapper.get('.chat-input-btn-send-attachments').attributes('disabled')).toBeUndefined()
    expect(api.sendChatImage).not.toHaveBeenCalled()
    await send()
    expect(api.sendChatImage).toHaveBeenCalledExactlyOnceWith({
      account: 'wx_account', username: 'wx_peer', display_name: '甲', image_path: image.path
    })
    expect(api.sendChatMessage).not.toHaveBeenCalled()
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(false)
    expect(wrapper.get('textarea').element.value).toBe('保留文字')
    expect(state.refreshSelectedMessages).toHaveBeenCalledOnce()
  })

  it('明确发送前错误保留图片与文字并显示原始原因', async () => {
    await pick()
    await wrapper.get('textarea').setValue('草稿')
    api.sendChatImage.mockRejectedValueOnce({ code: 'WECHAT_IMAGE_NOT_FOUND', message: '图片已被移走' })
    await send()
    expect(wrapper.get('[role="alert"]').text()).toContain('图片已被移走')
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(true)
    expect(wrapper.get('textarea').element.value).toBe('草稿')
    expect(wrapper.get('.chat-input-btn-send-attachments').attributes('disabled')).toBeUndefined()
    expect(state.refreshSelectedMessages).not.toHaveBeenCalled()
  })

  it.each([
    { code: 'WECHAT_SEND_UNCONFIRMED', message: '微信中未取得图片发送回执' },
    new Error('响应中断')
  ])('未知结果保留图片并锁定重发，人工核对已发才移除', async (failure) => {
    await pick()
    api.sendChatImage.mockRejectedValueOnce(failure)
    await send()
    expect(wrapper.get('.chat-input-image-pending').text()).toContain(failure.message)
    for (const selector of ['.chat-input-btn-send-attachments', '.chat-input-btn-image', '.chat-input-image-remove']) {
      expect(wrapper.get(selector).attributes('disabled')).toBeDefined()
      await wrapper.get(selector).trigger('click')
    }
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(true)
    expect(api.sendChatImage).toHaveBeenCalledOnce()
    await wrapper.get('.chat-input-image-confirm').trigger('click')
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(false)
    expect(api.sendChatImage).toHaveBeenCalledOnce()
  })

  it('未知结果核对未发送后只解锁，必须再次点击才发送', async () => {
    await pick()
    api.sendChatImage.mockRejectedValueOnce({ code: 'WECHAT_SEND_UNCONFIRMED', message: '检查微信' })
    await send()
    await wrapper.get('.chat-input-image-retry-ready').trigger('click')
    await flushPromises()
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(true)
    expect(wrapper.get('.chat-input-btn-send-attachments').attributes('disabled')).toBeUndefined()
    expect(api.sendChatImage).toHaveBeenCalledOnce()
    await send()
    expect(api.sendChatImage).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(false)
  })

  it.each([{ success: false }, {}])('缺少明确成功回执时保留图片等待核对', async (receipt) => {
    await pick()
    api.sendChatImage.mockResolvedValueOnce(receipt)
    await send()
    expect(wrapper.get('.chat-input-image-pending').text()).toContain('WECHAT_SEND_UNCONFIRMED')
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(true)
    expect(state.refreshSelectedMessages).not.toHaveBeenCalled()
  })

  it('选择取消不丢失已有图片，移除不触发发送', async () => {
    await pick()
    window.wechatDesktop.chooseImage.mockResolvedValueOnce({ canceled: true })
    await pick()
    expect(wrapper.get('.chat-input-image-preview').attributes('src')).toBe(image.previewDataUrl)
    await wrapper.get('.chat-input-image-remove').trigger('click')
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(false)
    expect(api.sendChatImage).not.toHaveBeenCalled()
  })

  it('文字发送保留已选图片，图片不会随正文自动发送', async () => {
    await pick()
    await wrapper.get('textarea').setValue('只发文字')
    await wrapper.get('.chat-input-btn-send').trigger('click')
    await flushPromises()
    expect(api.sendChatMessage).toHaveBeenCalledOnce()
    expect(api.sendChatImage).not.toHaveBeenCalled()
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(true)
  })

  it.each(['contact', 'account'])('切换 %s 后不能把原图片发到新目标', async (kind) => {
    await pick()
    if (kind === 'contact') state.selectedContact = { username: 'wx_other', name: '乙' }
    else state.selectedAccount = 'wx_other_account'
    await flushPromises()
    expect(wrapper.get('.chat-input-btn-send-attachments').attributes('disabled')).toBeDefined()
    await wrapper.get('.chat-input-btn-send-attachments').trigger('click')
    expect(api.sendChatImage).not.toHaveBeenCalled()
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(true)
  })

  it('选择对话框返回前切换会话，迟到结果不会绑定新目标', async () => {
    const pending = deferred()
    window.wechatDesktop.chooseImage.mockReturnValueOnce(pending.promise)
    await wrapper.get('.chat-input-btn-image').trigger('click')
    state.selectedContact = { username: 'wx_other', name: '乙' }
    pending.resolve(picked(image))
    await flushPromises()
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(false)
    expect(wrapper.get('[role="alert"]').text()).toContain('IMAGE_TARGET_CHANGED')
    expect(api.sendChatImage).not.toHaveBeenCalled()
  })

  it('桥接缺失明确提示 Windows 桌面支持范围', async () => {
    delete window.wechatDesktop
    await pick()
    expect(wrapper.get('[role="alert"]').text()).toContain('Windows')
    expect(api.sendChatImage).not.toHaveBeenCalled()
  })

  it('原生选择出错保留已有选择并显示错误', async () => {
    await pick()
    window.wechatDesktop.chooseImage.mockRejectedValueOnce(new Error('磁盘读取失败'))
    await pick()
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(true)
    expect(wrapper.get('[role="alert"]').text()).toContain('磁盘读取失败')
  })

  it('useApi 使用独立图片 endpoint，只发送图片路径与明确目标', async () => {
    setActivePinia(createPinia())
    vi.stubGlobal('useApiBase', () => 'http://127.0.0.1:10392/api')
    const fetch = vi.fn().mockResolvedValue({ success: true })
    vi.stubGlobal('$fetch', fetch)
    const client = useApi()
    expect(typeof client.sendChatImage).toBe('function')
    await client.sendChatImage({
      account: 'wx_account', username: 'wx_peer', display_name: '甲', image_path: image.path,
      content: '不可自动混发', previewDataUrl: image.previewDataUrl
    })
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/chat/send/image', expect.objectContaining({
      method: 'POST', retry: 0, body: {
        account: 'wx_account', username: 'wx_peer', display_name: '甲', image_path: image.path
      }
    }))
  })
})
