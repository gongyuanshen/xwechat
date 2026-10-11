import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, reactive, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import MessageInputWorkspace from '../components/chat/MessageInputWorkspace.vue'

vi.mock('../components/chat/AutoReplyControl.vue', () => ({ default: { template: '<span />' } }))
import { useApi } from '../composables/useApi'

const file = { canceled: false, path: 'C:\\资料\\报告.pdf', name: '报告.pdf', sizeBytes: 2048, kind: 'file' }
const image = { canceled: false, path: 'C:\\资料\\照片.JPG', name: '照片.JPG', sizeBytes: 4096, kind: 'image', previewDataUrl: 'data:image/png;base64,aW1hZ2U=' }

const picked = (...attachments) => ({ canceled: false, attachments })

describe('单个文件附件发送', () => {
  let state
  let api
  let wrapper

  beforeEach(() => {
    vi.stubGlobal('ref', ref)
    vi.stubGlobal('computed', computed)
    vi.stubGlobal('useState', () => ref({ selected: {}, drafts: {}, pinned: {} }))
    state = reactive({ selectedAccount: 'wx_account', selectedContact: { username: 'wx_peer', name: '甲' }, refreshSelectedMessages: vi.fn() })
    api = {
      sendChatFile: vi.fn().mockResolvedValue({ success: true }),
      sendChatImage: vi.fn().mockResolvedValue({ success: true, session: '甲', image_name: image.name,
        image_format: 'JPEG', image_size_bytes: 4096, duration_ms: 10, timestamp: 1 }),
      sendChatMessage: vi.fn().mockResolvedValue({ success: true })
    }
    window.wechatDesktop = { platform: 'win32', chooseFile: vi.fn().mockResolvedValue(picked(file)), chooseImage: vi.fn().mockResolvedValue(picked(image)) }
    wrapper = mount(MessageInputWorkspace, { props: { state, api } })
  })

  afterEach(() => {
    wrapper.unmount()
    delete window.wechatDesktop
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  const pick = async () => {
    await wrapper.get('.chat-input-btn-file').trigger('click')
    await flushPromises()
  }
  const send = async () => {
    await wrapper.get('.chat-input-btn-send-attachments').trigger('click')
    await flushPromises()
  }

  it('显示文件名、大小、会话，独立发送不清空文字', async () => {
    await wrapper.get('textarea').setValue('正文保留')
    await pick()
    expect(wrapper.get('.chat-input-body').text()).toContain('报告.pdf')
    expect(wrapper.get('.chat-input-attachment-size').text()).toBe('2.00 KB')
    expect(wrapper.get('.chat-input-attachment').text()).toContain('发送给 甲')
    await send()
    expect(api.sendChatFile).toHaveBeenCalledExactlyOnceWith({ account: 'wx_account', username: 'wx_peer', display_name: '甲', file_path: file.path })
    expect(api.sendChatImage).not.toHaveBeenCalled()
    expect(api.sendChatMessage).not.toHaveBeenCalled()
    expect(wrapper.find('.chat-input-attachment').exists()).toBe(false)
    expect(wrapper.get('textarea').element.value).toBe('正文保留')
    expect(state.refreshSelectedMessages).toHaveBeenCalledOnce()
  })

  it.each([[0, '0 B'], [1024 ** 2, '1.00 MB'], [1024 ** 3, '1024.00 MB']])('正确展示 %s 字节文件', async (sizeBytes, expected) => {
    window.wechatDesktop.chooseFile.mockResolvedValueOnce(picked({ ...file, sizeBytes }))
    await pick()
    expect(wrapper.get('.chat-input-attachment-size').text()).toBe(expected)
  })

  it('图片和文件共存，取消和选择错误保留全部旧附件', async () => {
    await wrapper.get('.chat-input-btn-image').trigger('click')
    await flushPromises()
    await pick()
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(true)
    expect(wrapper.findAll('.chat-input-attachment')).toHaveLength(2)
    window.wechatDesktop.chooseFile.mockResolvedValueOnce({ canceled: true })
    await pick()
    window.wechatDesktop.chooseFile.mockRejectedValueOnce(new Error('无法读取磁盘'))
    await pick()
    expect(wrapper.get('.chat-input-body').text()).toContain('报告.pdf')
    expect(wrapper.get('[role="alert"]').text()).toContain('无法读取磁盘')
    await wrapper.get('.chat-input-file-remove').trigger('click')
    expect(wrapper.findAll('.chat-input-attachment')).toHaveLength(1)
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(true)
    expect(api.sendChatFile).not.toHaveBeenCalled()
  })

  it('文件入口选图通过图片接口发送，不伪装为文件', async () => {
    window.wechatDesktop.chooseFile.mockResolvedValueOnce(picked(image))
    await pick()
    expect(wrapper.get('.chat-input-image-preview').attributes('src')).toBe(image.previewDataUrl)
    await wrapper.get('.chat-input-btn-send-attachments').trigger('click')
    await flushPromises()
    expect(wrapper.find('.chat-input-image-preview').exists()).toBe(false)
    expect(api.sendChatImage).toHaveBeenCalledExactlyOnceWith({
      account: 'wx_account', username: 'wx_peer', display_name: '甲', image_path: image.path
    })
    expect(api.sendChatFile).not.toHaveBeenCalled()
  })

  it('明确的发送前错误保留附件并显示原始错误', async () => {
    await pick()
    api.sendChatFile.mockRejectedValueOnce({ code: 'WECHAT_FILE_NOT_FOUND', message: '文件已被移走' })
    await send()
    expect(wrapper.get('[role="alert"]').text()).toContain('WECHAT_FILE_NOT_FOUND')
    expect(wrapper.get('[role="alert"]').text()).toContain('文件已被移走')
    expect(wrapper.get('.chat-input-body').text()).toContain('报告.pdf')
    expect(wrapper.get('.chat-input-btn-send-attachments').attributes('disabled')).toBeUndefined()
  })

  it.each([
    { code: 'WECHAT_SEND_UNCONFIRMED', message: '无法确认文件消息' },
    new Error('Failed to fetch'),
    { code: 'ECONNRESET', message: 'connection reset' }
  ])('未知结果保留文件并要求核对，关闭/替换附件不能绕过核对', async (error) => {
    await pick()
    api.sendChatFile.mockRejectedValueOnce(error)
    await send()
    expect(wrapper.get('[role="status"]').text()).toContain(error.message)
    for (const selector of ['.chat-input-btn-send-attachments', '.chat-input-btn-file', '.chat-input-btn-image', '.chat-input-file-remove']) {
      expect(wrapper.get(selector).attributes('disabled')).toBeDefined()
      await wrapper.get(selector).trigger('click')
    }
    expect(api.sendChatFile).toHaveBeenCalledOnce()
    await wrapper.get('.chat-input-file-confirm').trigger('click')
    expect(wrapper.find('.chat-input-attachment').exists()).toBe(false)
    expect(api.sendChatFile).toHaveBeenCalledOnce()
    expect(state.refreshSelectedMessages).toHaveBeenCalledOnce()
  })

  it.each([{ success: false }, {}])('缺少明确成功回执进入核对状态', async (receipt) => {
    await pick()
    api.sendChatFile.mockResolvedValueOnce(receipt)
    await send()
    expect(wrapper.get('[role="status"]').text()).toContain('WECHAT_SEND_UNCONFIRMED')
    expect(state.refreshSelectedMessages).not.toHaveBeenCalled()
  })

  it.each(['contact', 'account'])('切换 %s 后禁止发送或确认原附件', async (kind) => {
    await pick()
    if (kind === 'contact') state.selectedContact = { username: 'wx_other', name: '乙' }
    else state.selectedAccount = 'other_account'
    await flushPromises()
    expect(wrapper.get('.chat-input-btn-send-attachments').attributes('disabled')).toBeDefined()
    await send()
    expect(api.sendChatFile).not.toHaveBeenCalled()
    state.selectedAccount = 'wx_account'
    state.selectedContact = { username: 'wx_peer', name: '甲' }
    await flushPromises()
    api.sendChatFile.mockRejectedValueOnce({ code: 'WECHAT_SEND_UNCONFIRMED', message: '检查微信' })
    await send()
    state.selectedContact = { username: 'wx_other', name: '乙' }
    await flushPromises()
    expect(wrapper.get('.chat-input-file-confirm').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.chat-input-file-retry-ready').attributes('disabled')).toBeDefined()
    state.selectedContact = { username: 'wx_peer', name: '甲' }
    await flushPromises()
    await wrapper.get('.chat-input-file-retry-ready').trigger('click')
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(wrapper.get('.chat-input-btn-send-attachments').attributes('disabled')).toBeUndefined()
    expect(api.sendChatFile).toHaveBeenCalledOnce()
  })

  it('文件选择期间切换会话拒绝迟到结果', async () => {
    let resolve
    window.wechatDesktop.chooseFile.mockReturnValueOnce(new Promise(done => { resolve = done }))
    await wrapper.get('.chat-input-btn-file').trigger('click')
    state.selectedContact = { username: 'wx_other', name: '乙' }
    resolve(picked(file))
    await flushPromises()
    expect(wrapper.find('.chat-input-attachment').exists()).toBe(false)
    expect(wrapper.get('[role="alert"]').text()).toContain('FILE_TARGET_CHANGED')
  })

  it('文件请求和文字共享单飞锁，完成后不刷新新会话', async () => {
    await pick()
    await wrapper.get('textarea').setValue('正文')
    let resolve
    api.sendChatFile.mockReturnValueOnce(new Promise(done => { resolve = done }))
    await wrapper.get('.chat-input-btn-send-attachments').trigger('click')
    await wrapper.get('.chat-input-btn-send-attachments').trigger('click')
    await wrapper.get('textarea').trigger('keydown.enter.exact')
    expect(api.sendChatFile).toHaveBeenCalledOnce()
    expect(api.sendChatMessage).not.toHaveBeenCalled()
    state.selectedContact = { username: 'wx_other', name: '乙' }
    resolve({ success: true })
    await flushPromises()
    expect(state.refreshSelectedMessages).not.toHaveBeenCalled()
  })

  it('发送文字保留文件且不自动混发', async () => {
    await pick()
    await wrapper.get('textarea').setValue('正文')
    await wrapper.get('.chat-input-btn-send').trigger('click')
    await flushPromises()
    expect(api.sendChatMessage).toHaveBeenCalledOnce()
    expect(api.sendChatFile).not.toHaveBeenCalled()
    expect(wrapper.find('.chat-input-file-preview').exists()).toBe(true)
  })

  it.each([undefined, { platform: 'linux', chooseFile: vi.fn() }])('无 Windows 桌面桥接时明确提示支持范围', async (desktop) => {
    window.wechatDesktop = desktop
    await pick()
    expect(wrapper.get('[role="alert"]').text()).toContain('FILE_SEND_UNSUPPORTED')
    expect(api.sendChatFile).not.toHaveBeenCalled()
  })

  it('文件 API 只传目标和路径并显式禁用重试', async () => {
    setActivePinia(createPinia())
    vi.stubGlobal('useApiBase', () => 'http://127.0.0.1:10392/api')
    const fetch = vi.fn().mockResolvedValue({ success: true })
    vi.stubGlobal('$fetch', fetch)
    await useApi().sendChatFile({ account: 'a', username: 'u', file_path: file.path, content: '不发送正文', sizeBytes: 2048 })
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/chat/send/file', expect.objectContaining({
      method: 'POST', retry: 0, body: { account: 'a', username: 'u', display_name: null, file_path: file.path }
    }))
  })
})
