import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


REPO_URL = "https://github.com/vllm-project/vllm"
EXTRACTOR_VERSION = "demo-extractor-v2"
SOURCE_EXTENSIONS = {".py", ".cc", ".cpp", ".cu", ".cuh", ".h", ".hpp", ".c"}


@dataclass(frozen=True)
class Definition:
    name: str
    kind: str
    line: int
    evidence: str
    confidence: float
    is_work_item: int = 1


class VllmRepository:
    def __init__(self, path):
        self.path = Path(path)

    def git(self, *args):
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=True,
            capture_output=True,
        )
        return result.stdout.decode("utf-8", errors="replace")

    def ensure(self, base_version, target_version):
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "--bare", str(self.path)], check=True, capture_output=True)
            self.git("remote", "add", "origin", f"{REPO_URL}.git")
        refs = [f"refs/tags/{base_version}", f"refs/tags/{target_version}"]
        missing = []
        for version, ref in zip((base_version, target_version), refs):
            try:
                self.git("rev-parse", "--verify", ref)
            except subprocess.CalledProcessError:
                missing.append(f"{ref}:{ref}")
        if missing:
            self.git("fetch", "--depth=1", "origin", *missing)

    def revision(self, version):
        return self.git("rev-parse", f"refs/tags/{version}^{{commit}}").strip()

    def show(self, revision, path):
        if not path:
            return ""
        try:
            return self.git("show", f"{revision}:{path}")
        except subprocess.CalledProcessError:
            return ""

    def load_texts(self, revision, paths):
        paths = sorted(set(path for path in paths if path))
        if not paths:
            return {}
        request = "".join(f"{revision}:{path}\n" for path in paths).encode()
        result = subprocess.run(
            ["git", "-C", str(self.path), "cat-file", "--batch"],
            input=request,
            check=True,
            capture_output=True,
        )
        output = result.stdout
        texts = {}
        offset = 0
        for path in paths:
            header_end = output.index(b"\n", offset)
            header = output[offset:header_end].decode()
            if header.endswith(" missing"):
                texts[path] = ""
                offset = header_end + 1
                continue
            size = int(header.rsplit(" ", 1)[1])
            start = header_end + 1
            texts[path] = output[start:start + size].decode("utf-8", errors="replace")
            offset = start + size + 1
        return texts

    def search(self, revision, needle, limit=60):
        result = subprocess.run(
            ["git", "-C", str(self.path), "grep", "-n", "-F", needle, revision, "--",
             "*.py", "*.cc", "*.cpp", "*.cu", "*.cuh", "*.h", "*.hpp"],
            capture_output=True,
        )
        if result.returncode not in (0, 1):
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
        matches = []
        for line in result.stdout.decode("utf-8", errors="replace").splitlines()[:limit]:
            parts = line.split(":", 3)
            if len(parts) == 4:
                matches.append({"path": parts[1], "line": int(parts[2]), "text": parts[3]})
        return matches

    def changed_files(self, base_sha, target_sha):
        output = self.git("diff", "--name-status", "-M", base_sha, target_sha)
        changes = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0]
            old_path = parts[1]
            new_path = parts[2] if status.startswith("R") and len(parts) > 2 else old_path
            if Path(new_path).suffix in SOURCE_EXTENSIONS or Path(old_path).suffix in SOURCE_EXTENSIONS:
                changes.append((status, old_path, new_path))
        return changes

    def file_diff(self, base_sha, target_sha, old_path, new_path):
        output = self.git("diff", "--unified=3", base_sha, target_sha, "--", old_path, new_path)
        if len(output) > 24000:
            return output[:24000] + "\n... diff truncated for demo ..."
        return output


def extract_definitions(text, path):
    definitions = []
    seen = set()

    def add(definition):
        key = (definition.name, definition.kind, definition.line)
        if key not in seen:
            seen.add(key)
            definitions.append(definition)

    suffix = Path(path).suffix
    if suffix == ".py":
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if "@tilelang.jit" not in line and not re.search(r"@tilelang_jit\b", line):
                continue
            for following in range(index + 1, min(index + 10, len(lines))):
                match = re.match(r"\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", lines[following])
                if match:
                    add(Definition(match.group(1), "tilelang_kernel", following + 1,
                                   "发现 @tilelang.jit 或 vLLM @tilelang_jit 装饰的 kernel 定义", 0.95))
                    break

        for match in re.finditer(
            r"@torch\.library\.custom_op\(\s*['\"]([A-Za-z_]\w*(?:::[A-Za-z_]\w*)?)['\"]",
            text,
        ):
            add(Definition(match.group(1), "python_custom_op", text.count("\n", 0, match.start()) + 1,
                           "发现 torch.library.custom_op 注册", 0.98))

        for match in re.finditer(
            r"direct_register_custom_op\(\s*op_name\s*=\s*['\"]([^'\"]+)['\"]",
            text,
        ):
            add(Definition(match.group(1), "python_custom_op", text.count("\n", 0, match.start()) + 1,
                           "发现 direct_register_custom_op 注册", 0.95))

    if suffix in {".cc", ".cpp", ".cu", ".cuh", ".h", ".hpp", ".c"}:
        namespaces = re.findall(r"TORCH_LIBRARY(?:_FRAGMENT)?\(\s*([A-Za-z_]\w*)\s*,", text)
        if namespaces:
            namespace = namespaces[0]
            for match in re.finditer(r"\bm\.def\(\s*['\"]([A-Za-z_]\w*)", text):
                add(Definition(f"{namespace}::{match.group(1)}", "torch_library_op",
                               text.count("\n", 0, match.start()) + 1,
                               f"发现 TORCH_LIBRARY({namespace}) 中的 m.def 定义", 0.98))

    for match in re.finditer(r"torch\.ops\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)", text):
        add(Definition(f"{match.group(1)}::{match.group(2)}", "torch_call_site",
                       text.count("\n", 0, match.start()) + 1,
                       "仅发现 torch.ops 调用点，不代表该版本新增算子定义", 0.9, 0))

    return definitions


def excerpt(text, line, radius=18):
    if not text:
        return ""
    lines = text.splitlines()
    if not line:
        return "\n".join(lines[: radius * 2 + 1])[:12000]
    start = max(0, line - radius - 1)
    end = min(len(lines), line + radius)
    numbered = [f"{index + 1:5d}  {lines[index]}" for index in range(start, end)]
    return "\n".join(numbered)[:12000]


def definition_key(definition):
    return definition.name, definition.kind


def analyze(repository, base_sha, target_sha, progress=None):
    results = []
    changes = repository.changed_files(base_sha, target_sha)
    old_texts = repository.load_texts(
        base_sha, [old_path for status, old_path, _ in changes if status != "A"]
    )
    new_texts = repository.load_texts(
        target_sha, [new_path for status, _, new_path in changes if status != "D"]
    )
    for index, (status, old_path, new_path) in enumerate(changes):
        if progress and index % 500 == 0:
            progress(index, len(changes))
        old_text = "" if status == "A" else old_texts.get(old_path, "")
        new_text = "" if status == "D" else new_texts.get(new_path, "")
        old_definitions = {definition_key(item): item for item in extract_definitions(old_text, old_path)}
        new_definitions = {definition_key(item): item for item in extract_definitions(new_text, new_path)}
        keys = set(old_definitions) | set(new_definitions)
        if not keys:
            continue
        diff = repository.file_diff(base_sha, target_sha, old_path, new_path)

        for key in sorted(keys):
            old = old_definitions.get(key)
            new = new_definitions.get(key)
            if new and not old:
                change_type, current, path, text = "added", new, new_path, new_text
                evidence = f"目标版本出现新的定义；{new.evidence}"
                confidence = new.confidence
            elif old and not new:
                change_type, current, path, text = "removed", old, old_path, old_text
                evidence = f"目标快照中该定义消失；{old.evidence}"
                confidence = old.confidence
            else:
                change_type, current, path, text = "modified", new, new_path, new_text
                evidence = f"定义所在文件有变化，需进一步确认签名或实现是否变化；{new.evidence}"
                confidence = min(new.confidence, 0.65)
            results.append({
                "name": current.name,
                "kind": current.kind,
                "change_type": change_type,
                "path": path,
                "line": current.line,
                "source": excerpt(text, current.line),
                "diff": diff,
                "evidence": evidence,
                "confidence": confidence,
                "is_work_item": current.is_work_item,
                "metadata": {
                    "file_status": status,
                    "old_path": old_path,
                    "new_path": new_path,
                    "old_line": old.line if old else 0,
                    "target_line": new.line if new else 0,
                },
            })
    return results
