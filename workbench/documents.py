import hashlib
import json
import os
import urllib.request

from workbench.evidence import collect_evidence


CHANGE_LABELS = {"added": "新增", "modified": "相关文件修改", "removed": "移除"}
KIND_LABELS = {
    "torch_library_op": "Torch Library 自定义算子",
    "python_custom_op": "Python 自定义算子",
    "tilelang_kernel": "TileLang Kernel",
    "torch_call_site": "Torch 算子调用点",
}


def source_hash(operator):
    payload = "\n".join([
        operator["target_sha"], operator["name"], operator["path"],
        operator["source"], operator["diff"],
    ])
    return hashlib.sha256(payload.encode()).hexdigest()


def offline_document(operator, evidence):
    name = operator["name"]
    change = CHANGE_LABELS.get(operator["change_type"], operator["change_type"])
    kind = KIND_LABELS.get(operator["kind"], operator["kind"])
    schema = evidence["schema"] if evidence["schema"] != "待确认" else "待确认"
    references = evidence["references"]
    reference_rows = "\n".join(
        f"| `{item['path']}:{item['line']}` | {'测试' if item['is_test'] else '实现/调用'} | [固定源码]({item['url']}) |"
        for item in references
    ) or "| 待确认 | 未收集到额外引用 | — |"
    reference_blocks = "\n\n".join(
        f"### `{item['path']}:{item['line']}`\n\n```text\n{item['snippet']}\n```"
        for item in references[:5]
    ) or "待确认。"
    return f"""# `{name}`：vLLM 算子适配需求

> 本文是由固定版本源码证据生成的 Engram 风格草案。未从源码确认的内容统一标为“待确认”，不构成硬件适配或性能结论。

## 0. 范围

- **包含**：`{name}` 的定义、接口证据、相关调用与测试、国产芯片适配和验收事项。
- **算子类型**：{kind}。
- **版本变化**：vLLM `{operator['base_version']}` → `{operator['target_version']}`，{change}。
- **不包含**：上游/下游独立算子的融合设计、未经验证的性能收益和具体芯片实现方案。
- **范围判断**：{operator['scope_reason']}

## 1. 现有实现与位置

识别依据：{operator['evidence']}

主定义位于 `{operator['path']}:{operator['line']}`，[查看固定版本源码]({operator['source_url']})。

```text
输入 Tensor / 参数（shape 待确认）
        |
        v
+----------------------+
| {name}               |
+----------------------+
        |
        v
输出 Tensor / 副作用（以 schema 和函数体为准）
```

## 2. 函数签名 / Schema

### Python 签名

```python
{evidence['signature']}
```

### C++ Schema

```cpp
{schema}
```

## 3. 输入输出规格

| 项目 | 形状 | dtype / 类型 | 依据 |
|---|---|---|---|
| 输入参数 | 待确认 | 见签名或 schema；未标注部分待确认 | 固定版本定义 |
| 输出 | 待确认 | 待确认 | 需结合函数体、fake/meta 和调用方 |
| 可选参数及默认值 | 待确认 | 见签名或 schema | 固定版本定义 |
| 副作用 / mutates_args | 待确认 | — | 需检查 decorator、schema 和实现 |

## 4. 常量、精度与并行契约

- 数值累加精度、epsilon、量化 scale：**待确认**。
- 动态 shape、非连续 Tensor 和空输入支持：**待确认**。
- TP / SP / DP 下每个 rank 持有的 shape 及通信要求：**待确认**。
- CUDA Graph、buffer 地址稳定性和原地写要求：**待确认**。

## 5. 参考实现

```python
{evidence['definition_source']}
```

## 6. 相关调用、Fake/Meta 与测试证据

共保留 {len(references)} 条引用；出现顺序不代表运行时调用顺序。

{reference_blocks}

## 7. 国产芯片适配事项

- [ ] 依据签名、schema 和 fake/meta 补齐输入输出及 shape/dtype 契约。
- [ ] 确认通用接口与具体设备实现的边界，避免把 CUDA/ROCm 专属行为误写为通用语义。
- [ ] 检查动态 shape、非连续输入、混合精度、空输入和原地修改。
- [ ] 确认注册、dispatch、fake/meta 和 CUDA Graph 行为在目标后端一致。
- [ ] 关联适配 PR、验证报告和 Jira；性能收益必须实测，不在本文预设。

## 8. 待确认

| # | 事项 | 影响 |
|---|---|---|
| 1 | 完整输入输出 shape、dtype 与 stride 约束 | 决定目标后端接口是否兼容 |
| 2 | fake/meta、注册和分发路径 | 决定编译及动态 shape 行为 |
| 3 | 并行切分和通信依赖 | 决定各 rank 的输入输出形态 |
| 4 | 参考输出、误差阈值和性能基线 | 决定验收标准 |

## 9. 验收建议

### 9.1 数值

1. 以固定 commit 的原生后端或独立参考实现为 golden。
2. 覆盖典型、空输入、极值、非连续和动态 shape。
3. 明确每种 dtype 的误差标准；量化路径不得默认要求与非量化路径逐位一致。

### 9.2 功能与并行

1. 验证 fake/meta、注册、dispatch 和图捕获行为。
2. 覆盖相关 TP/SP/DP 配置，记录每个 rank 的实际 shape。
3. 如存在原地写或掩码，验证所有输出元素均被确定性写入。

### 9.3 性能

1. 分开记录算子正确性与性能结果。
2. 固定批量、shape、精度和硬件，对比适配前后延迟及带宽。
3. 不在没有测量时声称 launch、显存或带宽收益。

## 10. 参考位置

| 位置 | 类型 | 链接 |
|---|---|---|
| `{operator['path']}:{operator['line']}` | 主定义 | [固定源码]({operator['source_url']}) |
{reference_rows}

目标 commit：`{evidence['revision']}`。本文源码哈希：`{source_hash(operator)}`。
"""


def kimi_document(operator, evidence):
    api_key = os.environ.get("KIMI_API_KEY")
    model = os.environ.get("KIMI_MODEL")
    if not api_key or not model:
        return offline_document(operator, evidence), "offline-engram-v2"
    base_url = os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1").rstrip("/")
    prompt = f"""请根据固定版本的源码证据，生成一份中文 vLLM 算子适配需求文档，风格参考严谨的 Engram 算子规格。
禁止编造源码中不存在的 shape、dtype、语义、并行行为或性能收益；无法确认的字段明确写“待确认”。
章节必须包括：0 范围；1 现有实现与调用链；2 函数签名；3 输入输出规格；4 常量/精度/副作用/并行契约；5 算子语义；6 参考实现；7 国产芯片适配事项；8 待确认；9 数值/功能/性能验收；10 参考位置。
所有源码引用使用固定 commit 链接。

算子：{operator['name']}
版本：{operator['base_version']} -> {operator['target_version']}
变化：{operator['change_type']}
范围分类：{operator['scope_reason']}
主源码：{operator['source_url']}

证据：
{json.dumps(evidence, ensure_ascii=False)[:48000]}

Git diff：
{operator['diff'][:16000]}
"""
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": "你是严谨的 AI 编译与算子适配工程师。"},
                {"role": "user", "content": prompt},
            ],
        }).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.load(response)
    content = payload["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content)
    return content, f"kimi:{model}"


def generate_document(database, operator_id, repository=None):
    operator = database.get_operator(operator_id)
    if operator is None:
        raise ValueError("算子不存在")
    if not operator["is_work_item"]:
        raise ValueError("调用点不是独立适配工作项，无需生成算子文档")
    if operator["document_id"]:
        return operator, False
    evidence = collect_evidence(repository, operator)
    content, generator = kimi_document(operator, evidence)
    database.create_document(operator_id, content, generator, source_hash(operator))
    return database.get_operator(operator_id), True
