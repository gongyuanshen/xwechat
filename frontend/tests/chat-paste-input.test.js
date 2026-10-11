import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive, ref } from 'vue'
import MessageInputWorkspace from '../components/chat/MessageInputWorkspace.vue'

vi.mock('../components/chat/AutoReplyControl.vue', () => ({ default: { template: '<span />' } }))

const image = { kind: 'image', path: 'C:\\Temp\\截图.png', name: '截图.png', sizeBytes: 42, previewDataUrl: 'data:image/png;base64,aW1hZ2U=' }
const file = { kind: 'file', path: 'C:\\资料\\报告.pdf', name: '报告.pdf', sizeBytes: 100 }

describe('聊天输入区粘贴附件', () => {
  let wrapper, state, api, desktop
  beforeEach(() => {
    vi.stubGlobal('useState', () => ref({ selected: {}, drafts: {}, pinned: {} }))
    state = reactive({ selectedAccount: 'account', selectedContact: { username: 'peer', name: '甲' } })
    api = { sendChatImage: vi.fn(), sendChatFile: vi.fn(), sendChatMessage: vi.fn() }
    desktop = {
      platform: 'win32',
      chooseFile: vi.fn().mockResolvedValue({ canceled: false, attachments: [file] }),
      importChatAttachments: vi.fn().mockResolvedValue({ canceled: false, attachments: [image] })
    }
    window.wechatDesktop = desktop
    wrapper = mount(MessageInputWorkspace, { props: { state, api } })
  })
  afterEach(() => {
    wrapper.unmount()
    vi.unstubAllGlobals()
    delete window.wechatDesktop
    localStorage.clear()
  })
  const paste = (files = [], text = '') => {
    const event = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'clipboardData', { value: { files, getData: () => text } })
    wrapper.get('textarea').element.dispatchEvent(event)
    return event
  }
  const names = () => wrapper.findAll('.chat-input-attachment-name').map(node => node.text())

  it('截图粘贴追加预览，保留文字和已有附件，且不自动发送', async () => {
    await wrapper.get('.chat-input-btn-file').trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('保留草稿')
    const screenshot = new File(['image'], 'image.png', { type: 'image/png' })
    const event = paste([screenshot], '不应插入的图片 HTML 替代文字')
    await flushPromises()
    expect(event.defaultPrevented).toBe(true)
    expect(desktop.importChatAttachments).toHaveBeenCalledWith([screenshot])
    expect(names()).toEqual(['报告.pdf', '截图.png'])
    expect(wrapper.get('img').attributes('src')).toBe(image.previewDataUrl)
    expect(wrapper.get('textarea').element.value).toBe('保留草稿')
    for (const send of Object.values(api)) expect(send).not.toHaveBeenCalled()
  })

  it('一次粘贴多个混合附件按顺序追加，并能独立移除', async () => {
    desktop.importChatAttachments.mockResolvedValueOnce({ canceled: false, attachments: [image, file] })
    paste([new File(['image'], '图.png'), new File(['pdf'], '报告.pdf')])
    await flushPromises()
    expect(names()).toEqual(['截图.png', '报告.pdf'])
    await wrapper.get('.chat-input-image-remove').trigger('click')
    expect(names()).toEqual(['报告.pdf'])
  })

  it('文字、文件路径文本和空剪贴板保持浏览器默认粘贴，不访问桌面接口', async () => {
    for (const text of ['你好', 'C:\\资料\\报告.pdf', '']) expect(paste([], text).defaultPrevented).toBe(false)
    await flushPromises()
    expect(desktop.importChatAttachments).not.toHaveBeenCalled()
    expect(names()).toEqual([])
  })

  it('读取失败原样展示原因，已有附件和草稿保留', async () => {
    await wrapper.get('.chat-input-btn-file').trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('草稿')
    desktop.importChatAttachments.mockRejectedValueOnce(new Error('剪贴板文件已被删除'))
    paste([new File(['pdf'], '已删除.pdf')])
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('剪贴板文件已被删除')
    expect(names()).toEqual(['报告.pdf'])
    expect(wrapper.get('textarea').element.value).toBe('草稿')
    expect(wrapper.get('.chat-input-btn-file').attributes('disabled')).toBeUndefined()
  })

  it.each(['browser', 'old-desktop'])('缺少 %s 桌面粘贴接口时显示错误，不伪装成功', async (mode) => {
    if (mode === 'browser') delete window.wechatDesktop
    else delete desktop.importChatAttachments
    const event = paste([new File(['image'], 'image.png')])
    await flushPromises()
    expect(event.defaultPrevented).toBe(true)
    expect(wrapper.get('[role="alert"]').text()).toMatch(/桌面/)
    expect(names()).toEqual([])
  })

  it('异步导入期间锁定附件操作，切换会话后拒绝追加旧结果', async () => {
    let finish
    desktop.importChatAttachments.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    paste([new File(['image'], 'image.png')])
    await flushPromises()
    expect(wrapper.get('.chat-input-btn-file').attributes('disabled')).toBeDefined()
    paste([new File(['image'], 'second.png')])
    expect(desktop.importChatAttachments).toHaveBeenCalledTimes(1)
    state.selectedContact = { username: 'other', name: '乙' }
    finish({ canceled: false, attachments: [image] })
    await flushPromises()
    expect(names()).toEqual([])
    expect(wrapper.get('[role="alert"]').text()).toContain('TARGET_CHANGED')
  })

  it('附件属于原会话时禁止将新粘贴内容混入旧目标列表', async () => {
    await wrapper.get('.chat-input-btn-file').trigger('click')
    await flushPromises()
    state.selectedContact = { username: 'other', name: '乙' }
    const event = paste([new File(['image'], 'image.png')])
    await flushPromises()
    expect(event.defaultPrevented).toBe(true)
    expect(desktop.importChatAttachments).not.toHaveBeenCalled()
    expect(names()).toEqual(['报告.pdf'])
    expect(wrapper.get('[role="alert"]').text()).toContain('PASTE')
  })
})
