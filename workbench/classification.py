import re


DEVICE_MARKERS = {
    "cuda": "CUDA",
    "rocm": "ROCm",
    "hip": "HIP",
    "xpu": "XPU",
    "tpu": "TPU",
    "mps": "MPS",
    "cpu": "CPU",
    "amd": "AMD",
    "nvidia": "NVIDIA",
    "aiter": "AITER",
    "flashinfer": "FlashInfer",
    "sm90": "SM90",
    "sm100": "SM100",
    "rdna": "RDNA",
    "neuron": "Neuron",
    "ascend": "Ascend",
}


def classify_operator(item):
    name = item["name"].lower()
    path = item["path"].lower()
    kind = item["kind"]
    if kind == "torch_call_site":
        return "call_site", "仅是 torch.ops 调用点，不作为独立算子工作项"
    if kind == "tilelang_kernel":
        return "tilelang", "TileLang kernel 按你的要求单独纳入，不代表设备无关"
    if path.startswith("tests/") or "/tests/" in path:
        return "test_only", "测试代码中的定义，不进入默认适配清单"

    searchable = f"{name} {path}"
    for marker, label in DEVICE_MARKERS.items():
        if re.search(rf"(?:^|[^a-z0-9]){re.escape(marker)}(?:[^a-z0-9]|$)", searchable):
            return "device_specific", f"名称或路径显式关联 {label}，默认排除"

    if kind == "python_custom_op":
        return "general_custom_op", "通用 Python custom_op，未发现显式设备绑定；仍需人工确认"
    if kind == "torch_library_op" and item["name"].startswith("_C::"):
        return "general_custom_op", "通用 _C 注册，名称和路径未发现显式设备绑定；仍需人工确认"
    if kind == "torch_library_op":
        return "other_custom_op", "非通用 _C 命名空间，默认作为关联证据保留"
    return "other_custom_op", "未进入默认适配范围"


def with_classification(row):
    result = dict(row)
    scope, reason = classify_operator(result)
    result["scope"] = scope
    result["scope_reason"] = reason
    result["is_default_work_item"] = scope in {"general_custom_op", "tilelang"}
    return result
