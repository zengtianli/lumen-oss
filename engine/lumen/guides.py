"""Bundled workflow guidance; agents query the product instead of skills."""
from pathlib import Path
import json
from .types import LumenError

ROOT = Path(__file__).resolve().parent / "assets/workflows"


def guide(topic=None):
    manifest = ROOT / "index.json"
    if not manifest.is_file():
        raise LumenError("guides_missing", "生成标准尚未打包，请使用当前完整版本")
    topics = json.loads(manifest.read_text(encoding="utf-8"))
    if isinstance(topics, dict):
        topics = topics.get("topics", [])
    if topic is None:
        return {"topics": topics, "schema_command": "lumen schema --json", "runtime_owner": "Lumen"}
    item = next((item for item in topics if item.get("id") == topic), None)
    if not item:
        raise LumenError("unknown_guide", "未知工作流说明", {"topics": [item["id"] for item in topics]})
    path = (ROOT / item.get("path", topic + ".md")).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise LumenError("invalid_guide", "工作流资源缺失或路径不合法")
    return {"topic": item, "body": path.read_text(encoding="utf-8"), "runtime_owner": "Lumen"}
