import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive, ref } from 'vue'
import MessageInputWorkspace from '../components/chat/MessageInputWorkspace.vue'

vi.mock('../components/chat/AutoReplyControl.vue', () => ({ default: { template: '<span />' } }))

const image = { kind: 'image', path: 'C:\\照片\\图.png', name: '图.png', sizeBytes: 512, previewDataUrl: 'data:image/png;base64,aW1hZ2U=' }
const file = { kind: 'file', path: 'C:\\资料\\报告.pdf', name: '报告.pdf', sizeBytes: 2048 }
const first = { kind: 'file', path: 'C:\\资料\\首项.txt', name: '首项.txt', sizeBytes: 1 }
const last = { kind: 'file', path: 'C:\\资料\\末项.txt', name: '末项.txt', sizeBytes: 0 }
const picked = (...attachments) => ({ canceled: false, attachments })
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

describe('混合附件列表与顺序发送', () => {
  let wrapper, state, api
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    vi.stubGlobal('useState', () => ref({ selected: {}, drafts: {}, pinned: {} }))
    state = reactive({ selectedAccount: 'account', selectedContact: { username: 'peer', name: '甲' }, refreshSelectedMessages: vi.fn() })
    api = {
      sendChatImage: vi.fn().mockResolvedValue({ success: true, session: '甲', image_name: image.name,
        image_format: 'PNG', image_size_bytes: 512, duration_ms: 10, timestamp: 1 }),
      sendChatFile: vi.fn().mockResolvedValue({ success: true }),
      sendChatMessage: vi.fn().mockResolvedValue({ success: true })
    }
    window.wechatDesktop = { platform: 'win32', chooseImage: vi.fn().mockResolvedValue(picked(image)), chooseFile: vi.fn().mockResolvedValue(picked(file, last)) }
    wrapper = mount(MessageInputWorkspace, { props: { state, api } })
  })
  afterEach(() => {
    wrapper.unmount()
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    delete window.wechatDesktop
    localStorage.clear()
  })
  const pick = async (kind) => {
    await wrapper.get(`.chat-input-btn-${kind}`).trigger('click')
    await flushPromises()
  }
  const send = async () => {
    await wrapper.get('.chat-input-btn-send-attachments').trigger('click')
    await flushPromises()
  }
  const names = () => wrapper.findAll('.chat-input-attachment-name').map(node => node.text())

  it('先选图片再选多个文件会追加，独立删除不清除其他项或发送', async () => {
    await pick('image')
    await pick('file')
    expect(names()).toEqual(['图.png', '报告.pdf', '末项.txt'])
    expect(wrapper.get('.chat-input-image-preview').attributes('src')).toBe(image.previewDataUrl)
    expect(wrapper.get('.chat-input-btn-send-attachments').text()).toContain('3')
    await wrapper.findAll('.chat-input-attachment-remove')[1].trigger('click')
    expect(names()).toEqual(['图.png', '末项.txt'])
    expect(api.sendChatFile).not.toHaveBeenCalled()
    expect(api.sendChatImage).not.toHaveBeenCalled()
  })

  it('取消、选择失败或旧版 IPC 返回值均不得覆盖已有附件', async () => {
    await pick('image')
    window.wechatDesktop.chooseFile.mockResolvedValueOnce({ canceled: true })
    await pick('file')
    window.wechatDesktop.chooseFile.mockRejectedValueOnce(new Error('读取第二个文件失败'))
    await pick('file')
    expect(names()).toEqual(['图.png'])
    expect(wrapper.get('[role="alert"]').text()).toContain('读取第二个文件失败')
    window.wechatDesktop.chooseFile.mockResolvedValueOnce({ canceled: false, ...file })
    await pick('file')
    expect(names()).toEqual(['图.png'])
    expect(wrapper.get('[role="alert"]').text()).toContain('ATTACHMENT_PICKER_INVALID')
  })

  it('同名或同路径条目有独立身份，移除只影响指定的一项', async () => {
    window.wechatDesktop.chooseFile.mockResolvedValueOnce(picked(file, { ...file, path: 'D:\\报告.pdf' }, file))
    await pick('file')
    await wrapper.findAll('.chat-input-attachment-remove')[1].trigger('click')
    expect(names()).toEqual(['报告.pdf', '报告.pdf'])
  })

  it('混合附件按列表次序分别调用图片和文件接口，成功项逐一移除', async () => {
    window.wechatDesktop.chooseFile.mockResolvedValueOnce(picked(image, file, last))
    await pick('file')
    expect(wrapper.get('.chat-input-btn-send-attachments').attributes('disabled')).toBeUndefined()
    await send()
    expect(names()).toEqual(['报告.pdf', '末项.txt'])
    expect(api.sendChatImage).toHaveBeenCalledExactlyOnceWith({
      account: 'account', username: 'peer', display_name: '甲', image_path: image.path
    })
    expect(api.sendChatFile).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(999)
    expect(api.sendChatFile).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    expect(api.sendChatFile).toHaveBeenCalledExactlyOnceWith({
      account: 'account', username: 'peer', display_name: '甲', file_path: file.path
    })
    expect(names()).toEqual(['末项.txt'])
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(api.sendChatFile).toHaveBeenCalledTimes(2)
    expect(api.sendChatFile).toHaveBeenLastCalledWith({
      account: 'account', username: 'peer', display_name: '甲', file_path: last.path
    })
    expect(names()).toEqual([])
  })

  it('图片结果未知时保留该图片及后续附件，已成功文件不重发', async () => {
    window.wechatDesktop.chooseFile.mockResolvedValueOnce(picked(first, image, last))
    await pick('file')
    api.sendChatImage.mockRejectedValueOnce({ code: 'WECHAT_SEND_UNCONFIRMED', message: '图片回执待核对' })
    await send()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(names()).toEqual(['图.png', '末项.txt'])
    expect(wrapper.get('.chat-input-image-pending').text()).toContain('图片回执待核对')
    await vi.advanceTimersByTimeAsync(10000)
    expect(api.sendChatFile).toHaveBeenCalledOnce()
    expect(api.sendChatImage).toHaveBeenCalledOnce()
    await wrapper.get('.chat-input-image-confirm').trigger('click')
    expect(names()).toEqual(['末项.txt'])
    expect(api.sendChatFile).toHaveBeenCalledOnce()
    await send()
    expect(api.sendChatFile).toHaveBeenCalledTimes(2)
    expect(api.sendChatImage).toHaveBeenCalledOnce()
    expect(names()).toEqual([])
  })

  it('文件逐项发送并等待冷却，文字不混发', async () => {
    await pick('file')
    await wrapper.get('textarea').setValue('保留文字')
    await send()
    expect(api.sendChatFile).toHaveBeenCalledExactlyOnceWith({ account: 'account', username: 'peer', display_name: '甲', file_path: file.path })
    expect(api.sendChatImage).not.toHaveBeenCalled()
    expect(names()).toEqual(['末项.txt'])
    await wrapper.get('.chat-input-btn-send-attachments').trigger('click')
    await wrapper.get('textarea').trigger('keydown.enter.exact')
    await vi.advanceTimersByTimeAsync(999)
    expect(api.sendChatFile).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    expect(api.sendChatFile).toHaveBeenLastCalledWith({ account: 'account', username: 'peer', display_name: '甲', file_path: last.path })
    expect(api.sendChatFile).toHaveBeenCalledTimes(2)
    expect(names()).toEqual([])
    expect(wrapper.get('textarea').element.value).toBe('保留文字')
    expect(api.sendChatMessage).not.toHaveBeenCalled()
  })

  it('第二项明确失败时保留第二项及后续项，继续发送不会重发已成功项', async () => {
    window.wechatDesktop.chooseFile.mockResolvedValueOnce(picked(first, file, last))
    await pick('file')
    api.sendChatFile.mockResolvedValueOnce({ success: true }).mockRejectedValueOnce({ code: 'WECHAT_FILE_NOT_FOUND', message: '文件不存在' })
    await send()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(names()).toEqual(['报告.pdf', '末项.txt'])
    expect(wrapper.get('[role="alert"]').text()).toContain('文件不存在')
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.sendChatFile).toHaveBeenCalledTimes(2)
    await send()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(api.sendChatImage).not.toHaveBeenCalled()
    expect(api.sendChatFile).toHaveBeenCalledTimes(4)
    expect(names()).toEqual([])
  })

  it.each([new Error('响应丢失'), { code: 'WECHAT_SEND_UNCONFIRMED', message: '未确认' }, { receipt: {} }])('不确定项必须人工核对，不自动发送后续项或重试', async (failure) => {
    window.wechatDesktop.chooseFile.mockResolvedValueOnce(picked(first, file, last))
    await pick('file')
    api.sendChatFile.mockResolvedValueOnce({ success: true })
    if (failure.receipt) api.sendChatFile.mockResolvedValueOnce(failure.receipt)
    else api.sendChatFile.mockRejectedValueOnce(failure)
    await send()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(names()).toEqual(['报告.pdf', '末项.txt'])
    expect(wrapper.get('[role="status"]').text()).toContain('报告.pdf')
    expect(wrapper.get('.chat-input-btn-send-attachments').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('.chat-input-attachment-remove')[0].attributes('disabled')).toBeDefined()
    await vi.advanceTimersByTimeAsync(10000)
    expect(api.sendChatFile).toHaveBeenCalledTimes(2)
    await wrapper.get('.chat-input-file-confirm').trigger('click')
    expect(names()).toEqual(['末项.txt'])
    expect(api.sendChatFile).toHaveBeenCalledTimes(2)
    await send()
    expect(api.sendChatFile).toHaveBeenCalledTimes(3)
    expect(api.sendChatImage).not.toHaveBeenCalled()
  })

  it('核对未发送只解锁当前项，不自动恢复整批发送', async () => {
    await pick('file')
    api.sendChatFile.mockRejectedValueOnce(new Error('断网'))
    await send()
    await wrapper.get('.chat-input-file-retry-ready').trigger('click')
    await vi.advanceTimersByTimeAsync(10000)
    expect(names()).toEqual(['报告.pdf', '末项.txt'])
    expect(api.sendChatFile).toHaveBeenCalledOnce()
    expect(wrapper.get('.chat-input-btn-send-attachments').attributes('disabled')).toBeUndefined()
  })

  it.each(['contact', 'account', 'roundtrip'])('冷却中切换 %s 后终止批次；切回也必须重新点击', async (kind) => {
    window.wechatDesktop.chooseFile.mockResolvedValueOnce(picked(first, file, last))
    await pick('file')
    await send()
    if (kind === 'account') state.selectedAccount = 'other-account'
    else state.selectedContact = { username: 'other', name: '乙' }
    if (kind === 'roundtrip') state.selectedContact = { username: 'peer', name: '甲' }
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(api.sendChatFile).toHaveBeenCalledOnce()
    expect(names()).toEqual(['报告.pdf', '末项.txt'])
    expect(wrapper.get('[role="alert"]').text()).toContain('会话')
    state.selectedAccount = 'account'
    state.selectedContact = { username: 'peer', name: '甲' }
    await flushPromises()
    expect(api.sendChatFile).toHaveBeenCalledOnce()
  })

  it('请求期间切换会话只完成已提交项，不发送下一项也不刷新新会话', async () => {
    window.wechatDesktop.chooseFile.mockResolvedValueOnce(picked(first, file, last))
    await pick('file')
    const pending = deferred()
    api.sendChatFile.mockReturnValueOnce(pending.promise)
    await send()
    state.selectedContact = { username: 'other', name: '乙' }
    pending.resolve({ success: true })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1000)
    expect(names()).toEqual(['报告.pdf', '末项.txt'])
    expect(api.sendChatFile).toHaveBeenCalledOnce()
    expect(state.refreshSelectedMessages).not.toHaveBeenCalled()
    expect(wrapper.get('.chat-input-btn-file').attributes('disabled')).toBeDefined()
  })

  it('选择期间切走再切回仍拒绝迟到结果并保留旧列表', async () => {
    await pick('image')
    const pending = deferred()
    window.wechatDesktop.chooseFile.mockReturnValueOnce(pending.promise)
    await wrapper.get('.chat-input-btn-file').trigger('click')
    state.selectedContact = { username: 'other', name: '乙' }
    state.selectedContact = { username: 'peer', name: '甲' }
    pending.resolve(picked(file))
    await flushPromises()
    expect(names()).toEqual(['图.png'])
    expect(wrapper.get('[role="alert"]').text()).toContain('TARGET_CHANGED')
  })

  it('组件卸载后不会继续发送队列', async () => {
    await pick('file')
    await send()
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(1000)
    expect(api.sendChatFile).toHaveBeenCalledOnce()
  })
})
