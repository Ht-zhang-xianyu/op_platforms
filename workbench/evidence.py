import ast
import re

from workbench.analyzer import excerpt


def python_definition(text, line):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "", "待确认（Python 解析失败）"
    lines = text.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.lineno != line:
            continue
        start = min([node.lineno] + [item.lineno for item in node.decorator_list])
        source = "\n".join(lines[start - 1:node.end_lineno])[:16000]
        arguments = ast.unparse(node.args)
        returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        signature = f"def {node.name}({arguments}){returns}"
        return source, signature
    return "", "待确认（未定位到完整函数定义）"


def cpp_schema(text, name):
    short_name = name.split("::")[-1]
    match = re.search(rf"\bm\.def\(\s*['\"]{re.escape(short_name)}[^;]+;", text, re.S)
    return match.group(0)[:5000] if match else "待确认（未提取到完整 schema）"


def collect_evidence(repository, operator):
    revision = operator["base_sha"] if operator["change_type"] == "removed" else operator["target_sha"]
    text = repository.show(revision, operator["path"]) if repository else ""
    definition_source = operator["source"]
    signature = "待确认"
    schema = "待确认"
    if operator["kind"] in {"python_custom_op", "tilelang_kernel"}:
        definition_source, signature = python_definition(text, operator["line"])
    elif operator["kind"] == "torch_library_op":
        schema = cpp_schema(text, operator["name"])

    references = []
    if repository:
        short_name = operator["name"].split("::")[-1]
        matches = repository.search(revision, short_name)
        matches = [item for item in matches
                   if not (item["path"] == operator["path"] and item["line"] == operator["line"])]
        matches.sort(key=lambda item: (
            not item["path"].startswith("tests/"),
            "fake" not in item["path"].lower() and "fake" not in item["text"].lower(),
            item["path"] != operator["path"],
        ))
        selected = matches[:10]
        texts = repository.load_texts(revision, [item["path"] for item in selected])
        for item in selected:
            references.append({
                **item,
                "snippet": excerpt(texts.get(item["path"], ""), item["line"], 5),
                "url": f"{operator['repo']}/blob/{revision}/{item['path']}#L{item['line']}",
                "is_test": item["path"].startswith("tests/"),
            })
    return {
        "revision": revision,
        "definition_source": definition_source or operator["source"],
        "signature": signature,
        "schema": schema,
        "references": references,
        "reference_count": len(references),
    }
