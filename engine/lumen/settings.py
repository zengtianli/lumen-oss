"""Runtime defaults and labels: one editable configuration, safe fallback."""
from copy import deepcopy
from pathlib import Path
import json
from .store import atomic_json
from .types import LumenError

DEFAULTS = {
    "default_kind": "portal", "theme": "light", "content_width": "full",
    "font_size": 17, "restore_reading": True,
    "labels": {"portal": "门户", "report": "阅读与报告", "teach": "教学", "library": "知识库", "generate": "生成", "import": "导入", "search": "搜索", "save": "保存", "favorite": "收藏", "notes": "个人笔记", "versions": "版本"},
    "generation": {"timeout_seconds": 600, "max_source_bytes": 240000},
}
GENERATION_LIMITS = {"timeout_seconds": (10, 3600), "max_source_bytes": (1, 400_000)}


def validate_generation_value(key, value, *, error_code="invalid_settings", numeric_timeout=False):
    low, high = GENERATION_LIMITS[key]
    numeric = (int, float) if key == "timeout_seconds" and numeric_timeout else (int,)
    if isinstance(value, bool) or not isinstance(value, numeric) or not low <= value <= high:
        label = "生成时间预算（秒）" if key == "timeout_seconds" else "来源文本预算（字节）"
        raise LumenError(error_code, f"{label}应为 {low} 至 {high}" + ("" if numeric_timeout and key == "timeout_seconds" else " 的整数"))
    return value


def settings(root, values=None):
    path = Path(root) / "settings.json"
    result = deepcopy(DEFAULTS)
    warning = None
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            validate(loaded)
            for key, value in loaded.items():
                result[key] = {**result[key], **value} if isinstance(value, dict) and isinstance(result.get(key), dict) else value
        except (OSError, ValueError, LumenError) as error:
            warning = "配置文件损坏，已使用内置默认值：" + str(error)
    if values is not None:
        validate(values)
        for key, value in values.items():
            result[key] = {**result[key], **value} if isinstance(value, dict) and isinstance(result.get(key), dict) else value
        atomic_json(path, result)
    return {"settings": result, "warning": warning}


def validate(values):
    if not isinstance(values, dict):
        raise LumenError("invalid_settings", "配置必须为对象")
    if set(values) - set(DEFAULTS):
        raise LumenError("invalid_settings", "配置包含未知字段")
    if "font_size" in values and (not isinstance(values["font_size"], int) or not 12 <= values["font_size"] <= 30):
        raise LumenError("invalid_settings", "字号范围为12至30")
    if "default_kind" in values and values["default_kind"] not in ("portal", "report", "teach"):
        raise LumenError("invalid_settings", "未知生成用途")
    if "theme" in values and values["theme"] not in ("light", "system", "dark"):
        raise LumenError("invalid_settings", "未知外观")
    if "labels" in values and (not isinstance(values["labels"], dict) or any(not isinstance(value, str) for value in values["labels"].values())):
        raise LumenError("invalid_settings", "界面词表必须为文字")
    if "restore_reading" in values and not isinstance(values["restore_reading"], bool):
        raise LumenError("invalid_settings", "恢复阅读位置必须为布尔值")
    if "generation" in values:
        if not isinstance(values["generation"], dict):
            raise LumenError("invalid_settings", "生成设置必须为对象")
        for key, value in values["generation"].items():
            if key not in GENERATION_LIMITS:
                raise LumenError("invalid_settings", "生成设置包含未知预算字段")
            validate_generation_value(key, value)


def export_config(root, output):
    path = Path(output).expanduser().resolve()
    if path.exists():
        raise LumenError("file_exists", "配置导出目标已存在")
    atomic_json(path, {"schema_version": 1, "product": "cyou.tianli.Lumen", "values": settings(root)["settings"]})
    return {"path": str(path)}


def import_config(root, source):
    try:
        data = json.loads(Path(source).expanduser().read_text(encoding="utf-8"))
        if data.get("product") != "cyou.tianli.Lumen" or data.get("schema_version") != 1:
            raise ValueError("不属于Lumen配置")
        validate(data["values"])
    except (OSError, ValueError, KeyError) as error:
        raise LumenError("invalid_settings", "配置导入失败，现有设置未改变", {"reason": str(error)}) from error
    current = Path(root) / "settings.json"
    if current.exists():
        backup = current.with_name("settings.backup.json")
        backup.write_bytes(current.read_bytes())
    return settings(root, data["values"])
