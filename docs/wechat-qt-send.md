# Windows 微信 4.x 发送适配

本次验证环境：Windows、微信（Weixin）4.1.15.13 中文界面。

## 原因与实现

微信主窗口类为 `Qt51514QWindowIcon`；Win32 子窗口枚举只提供渲染窗口，
不提供单独的 Edit HWND。输入框实际由 UI Automation 暴露，
AutomationId 为 `chat_input_field`，类型为 Edit。其 Name 会附加空白输入框的
语音提示，不能用来判断会话身份；当前会话名称检查使用会话标题的精确名称。

`WeChatBridge` 在公共的锁屏、进程、窗口、激活检查之后，按窗口类显式选择
Qt 适配器 `WeChatQtUI`。原有非 Qt 路径保持不变，Qt 失败不会回退到盲发按键。

Qt 流程：

1. 使用搜索框 ValuePattern 写入目标名称，并回读验证。
2. 只接受名称完全一致的本地会话搜索结果；同名结果（包括屏幕外结果）报错。
3. 点击该结果控件的 UIA 坐标，核对会话标题精确匹配目标，并定位聊天面板中的固定输入控件。
4. 检查现有草稿、设置焦点，通过 ValuePattern 写入正文，并逐字回读验证。
5. 在 `chat_message_page` 内定位发送按钮，重新核对目标和正文后点击一次。
6. 观察输入框清空及消息列表出现新的对应消息元素，才返回成功。

搜索结果和发送按钮的 Invoke 在本机均曾返回成功但未执行动作，因此两者均
通过 UIA 定位后点击控件坐标，随后核实界面结果。这是明确的适配方式，不是
Invoke 失败后的重发兜底。消息正文通过 ValuePattern 写入，不使用全局
Ctrl+V / Alt+S，也不改变剪贴板。

发送按钮与输入框共同属于 `chat_message_page`；`content_view.bottom_ui_`
是同级区域内的占位容器，不是发送按钮的祖先。测试树按照本机读取的该结构构造。

界面转换期间，Qt 可能销毁正在枚举的控件。只对
`UIA_E_ELEMENTNOTAVAILABLE (0x80040201)` 重新查询，并记录 Warning；
受桥接的 `send_timeout_s`（默认 10 秒）限制。其余 COM 错误直接暴露。
依据：[Microsoft UI Automation 错误码](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-error-codes)。

## 错误与结果语义

- `WECHAT_SESSION_AMBIGUOUS`：出现同名结果，不选择第一条发送。
- `WECHAT_DRAFT_CONFLICT`：微信中已有不同的草稿，不覆盖或追加。
- `WECHAT_INPUT_WRITE_FAILED`：文本接口不可写或回读不一致。
- `WECHAT_SEND_UNCONFIRMED`：已尝试调用发送，但没有确认结果；保留项目草稿，
  从不确定结果返回时重新开始现有的三秒防重复窗口，不自动重发。
- `WECHAT_UIA_ERROR`：UIA 初始化或控件操作失败；后台保留完整异常链及阶段。

成功仅表示本机微信客户端已处理提交并显示新消息，不是微信服务器的送达回执。
当前 GUI 按展示名定位；不具备账号切换或按 wxid 唯一识别收件人的能力。
自动回复默认关闭，启用时由用户确认数据账号与微信当前登录账号一致，明确选择窗口并授权回复范围。
运行期间绑定进程 ID、进程启动时间和窗口句柄，按完整名称定位会话，以客户端显示证据确认本机提交。
这些检查不能证明实时登录账号、收件人 ID 或新自身数据库消息身份；运行期间不得切换微信账号。
同名结果、名称变化或不同草稿会暂停相应会话；提交结果未知时保留记录供人工核对，不自动重试或补发旧批次。
应用重启后不会自动启用回复，需要重新确认窗口和账号；完整自动回复流程仍需单独进行真实模型和微信实机验收。
不宣称支持所有微信版本、其他语言界面或屏幕阅读接口被禁用的环境。

## 使用与验证

新增的 Windows 依赖 `uiautomation==2.0.29` 已写入 `pyproject.toml` 和 `uv.lock`，
当前工作区 `.venv` 已安装。源码开发模式重启项目后使用本次代码；其他机器按
仓库原有流程执行 `uv sync --no-editable`。安装包需要重新构建；Windows
打包脚本已增加 UIA 依赖收集。

回归命令（项目根目录）：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_wechat_qt_ui.py tests/test_wechat_ui_bridge.py tests/test_ui_bridge_adversarial.py tests/test_ui_bridge_challenger.py tests/test_chat_send_api.py tests/test_chat_send_adversarial.py -q -p no:cacheprovider
```

本机无发送验证已完成：输入框识别、两个会话之间切换、文件传输助手中中文/换行/
表情草稿写入与逐字回读、草稿恢复。独立 PyInstaller 最小程序已验证冻结环境中的
UIA 初始化及真实输入框识别；这不等同于完整安装包验收。

2026-09-30 验证记录：

- 相关后端回归测试 145 项通过，其中 Qt 适配测试 20 项；前端输入组件测试 29 项通过。
- 经用户授权，真实代码已向文件传输助手发出一条测试消息，输入清空且出现新消息。
- 此次实发暴露了空输入框 Name 附加语音提示造成的回执误判；已以回归测试复现并
  修正。修正后的只读真机检查确认 `input_found=true`、`draft_empty=true`。
- 用户额外授权后，09:58 通过真实 `WeChatBridge.send_message` 向文件传输助手发送
  一条“发送回执验证 2026-09-30”，返回 `success=true`、`content_length=17`、
  `duration_ms=900.24`，进程退出码为 0。
- 实发前已有一条同文消息，实发后新增一条，输入框清空；即使输入框 Name 带语音
  提示，最终代码仍正确确认成功。此次验证覆盖真实发送与本机回执，不代表服务器送达回执。
