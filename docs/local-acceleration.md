# 本地模型加速与验收

在 **设置 → 本地加速** 查看显卡、安装独立组件、验证模型并选择运行设备。该入口统一管理本地语义检索和附件 OCR；CPU 随基础程序提供。加速只改变本机推理设备，不改变账号权限、索引范围或 Agent 的回答模型服务。

## 功能范围

| 功能 | 可选设备 | 设置与运行状态 |
| --- | --- | --- |
| 本地语义检索 | CPU、NVIDIA CUDA、AMD DirectML | 模型与设备偏好按微信账号保存；三款现有 FP32 模型逐个验证 |
| 附件 OCR | CPU、AMD DirectML | 全局偏好；RapidOCR 3.10.0，Det/Rec 为 PP-OCRv6，Cls 保留 PP-OCRv2 |
| 语音转文字 | 沿用各语音模型现有后端 | 本入口展示实际状态；设备偏好仍在“语音转文字”设置，当前未接入 AMD |
| Laya 本地分析 | CPU | 成功推理后才显示实际 CPU；未接入 AMD |

GPU 能力按“设备、驱动、组件、当前模型”分别验证。发现 AMD 显卡、安装成功或 Provider 名称存在，都不表示某个模型已经可以使用。当前 AMD 实机证据见下文；OCR 首版未接入 NVIDIA GPU。

## 使用步骤

1. 打开 **设置 → 本地加速**，等待异步检测完成。启动检测不下载组件，也不预加载全部模型。更换显卡、驱动或处理故障后，点击 **重新检测显卡**。
2. 如需 AMD，下载 **AMD DirectML** 组件；也可点击 **离线导入**，填写固定版本 wheel 所在的本机目录。下载可暂停和继续，错误会显示具体原因。
3. 检索先在 **设置 → AI 服务 → 本地检索** 下载并选择模型，再在本地加速中验证；OCR 使用随基础程序准备的三个权重。组件安装后会触发当前可验证模型的验证，其他模型切换后仍须单独通过验证。
4. 选择 **自动**、**CPU** 或已验证的具体 GPU。检索自动优先使用已验证的 CUDA，其次是已验证的 AMD DirectML，再使用 CPU；OCR 自动在已验证的 AMD DirectML 与 CPU 之间选择。显式 GPU 尚未通过验证时不可选。保存从下一个工作单元生效；一份附件中的所有页固定使用开始时的配置。
5. 在“功能运行”查看实际设备。**尚未运行**、**已通过验证**和实际运行是不同状态；验证不会伪装成已处理业务数据。模型进程释放后显示上次运行。GPU 部分算子在 CPU 执行时显示 **GPU + 部分 CPU**。

附件中心提供本地加速入口；已完成正文中的设备信息来自那次识别的缓存，属于历史结果，不表示 GPU 当前仍占用。OCR 依旧由用户主动触发，识别时不下载模型、不调用云端服务。

### 故障与恢复

**自动模式**仅在明确归类的 GPU 初始化、执行或显存故障后，销毁故障 worker，在独立 CPU worker 中重做尚未提交的工作单元一次，并展示改用 CPU 的原因。OCR 随后的页继续使用 CPU，整项完成后才发布缓存。已故障设备会保持隔离，重启不会悄悄重新尝试；重新检测后需重新验证。

**显式 GPU**发生故障会停止该任务，不改用 CPU。取消、无效输入、模型缺失或损坏、配置错误、未知进程退出及超时不会被当成可自动恢复的 GPU 故障。CPU 再失败也会停止；检索保留已提交进度，附件不会发布半份成功结果。配置损坏和读写失败直接展示错误，不重置成正常默认值。

## 独立组件与模型

AMD 清单为 [`local_acceleration_dml.json`](../src/wechat_decrypt_tool/resources/local_acceleration_dml.json)，使用官方 PyPI 固定文件、大小与 SHA-256。Windows x64 / CPython 3.11 的下载闭包如下：

| 文件 | 下载字节数 |
| --- | ---: |
| `onnxruntime_directml-1.24.4-cp311-cp311-win_amd64.whl` | 25,106,000 |
| `sympy-1.14.0-py3-none-any.whl` | 6,299,353 |
| `mpmath-1.3.0-py3-none-any.whl` | 536,198 |
| 合计 | **31,941,551** |

`flatbuffers`、`numpy>=1.21.6`、`packaging`、`protobuf` 由基础应用提供；冻结构建也收集对应包及元数据。CPython 3.12/3.13 使用清单中匹配 ABI 的 ORT wheel，下载总量略有不同，尚未在对应解释器实机验收。离线导入必须同时提供匹配 ABI 的 ORT wheel、SymPy 和 mpmath，不能只复制一个 DLL。解压还需额外磁盘空间。

组件在应用数据目录中独立解压，不替换基础 CPU ORT、不修改系统 PATH、不安装全局 CUDA Toolkit。CPU、CUDA、DirectML 使用隔离推理进程，进程先选择运行时再导入模型库；不得在同一进程混用两份 ORT。NVIDIA 继续使用现有 [CUDA 组件清单](local-semantic-search.md#cpu--nvidia--amd-兼容矩阵)。

检索沿用 [`local_search_models.json`](../src/wechat_decrypt_tool/resources/local_search_models.json) 固定的 BGE Small 中文、BGE Base 中文和 Multilingual E5 Small，未重新导出或量化。三者均为 ONNX IR 6 / opset 11。OCR 沿用 [`local_ocr.py`](../src/wechat_decrypt_tool/local_ocr.py) 的三个原文件及 SHA-256：Det/Rec 为 IR 10 / opset 11，Cls 为 IR 7 / opset 11。每次加载验证完整模型哈希；加速不改变权重、词表、池化、前缀或图像预处理。

## 2026-10-11 AMD 实机记录

环境为 Windows x64、CPython 3.11.16、AMD Radeon RX 6750 GRE 10GB、驱动 `32.0.21045.9003`；CPU ORT 1.28.0，DirectML ORT 1.24.4。真实显卡当前原始 DXGI 索引为 0，专用显存 10,689,413,120 字节。设备以持久身份保存，每次 worker 启动重新映射当前索引和 LUID；检测排除了同名但无渲染能力的间接显示虚拟适配器。不能把过滤后列表的位置直接传给 DirectML。

全部输入为预先生成的非私人文本、图片和 PDF，没有读取用户聊天、账号目录或用户附件。以下“节点数”是固定样例 profile 中的执行事件数，不是唯一算子数，随样例数与执行次数变化；GPU 列确为 `DmlExecutionProvider` 的已执行事件。

### 检索正确性与执行证据

固定三个主题为项目交付、周末聚餐、发票金额。三模型 CPU 与 DML 查询都命中 `[0, 1, 2]`；验证包括变长输入、批量形状变化、向量维度、有限值与归一化。预先固定判据为最大绝对误差 ≤0.001、最小余弦相似度 ≥0.99995。

| 模型 | 隔离探针最大绝对误差 | 最小余弦相似度 | 生产 warmup DML / CPU 执行事件 |
| --- | ---: | ---: | ---: |
| BGE Small 中文 | 2.8313e-7 | 0.9999999726 | 250 / 4 |
| BGE Base 中文 | 2.3842e-7 | 0.9999999765 | 698 / 4 |
| Multilingual E5 Small | 1.1921e-7 | 0.9999999622 | 698 / 4 |

隔离探针覆盖更多调用，对应 DML/CPU 事件分别为 1125/18、3141/18、3141/18。接入正式 `LocalInference` worker 后再次逐模型验证，命中仍一致，最大绝对误差均小于 2.1e-7。上述是固定检索样例，不代表真实账号的全部查询质量。

| 模型 | 单查询 CPU / DML，毫秒 | 批量 8 条 CPU / DML，毫秒 | 首次编码 CPU / DML，毫秒 |
| --- | ---: | ---: | ---: |
| BGE Small 中文 | 5.84 / 17.15 | 16.89 / 19.88 | 9.11 / 73.18 |
| BGE Base 中文 | 29.46 / 63.93 | 96.25 / 71.74 | 44.22 / 74.43 |
| Multilingual E5 Small | 8.05 / 50.51 | 38.20 / 51.64 | 19.30 / 62.06 |

单查询与批量耗时为三次均值，开启 profiling。此样例中仅 BGE Base 的批量 8 条 DML 更快；自动设备优先级不是“当前 GPU 一定比 CPU 快”的承诺。首次编码不含模型加载，加载时间也单独保存在证据中。

另用正式分块、worker 和 SQLite 提交路径完成 24 条固定消息的索引复验：每模型 24 个片段、3 批 × 8 条、一次提交。三个主题 top1 命中正确，CPU 索引配 DML 查询及反向查询均通过；切换前后的消息/片段统计、提交游标和已存向量 SHA-256 完全不变。

| 模型 | 新 worker 冷启动 CPU / DML，秒 | 24 条索引含冷启动 CPU / DML，秒 | 单查询含 SQLite CPU / DML，毫秒 |
| --- | ---: | ---: | ---: |
| BGE Small 中文 | 0.368 / 0.677 | 0.448 / 0.819 | 5.42–5.89 / 12.79–17.31 |
| BGE Base 中文 | 0.979 / 1.158 | 1.363 / 1.369 | 26.21–27.75 / 52.04–58.62 |
| Multilingual E5 Small | 1.394 / 1.933 | 1.871 / 2.549 | 6.05–6.23 / 35.48–37.43 |

这是一次小批量记录；冷启动指新进程，没有清除系统文件缓存，未包含微信读取、数据库建表和 UI。完整数据和复现脚本分别为 `index-production-result.json`、`index-production-check.py`，位于本地验收目录。

### OCR 正确性与执行证据

样例固定为中文“本地文字识别测试”、英文 `Receipt ZX739201`、数字 `1234567890`，覆盖正常图、整图 180° 旋转、空白、双页扫描 PDF，以及原生标题加内嵌图片 PDF。每张 OCR 图重复三次，9 个图像/PDF片段的文本、页标和顺序与 CPU 完全一致；框坐标和置信度的实际最大差均为 0，预设容差分别为 2 px 和 0.02。正式生产 worker 重放六个 OCR 单元也全部符合固定预期。

| 阶段 | 正式 worker warmup DML 事件 | CPU 事件 | 全部隔离样例 DML / CPU 事件 |
| --- | ---: | ---: | ---: |
| 检测 Det | 314 | 0 | 2826 / 0 |
| 方向分类 Cls | 310 | 0 | 2325 / 0 |
| 识别 Rec | 340 | 4 | 2550 / 30 |
| 正式 warmup 合计 | **964** | **4** | — |

三个阶段各自有 GPU 执行证据；Rec 仍有部分 CPU 执行，实际状态显示 GPU + 部分 CPU。

| OCR 记录 | CPU 秒 | DML 秒 |
| --- | ---: | ---: |
| 导入与三阶段初始化 | 0.614 | 0.856 |
| 第一次正常单图推理 | 0.841 | 1.201 |
| 正常单图三次中位数 | 0.807 | 0.135 |
| 旋转图三次中位数 | 0.775 | 0.157 |
| 扫描 PDF 第一页三次中位数 | 6.890 | 0.330 |
| 扫描 PDF 第二页三次中位数 | 6.850 | 0.310 |
| 双页 PDF 读取与每页三次 OCR | 41.417 | 2.151 |

单图大小为 1100×550，扫描 PDF 页渲染为 2200×1100。GPU 首图包括首次图编译成本。检索与 OCR 的计时均未控制并行任务、系统负载及温度，属于本次执行记录，不是严格性能基准；不能据此承诺所有输入加速。生产 OCR 的独立验证加六个正式单元重放总计约 6.267 秒，也不与上述单次时间直接比较。

## 验收层次与证据文件

| 层次 | 已完成 | 边界 |
| --- | --- | --- |
| 自动化测试 | 配置/组件/设备选择、故障分类、一次 CPU 恢复、取消、关闭、账号隔离、附件整项发布等 | 模拟错误证明逻辑行为，不证明真实驱动故障恢复 |
| 源码应用生命周期 | 完整应用在隔离空数据目录完成 startup → 状态 API → shutdown | 没有加载业务模型，没有使用真实账号数据 |
| 源码真实推理 | 三个检索模型 CPU/DML 对比，OCR 三阶段及固定图片/PDF，正式 worker 重放 | 非私人固定样例，不是实际账号全流程 |
| 生产 HTTP API | ASGI 路由的三检索模型与 OCR 验证真实执行通过；验证后业务状态仍是尚未运行 | 在进程内调用生产 API，不是浏览器到桌面的完整链路 |
| PyInstaller frozen | 6.22.3 onedir 可执行程序调用正式 worker，BGE Small CPU/DML 与 OCR CPU/DML 均通过 | 使用真实 `aiPackagingArgs` 和依赖元数据；未覆盖三检索模型的全部 frozen 组合 |
| v1.3.0 正式 onefile 后端 | 从最终发布包启动 HTTP API，三个检索模型与 OCR 三阶段真实 DML 验证通过；合成附件 CPU/DML OCR 全文一致、实际设备匹配且无回退 | 非私人固定样例；不等同真实账号全流程或所有输入性能保证；本次发布验收未发送微信消息，也未验证自动回复投递 |
| v1.3.0 安装包/ZIP | PE 版本、ASAR、静态 UI、ZIP CRC、NSIS 与展开目录逐文件哈希检查通过；OCR 权重、资源与依赖元数据完整；包内未包含本机运行数据或凭据 | 尚未覆盖安装器实际安装、升级、干净 Windows、其他物理显卡或 Electron 完整业务验收 |
| NVIDIA | 保留既有 CUDA 组件和接口，历史检索证据见检索文档 | 本机无 NVIDIA，未重跑本轮代码的 CUDA 实机矩阵；OCR 未接入 NVIDIA GPU |
| 其他设备/平台 | CPU 路径保留，AMD 清单限定 Windows x64 | 其他 AMD、Intel GPU、Python 3.12/3.13 等没有本轮实机证据 |

语音后端本版未新增 AMD 加速，Laya 继续使用 CPU；普通关键词搜索、数据库查询与 PDF 文本读取沿用现有实现。以上验证记录与限制保存在项目文档，GitHub Release 只保留版本变更、下载与必要使用说明。

frozen 实测中，CPU 1.28.0 的模块和原生库位于 `_internal`，DirectML 1.24.4 来自完整外置组件目录；BGE Small warmup 为 250 GPU / 4 CPU，OCR 为 964 GPU / 4 CPU。已核验实际模块来源，不能仅用 frozen 导入成功代替推理验证。

本地证据位于 `tmp/local-acceleration-validation/`，属于本次工作区生成物，通常不会进入 Git 或发布包：

- `hardware.json`：原生设备身份、索引、驱动及排除理由。
- `search-cpu.json`、`search-dml.json`、`search-comparison.json`、`search-opsets.json`：三模型结果、耗时、误差和模型元数据；同目录 `cpu-*.json` / `dml-*.json` 为原始 profile。
- `search-production-result.json`：正式检索 worker 的逐模型结果、实际 ORT 模块/原生库来源及 warmup 证据。
- `ocr-probe/samples/manifest.json`：固定输入及字体哈希；`ocr-probe/cpu.json`、`dml.json`、`comparison.json` 和阶段 profile：OCR 结果、差异与耗时；`ocr-opsets.json`：模型元数据。
- `ocr-production-result.json`、`ocr-probe-report.md`：生产 OCR 重放与阶段记录。
- `api-production-result.json`：生产 API 验证任务及验证后的实际业务状态。
- `source-app-result.json`、`source-app-check.log`：完整源码应用在隔离空数据目录的启动、状态与关闭记录。
- `frozen-result.json`、`frozen-build.log`、`acceleration-probe.spec`：冻结程序推理结果、构建日志和构建输入。

## 开发与复验

共享管理入口在 [`local_acceleration.py`](../src/wechat_decrypt_tool/local_acceleration.py)，组件、设备与业务 worker 通过持久身份和验证键连接。验证键包含功能、模型版本/哈希、后端、设备、驱动、组件与探针版本；改变任一项需要对应验证。检索按一次编码、OCR 按整份附件固定配置，worker 不写用户数据库，结果提交仍由父进程保留既有账号和生命周期校验。

[`local_acceleration_runtime.py`](../src/wechat_decrypt_tool/local_acceleration_runtime.py) 集中加载 ORT、创建 Session 和读取 profile。DirectML 使用顺序执行、关闭 memory pattern，禁止 ORT 整个 Session 隐式切到 CPU。CPU 算子分区会如实记录。固定样例 warmup 完成后关闭 profiling，正式私有输入不生成 profile。OCR 借助 RapidOCR 原生 `cfg.session` 注入三个真实 Session，不修改 site-packages、不全局 monkeypatch，也不先创建无用 CPU 模型。

运行下面命令前，须已显式准备清单匹配的组件与模型。`device-identity` 使用当前检测的持久身份；OCR 独立探针的 `device-id` 必须使用原生 DXGI 原始索引。示例针对本机，其他电脑不能照抄索引：

```powershell
.venv\Scripts\python.exe tools/verify_local_acceleration.py --backend cpu --models-root tmp/local-acceleration-validation/models --output tmp/local-acceleration-validation/repeat-search-cpu.json
.venv\Scripts\python.exe tools/verify_local_acceleration.py --backend dml --runtime-root tmp/local-acceleration-validation/components/dml-ort1.24.4/cp311 --device-identity '<从当前检测结果复制设备身份>' --models-root tmp/local-acceleration-validation/models --output tmp/local-acceleration-validation/repeat-search-dml.json --compare-with tmp/local-acceleration-validation/repeat-search-cpu.json
.venv\Scripts\python.exe tools/probe_ocr_dml.py --mode compare --runtime-root tmp/local-acceleration-validation/components/dml-ort1.24.4/cp311 --device-id 0 --output-root tmp/local-acceleration-validation/repeat-ocr --repetitions 3
.venv\Scripts\python.exe -m pytest tests/test_local_ocr_acceleration.py tests/test_local_ocr.py tests/test_attachments.py tests/test_local_search_ocr.py tests/test_library_extracted_text.py tests/test_ocr_dml_probe.py -q
```

冻结复验入口是 [`tools/verify_local_acceleration_frozen.py`](../tools/verify_local_acceleration_frozen.py)，必须从实际构建的可执行程序运行并确认结果中的 `frozen=true`、真实模型输出和 GPU 执行证据。新增硬件支持或升级组件时，应重新执行对应模型的 CPU 对比、正式 worker 和 frozen 验证，再更新本记录。

最终定向回归记录：26 个相关 Python 测试文件共 **399 passed**（含受控 CPU 推理，既有 jieba 弃用提示 1 条），桌面 Node 测试 **143 passed**。前端全量 Node/Vitest 测试及生成构建通过；其后设备错误展示等小改的相关 **86 项测试**通过。原始日志位于验收目录。

后续故障分类、停止闸门与平台限制检查分别通过 114 项、23 项定向测试；它们与上述回归有重叠，不累加为独立用例总数。最终前端重新生成 38 个路由成功。无对应硬件的项目仍按验收矩阵标记未验证。

### 实施后的复查

2026-10-11 追加复查：相同运行状态不再重复写事件；状态写入失败仍会抛错，不缓存为成功。验证完成后释放任务与取消令牌，同一验证键仅保留最新结果，重新检测后的任务与旧代次隔离。手动安装和重启续传共用安装后的自动验证流程，沿用首次安装的账号上下文；没有旧账号上下文时不猜测账号。

本地加速页合并重复刷新，仅在栏目激活时订阅事件及每 5 秒读取语音/Laya 状态，关闭后停止；验证任务按当前模型显示，CPU 回退历史也明确标注上次运行。复查后的后端相关测试 **171 passed**、前端相关测试 **91 passed**，38 路由生成成功；三个检索模型与 OCR 的生产验证 API 已重新执行真实 AMD 推理，源码应用启动/状态/关闭也再次通过。

v1.3.0 发布验收补充：滚动改变设置栏目高亮时保留同账号已加载内容、导入路径与进行中的操作；显式操作仍可刷新结果，账号切换和关闭会隔离旧响应。附件图片/视频预览改为居中原生弹窗，打开和关闭保持列表位置，支持关闭按钮、Esc 与遮罩关闭。桌面与窄窗口浏览器验证使用合成数据；最终相关后端回归 188 项、桌面回归 143 项、前端 Node 85 项和 Vitest 1122 项通过，38 路由生成完成。
