"""Bounded read-only discovery using the maintained deliverables index reader."""
import importlib
import json
from pathlib import Path
import re
import sys
import time

from .store import Store
from .types import LumenError


def reader():
    try:
        return importlib.import_module("deliverables_index")
    except ModuleNotFoundError as error:
        if error.name != "deliverables_index":
            raise
    raise LumenError("reader_missing", "The bundled HTML reader is missing")


def discover(state_dir=None, roots=None, limit=100):
    if roots is None:
        home = Path.home()
        roots = [str(home / "Documents/Lumen")]
    if not isinstance(roots, list) or not 1 <= len(roots) <= 10 or any(not isinstance(root, str) for root in roots):
        raise LumenError("invalid_roots", "请提供1至10个资料目录")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise LumenError("invalid_limit", "候选数量范围为1至1000")
    source_reader = reader()
    registered = {doc.get("source_path"): doc for doc in Store(state_dir).list(archived=None)["documents"] if doc.get("source_path")}
    candidates, missing, skipped = [], [], []
    seen = set()
    deadline = time.monotonic() + 5
    visited = 0
    truncated = False
    for requested in roots:
        root = Path(requested).expanduser().resolve()
        if not root.is_dir():
            missing.append(str(root))
            continue
        pending = [(root, 0)]
        while pending:
            folder, depth = pending.pop(0)
            if time.monotonic() >= deadline or visited >= 250 or len(candidates) >= limit:
                truncated = True
                break
            visited += 1
            index = folder / "index.html"
            structured = index.is_file() and (folder / "content.json").is_file()
            bucket = {"dir": folder, "glob": "index.html" if index.is_file() else "*.html", "key": "knowledge"}
            for entry in source_reader.scan_bucket(bucket, check_share=False):
                path = entry["path"].resolve()
                if path in seen or entry["path"].is_symlink():
                    continue
                seen.add(path)
                if re.search(r"pelican|鹈鹕|svg[-_ ]?test", path.name + " " + entry["title"], re.I) or path.name in ("template.html", "graph-view.html"):
                    skipped.append(str(path))
                    continue
                document = registered.get(str(path))
                page_count = None
                if structured:
                    try:
                        page_count = len(json.loads((folder / "content.json").read_text())["pages"])
                    except (OSError, ValueError, KeyError):
                        pass
                candidates.append({"path": str(path), "title": entry["title"], "kind": "portal" if index.is_file() else "report", "project": folder.name, "registered": document is not None, "document_id": document["id"] if document else None, "page_count": page_count, "modified_at": entry["mtime_iso"], "bytes": entry["size"]})
                if len(candidates) >= limit:
                    truncated = True
                    break
            if depth < 2 and not index.is_file():
                try:
                    children = [child for child in folder.iterdir() if child.is_dir() and not child.is_symlink() and not child.name.startswith(".") and child.name not in source_reader.PROJECT_EXCLUDE_DIRS | {"tests", "fixtures", "test", "sessions", "_archive", "source-before", "acceptance", "raw"}]
                    children.sort(key=lambda child: child.stat().st_mtime, reverse=True)
                    pending.extend((child, depth + 1) for child in children)
                except OSError:
                    missing.append(str(folder))
        if truncated:
            break
    candidates.sort(key=lambda item: item["modified_at"], reverse=True)
    return {"candidates": candidates, "scope": {"roots": roots, "max_depth": 2, "visited_directories": visited, "deadline_seconds": 5, "portal_entries_only": True}, "missing_roots": missing, "skipped_test_or_template": skipped, "truncated": truncated}
