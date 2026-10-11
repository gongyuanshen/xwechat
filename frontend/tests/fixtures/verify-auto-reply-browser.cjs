// Actual Vue components in an isolated Chrome profile; fixture API only.
const { spawn } = require('node:child_process')
const fs = require('node:fs/promises')
const path = require('node:path')
const assert = require('node:assert/strict')
const { parseArgs } = require('node:util')
const { values } = parseArgs({ options: { browser: { type: 'string' }, output: { type: 'string' }, fixture: { type: 'string', default: 'http://127.0.0.1:3078/tests/fixtures/auto-reply.html' } } })
if (!values.browser || !values.output) throw new Error('Require --browser and --output')
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
async function main() {
  const output = path.resolve(values.output)
  await fs.mkdir(output, { recursive: true })
  const profile = await fs.mkdtemp(path.join(output, 'isolated-profile-'))
  const browser = spawn(values.browser, ['--headless', '--disable-gpu', '--no-first-run', '--disable-background-networking', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=3079', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] })
  const result = { synthetic_only: true, model_calls: 0, wechat_sends: 0, checks: [], errors: [] }
  let socket, counter = 0, evaluate, acceptHistoryConfirm = false
  const pending = new Map()
  const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++counter; const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)) }, 12000); pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params })) })
  try {
    let pages
    for (let attempt = 0; attempt < 80; attempt++) {
      try { pages = await (await fetch('http://127.0.0.1:3079/json/list')).json(); if (pages.some(page => page.type === 'page')) break } catch (error) { if (attempt === 79) throw error }
      await pause(100)
    }
    socket = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject })
    socket.onmessage = event => {
      const message = JSON.parse(event.data)
      if (message.id) { const call = pending.get(message.id); if (!call) return; pending.delete(message.id); clearTimeout(call.timer); message.error ? call.reject(new Error(JSON.stringify(message.error))) : call.resolve(message.result) }
      if (message.method === 'Runtime.exceptionThrown') result.errors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text)
      if (message.method === 'Page.javascriptDialogOpening') {
        result.dialogs = [...(result.dialogs || []), message.params.message]
        send('Page.handleJavaScriptDialog', { accept: acceptHistoryConfirm }).catch(error => result.errors.push(error.message))
      }
    }
    await send('Page.enable'); await send('Runtime.enable')
    evaluate = async expression => { const response = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails)); return response.result.value }
    const wait = async expression => { for (let attempt = 0; attempt < 100; attempt++) { if (await evaluate(expression)) return; await pause(100) } throw new Error(`Condition failed: ${expression}`) }
    const shot = async name => { const metrics = await send('Page.getLayoutMetrics'); const capture = await send('Page.captureScreenshot', name.endsWith('-dropdown') ? { format: 'png', captureBeyondViewport: false } : { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: Math.max(metrics.cssContentSize.width, metrics.cssLayoutViewport.clientWidth), height: Math.max(metrics.cssContentSize.height, metrics.cssLayoutViewport.clientHeight), scale: 1 } }); await fs.writeFile(path.join(output, `${name}.png`), Buffer.from(capture.data, 'base64')) }
    for (const [name, width, mode, unselected, zoom = 1, running = false, historyCase = false] of [
      ['model-unselected-desktop', 1280, 'settings-dialog', true], ['model-selected-desktop', 1280, 'settings-dialog'],
      ['model-narrow', 560, 'settings-dialog', true], ['model-zoomed', 1200, 'settings-dialog', true, 1.5],
      ['settings-desktop', 1280, 'settings'], ['settings-narrow', 560, 'settings'], ['toolbar-desktop', 1100, 'chat'], ['toolbar-narrow', 390, 'chat'],
      ['scope-running-desktop', 1280, 'settings-dialog', false, 1, true], ['scope-running-narrow', 560, 'settings-dialog', false, 1, true],
      ['history-desktop', 1100, 'settings-dialog', false, 1, false, true], ['history-narrow', 540, 'settings-dialog', false, 1, false, true],
    ]) {
      await send('Emulation.setDeviceMetricsOverride', { width, height: zoom > 1 ? 800 : 980, deviceScaleFactor: zoom, mobile: false })
      await send('Page.navigate', { url: `${values.fixture}?mode=${mode}${unselected ? '&unselected' : ''}${running ? '&running&many' : ''}${historyCase ? '&history' : ''}` })
      await wait(mode !== 'chat' ? 'document.querySelector(".ar-content")' : 'document.querySelector(".auto-reply-control [role=status]")?.textContent.includes("待核对")')
      if (name.startsWith('model-')) {
        await evaluate('document.querySelector(".ar-model summary").scrollIntoView({block:"center"})')
        await evaluate('document.querySelector(".ar-model summary").click()')
        await wait('document.querySelector(".ar-model details").open')
        // Existing selections first open strength controls; inspect the model list too.
        if (!unselected) {
          await evaluate('document.querySelector(".ar-model .strength-center").click()')
          await wait('document.querySelector(".ar-model .model-list")')
        }
        const popup = await evaluate(`(()=>{const rect=e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}};const button=document.querySelector('.ar-model .model-list-heading>button');const text=[...button.childNodes].find(node=>node.nodeType===3&&node.textContent.trim());const range=document.createRange();range.selectNodeContents(text);return {trigger:rect(document.querySelector('.ar-model summary')),menu:rect(document.querySelector('.ar-model .agent-selection-popover')),container:rect(document.querySelector('.qa-dialog-scroll')),backTextLines:range.getClientRects().length}})()`)
        result.checks.push({ name, zoom, popup })
        await shot(name)
        assert.ok(Math.abs(popup.menu.left - popup.trigger.left) < 2, `Model list must align with trigger: ${JSON.stringify(popup)}`)
        assert.ok(popup.menu.top >= popup.trigger.bottom, `Model list must open below trigger: ${JSON.stringify(popup)}`)
        assert.ok(popup.menu.left >= popup.container.left && popup.menu.right <= popup.container.right, `Model list must not be clipped horizontally: ${JSON.stringify(popup)}`)
        assert.ok(popup.menu.bottom <= popup.container.bottom, `Model list must remain visible vertically: ${JSON.stringify(popup)}`)
        assert.equal(popup.backTextLines, 1, `Back label must remain on one line: ${JSON.stringify(popup)}`)
      }
      if (running) {
        assert.equal(await evaluate('document.querySelector("[aria-label=自动回复总开关]")'), null)
        assert.equal(await evaluate('document.querySelectorAll(".ar-session").length'), 0)
        const before = await evaluate('document.querySelector(".ar-content").scrollHeight')
        await evaluate('document.querySelector("[aria-label=自动回复范围]").scrollIntoView({block:"center"})')
        await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        await evaluate('document.querySelector("[aria-label=自动回复范围]").click()')
        await wait('document.querySelector("[aria-multiselectable=true]")')
        await evaluate('document.querySelectorAll(".ui-select-actions button")[1].click()')
        await wait('document.querySelector("[aria-label=自动回复范围]").textContent.includes("选择群聊或个人")')
        await evaluate('document.querySelector(".ui-select-menu [role=option]").click()')
        await wait('document.querySelector("[aria-label=自动回复范围]").textContent.includes("0 个群聊、1 个私聊")')
        await evaluate('document.querySelector(".ui-select-actions button").click()')
        await wait('document.querySelector("[aria-label=自动回复范围]").textContent.includes("47 个群聊、10 个私聊")')
        const dropdown = await evaluate(`(()=>{const e=document.querySelector('.ui-select-menu'),r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height,scrollHeight:e.scrollHeight,selected:e.querySelectorAll('[role=option][aria-selected=true]').length}})()`)
        assert.equal(dropdown.selected, 57)
        assert.ok(dropdown.height <= 280 && dropdown.scrollHeight > dropdown.height)
        assert.ok(dropdown.left >= 0 && dropdown.right <= width && dropdown.top >= 0 && dropdown.bottom <= 980)
        result.checks.push({ name: `${name}-dropdown`, dropdown })
        await shot(`${name}-dropdown`)
        await evaluate('document.querySelector(".ui-select-search input").dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))')
        await wait('!document.querySelector(".ui-select-menu")')
        assert.equal(await evaluate('document.querySelectorAll(".ar-session").length'), 0)
        assert.equal(await evaluate('document.querySelector(".ar-content").scrollHeight'), before)
      }
      if (historyCase) {
        await wait('document.querySelector(".ar-pagination").textContent.includes("共 6 条 · 第 1 页")')
        assert.equal(await evaluate('document.querySelectorAll(".ar-records > li").length'), 5)
        assert.equal(await evaluate('[...document.querySelector(".ar-records > li").querySelectorAll("button")].some(button=>button.textContent.trim()==="删除记录")'), false)
        await evaluate('document.querySelector(".ar-history").scrollIntoView({block:"start"})')
        await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        await shot(`${name}-page-one`)
        await evaluate('[...document.querySelectorAll(".ar-pagination button")].find(button=>button.textContent.trim()==="下一页").click()')
        await wait('document.querySelector(".ar-pagination").textContent.includes("共 6 条 · 第 2 页")')
        assert.equal(await evaluate('document.querySelectorAll(".ar-records > li").length'), 1)
        acceptHistoryConfirm = true
        await evaluate('[...document.querySelectorAll(".ar-records button")].find(button=>button.textContent.trim()==="删除记录").click()')
        acceptHistoryConfirm = false
        await wait('document.querySelector(".ar-pagination")?.textContent.includes("共 5 条 · 第 1 页")')
        assert.equal(result.dialogs.at(-1), '删除这条回复记录？仅从本程序的回复历史中移除，不会删除或撤回微信消息。')
        assert.equal(await evaluate('document.querySelectorAll(".ar-records > li").length'), 5)
        assert.equal(await evaluate('[...document.querySelectorAll(".ar-pagination button")].every(button=>button.disabled)'), true)
        await evaluate('document.querySelector("[aria-label=记录状态筛选]").click()')
        await evaluate('[...document.querySelectorAll(".ui-select-menu [role=option]")].find(option=>option.textContent.trim()==="待核对").click()')
        await wait('document.querySelector(".ar-pagination")?.textContent.includes("共 1 条 · 第 1 页")')
        const bulkButton = '[...document.querySelectorAll(".ar-history > .ar-heading button")].find(button=>button.textContent.trim()==="一键删除")'
        await evaluate(`${bulkButton}.click()`)
        assert.equal(result.dialogs.at(-1), '一键删除当前账号所有会话、所有分页中可删除的回复记录？此操作不受当前筛选影响，正在处理和待核对的记录会保留。仅从本程序的回复历史中移除，不会删除或撤回微信消息。')
        assert.equal(await evaluate('window.autoReplyAcceptance.requests.filter(request=>request.method==="DELETE").length'), 1)
        acceptHistoryConfirm = true
        await evaluate(`${bulkButton}.click()`)
        acceptHistoryConfirm = false
        await wait('document.querySelector(".ar-history > [role=status]")?.textContent === "已删除 4 条回复记录。"')
        await wait('document.querySelector(".ar-pagination")?.textContent.includes("共 1 条 · 第 1 页")')
        assert.equal(await evaluate('document.querySelector("[aria-label=记录状态筛选]").textContent.trim()'), '待核对')
        assert.equal(await evaluate('document.querySelectorAll(".ar-records > li").length'), 1)
        assert.equal(await evaluate('document.querySelector(".ar-records > li strong").textContent'), '产品测试群')
        assert.equal(await evaluate('[...document.querySelectorAll(".ar-records button")].some(button=>button.textContent.trim()==="删除记录")'), false)
        const historyRequests = await evaluate('window.autoReplyAcceptance.requests.filter(request=>request.path.startsWith("/auto-reply/history"))')
        const deleteRequests = historyRequests.filter(request => request.method === 'DELETE')
        assert.equal(deleteRequests.length, 2)
        assert.deepEqual(deleteRequests.at(-1), { path: `/auto-reply/history?account=${encodeURIComponent('演示数据账号')}`, method: 'DELETE', retry: 0 })
        assert.ok(historyRequests.filter(request => request.method === 'GET').every(request => new URL(request.path, 'http://fixture').searchParams.get('page_size') === '5'))
        assert.equal(new URL(historyRequests.at(-1).path, 'http://fixture').searchParams.get('status'), 'needs_review')
        const headingButtons = await evaluate(`(()=>{const e=document.querySelector('.ar-history > .ar-heading');return [...e.querySelectorAll('button')].map(button=>{const range=document.createRange();range.selectNodeContents(button);const r=button.getBoundingClientRect();return {text:button.textContent.trim(),lines:range.getClientRects().length,left:r.left,right:r.right,top:r.top,bottom:r.bottom}})})()`)
        assert.ok(headingButtons.every(button => button.lines === 1 && button.left >= 0 && button.right <= width), JSON.stringify(headingButtons))
        assert.ok(Math.abs(headingButtons[0].top - headingButtons[1].top) < 3, JSON.stringify(headingButtons))
        const widthCheck = await evaluate('({scroll:document.querySelector(".qa-dialog-scroll").scrollWidth,client:document.querySelector(".qa-dialog-scroll").clientWidth})')
        assert.ok(widthCheck.scroll <= widthCheck.client, JSON.stringify(widthCheck))
        result.checks.push({ name: `${name}-pagination-delete-bulk`, widthCheck, headingButtons, historyRequests })
      }
      const layout = await evaluate(`({ width: innerWidth, documentWidth: document.documentElement.scrollWidth, toolbar: (()=>{const e=document.querySelector('.chat-input-tools');if(!e)return null;return {flexWrap:getComputedStyle(e).flexWrap,whiteSpace:getComputedStyle(e).whiteSpace,children:[...e.children].map(x=>({top:x.getBoundingClientRect().top,height:x.getBoundingClientRect().height})),controlWhiteSpace:getComputedStyle(document.querySelector('.auto-reply-control')).whiteSpace}})(),requests:window.autoReplyAcceptance.requests })`)
      assert.ok(layout.documentWidth <= width, JSON.stringify(layout))
      assert.ok(layout.requests.every(request => request.method === 'GET' || (historyCase && request.method === 'DELETE' && (request.path.startsWith('/auto-reply/history/') || request.path.startsWith('/auto-reply/history?')))))
      assert.deepEqual(await evaluate('({synthetic_only:window.autoReplyAcceptance.synthetic_only,model_calls:window.autoReplyAcceptance.model_calls,wechat_sends:window.autoReplyAcceptance.wechat_sends})'), { synthetic_only: true, model_calls: 0, wechat_sends: 0 })
      if (layout.toolbar) { assert.equal(layout.toolbar.flexWrap, 'nowrap'); assert.equal(layout.toolbar.controlWhiteSpace, 'nowrap'); assert.ok(Math.max(...layout.toolbar.children.map(item => item.top)) - Math.min(...layout.toolbar.children.map(item => item.top)) < 3) }
      result.checks.push({ name, ...layout }); await shot(name)
    }
    assert.deepEqual(result.errors, []); result.passed = true
  } catch (error) {
    result.passed = false; result.failure = error.stack; process.exitCode = 1
    if (evaluate) { try { result.debug = await evaluate('({body:document.body.textContent,requests:window.autoReplyAcceptance?.requests})') } catch (debugError) { result.debug_error = debugError.message } }
  } finally {
    if (socket?.readyState === 1) { try { await send('Browser.close') } catch (error) { result.cleanup_error = error.message }; socket.close() }
    if (browser.exitCode === null && browser.signalCode === null) browser.kill()
    for (const call of pending.values()) clearTimeout(call.timer)
    await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify({ passed: result.passed, checks: result.checks.map(check => check.name), errors: result.errors, failure: result.failure, output }, null, 2))
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
