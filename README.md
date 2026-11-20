# Operator Platforms

一个面向国产芯片适配场景的 vLLM 算子工作台 Demo，打通：

```text
vLLM 版本差异 → 算子范围分类 → 源码证据 → Engram 风格文档 → 审核 → Mock Jira → 记录追踪
```

当前内置真实版本对比：`v0.30.0` → `v0.34.0`。

## 已实现

- 真实拉取并比较两个 vLLM 固定 commit，缓存分析结果。
- 默认展示通用 Python `custom_op`、通用 `_C` 注册和 TileLang kernel。
- 单独保留设备专属实现、测试定义和调用点，并展示分类理由。
- 查看算子源码、Git diff、固定版本链接和识别依据。
- 收集函数签名、C++ schema、fake/meta、调用方及测试证据。
- 生成 Engram 风格的结构化文档草案，未确认内容明确标记“待确认”。
- 文档中心支持历史文档阅读、状态过滤和 Markdown 下载。
- 文档审核、幂等 Mock Jira 创建及完整事件追踪。
- 深色工程控制台，包含总览、算子、文档、Jira 和记录页面。

## 在另一台机器上运行

### 1. 环境要求

- Python 3.10 或更高版本。
- Git。
- 首次运行时可以访问 GitHub，用于拉取 vLLM 源码。
- 不需要安装任何第三方 Python 包。

### 2. 克隆并启动

```bash
git clone https://github.com/Ht-zhang-xianyu/op_platforms.git
cd op_platforms
python3 app.py serve
```

终端显示以下地址后，在浏览器打开：

```text
http://127.0.0.1:8765
```

首次启动会自动执行：

1. 将 vLLM `v0.30.0` 和 `v0.34.0` 拉取到 `.data/vllm.git`。
2. 分析两个版本的源码差异。
3. 将结果写入 `.data/workbench.db`。
4. 后续启动复用缓存，不重复分析。

也可以先手动执行分析：

```bash
python3 app.py analyze
python3 app.py serve
```

### 3. 让另一台机器通过局域网访问页面

在运行服务的机器上执行：

```bash
python3 app.py serve --host 0.0.0.0 --port 8765
```

然后访问：

```text
http://<运行服务的机器IP>:8765
```

当前 Demo 没有登录和权限控制，只建议在可信局域网内这样启动，并确认本机防火墙允许该端口。

## 可选：使用模型生成文档

默认使用 `offline-engram-v2` 离线模板，不需要 API Key。如需调用模型，在服务端环境配置：

```bash
export KIMI_API_KEY="你的 API Key"
export KIMI_MODEL="你的模型 ID"
# 可选，默认值是 https://api.moonshot.cn/v1
export KIMI_BASE_URL="https://api.moonshot.cn/v1"

python3 app.py serve
```

API Key 只应配置在服务端环境变量中，不要写入代码或提交到 Git。当前模型路径是一次性文档生成，还不是具有自主工具调用循环的完整 Agent。

## 数据保存与多机器说明

以下内容只保存在当前机器的 `.data/` 中，不会提交到 GitHub：

- vLLM Git 缓存。
- 版本分析结果。
- 生成的文档及审核状态。
- Mock Jira 和事件记录。

因此，另一台机器克隆后得到的是相同代码，但不是当前机器的文档和历史数据副本。首次启动会重新分析，文档中心需要重新生成内容。

## 页面入口

- **总览**：版本、默认工作项、文档和 Jira 统计。
- **算子清单**：按范围、变化和文档状态过滤，生成文档并查看证据。
- **文档中心**：阅读历史文档、下载 Markdown、跳转算子和 Jira。
- **Jira 追踪**：查看 Mock 工单与算子、文档之间的关系。
- **全部记录**：查询分析、文档生成、审核和 Jira 创建事件。

## 项目结构

```text
app.py                       命令行入口
workbench/analyzer.py        vLLM 快照、Git diff 和算子识别
workbench/classification.py  设备范围分类
workbench/evidence.py        文档证据收集
workbench/documents.py       离线/模型文档生成
workbench/database.py        SQLite 存储与追踪
workbench/server.py          Web 服务和 API
static/                      深色控制台前端
```

## 当前边界

- “设备无关”来自名称、路径和注册上下文的静态初筛，仍需人工确认。
- “相关修改”表示定义所在文件变化，不代表算子签名或语义一定改变。
- 离线文档是带证据边界的草案，不自动补全未经证实的 shape、并行语义或性能结论。
- Jira 当前为 Mock，需要后续替换为内部 Jira 适配器。
- 完整 Agent 的多步工具调用、运行轨迹和会话记录尚未实现。
