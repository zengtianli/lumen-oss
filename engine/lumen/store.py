"""Transactional, portable HTML documents. The SQLite index is a derived view."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from urllib.parse import quote, unquote, urljoin, urlsplit
from html import escape as html_escape

from .types import LumenError


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return deepcopy(default)
    except (ValueError, OSError) as error:
        raise LumenError("corrupt_file", "知识库文件无法读取", {"path": str(path), "reason": str(error)})


def portable_snapshot_base(markup, old_site, page_relative):
    """Rebase only managed file bases; snapshots must survive ZIP extraction."""
    def base_tag(match):
        tag = match.group(0)
        href = re.search(r'\bhref\s*=\s*([\"\'])(.*?)\1', tag, re.I | re.S)
        if not href:
            return tag
        parsed = urlsplit(href[2])
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            return tag
        target = Path(unquote(parsed.path)).resolve()
        try:
            target.relative_to(old_site.resolve())
        except ValueError:
            return tag
        relative = os.path.relpath(target, (old_site / page_relative).parent)
        value = quote(relative, safe="/.") + ("/" if parsed.path.endswith("/") else "")
        if parsed.query: value += "?" + parsed.query
        if parsed.fragment: value += "#" + parsed.fragment
        return tag[:href.start(2)] + html_escape(value, quote=True) + tag[href.end(2):]
    return re.sub(r"<base\b[^>]*>", base_tag, markup, flags=re.I)


def default_state_dir():
    return Path(os.environ.get("LUMEN_STATE_DIR", str(Path.home() / "Library/Application Support/Lumen"))).expanduser().resolve()


@contextmanager
def library_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / ".write.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


_MISSING = object()


def _copy(value):
    return _MISSING if value is _MISSING else deepcopy(value)


def metadata_revision(existing, incoming):
    """All writers share the same revision for changed library properties."""
    if existing is None:
        return 0
    fields = ("title", "project", "tags", "notes", "favorite", "archived", "source_path", "sources", "trashed", "deleted_at")
    changed = any(existing.get(field) != incoming.get(field) for field in fields)
    return existing.get("metadata_revision", 0) + int(changed)


def merge_content(base, current, incoming, path="", conflicts=None):
    """Three-way merge keyed page/block collections; never discard human edits."""
    conflicts = [] if conflicts is None else conflicts
    if current == base:
        return _copy(incoming), conflicts
    if incoming == base or current == incoming:
        return _copy(current), conflicts
    if isinstance(base, dict) and isinstance(current, dict) and isinstance(incoming, dict):
        result = {}
        for key in sorted(set(base) | set(current) | set(incoming)):
            merged, conflicts = merge_content(base.get(key, _MISSING), current.get(key, _MISSING), incoming.get(key, _MISSING), path + "/" + str(key), conflicts)
            if merged is not _MISSING:
                result[key] = merged
        return result, conflicts
    if all(isinstance(value, list) for value in (base, current, incoming)):
        keyed = all(all(isinstance(item, dict) and item.get("id") for item in value) for value in (base, current, incoming))
        if keyed:
            maps = [{item["id"]: item for item in value} for value in (base, current, incoming)]
            order = list(dict.fromkeys([item["id"] for item in incoming] + [item["id"] for item in current]))
            result = []
            for key in order:
                merged, conflicts = merge_content(*(mapping.get(key, _MISSING) for mapping in maps), path + "/@" + key, conflicts)
                if merged is not _MISSING:
                    result.append(merged)
            return result, conflicts
        if len(base) == len(current) == len(incoming):
            result = []
            for index in range(len(base)):
                merged, conflicts = merge_content(base[index], current[index], incoming[index], path + "/" + str(index), conflicts)
                result.append(merged)
            return result, conflicts
    conflicts.append({"path": path or "/", "base": None if base is _MISSING else base, "current": None if current is _MISSING else current, "incoming": None if incoming is _MISSING else incoming})
    return current if current is _MISSING else deepcopy(current), conflicts


def apply_patches(content, patches):
    result = deepcopy(content)
    if not isinstance(patches, list) or len(patches) > 10000:
        raise LumenError("invalid_patches", "修改必须为有限的字段列表")
    for patch in patches:
        pointer = patch.get("path", "")
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            raise LumenError("invalid_patch", "修改缺少有效字段路径")
        keys = [key.replace("~1", "/").replace("~0", "~") for key in pointer[1:].split("/")]
        if any(key in ("__proto__", "constructor", "prototype") for key in keys):
            raise LumenError("invalid_patch", "修改路径不被允许")
        parent = result
        try:
            for key in keys[:-1]:
                parent = parent[int(key)] if isinstance(parent, list) else parent[key]
            key = int(keys[-1]) if isinstance(parent, list) else keys[-1]
            original = parent[key]
            if "original" in patch and original != patch["original"]:
                raise LumenError("conflict", "这一字段已经变化，请重新载入后比较", {"path": pointer, "current": original, "original": patch["original"]})
            # Renderer source fields are textual; IDs, navigation, executable code,
            # scientific inputs and whole trees must go through explicit build.
            value = patch.get("value")
            textual = isinstance(original, str) and isinstance(value, str)
            numeric = isinstance(original, (int, float)) and not isinstance(original, bool) and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            if not textual and not numeric:
                raise LumenError("invalid_patch", "编辑值必须保持文字或有限数字类型", {"path": pointer})
            if keys[-1] in ("id", "type", "parent", "home", "section", "page", "path", "url", "href", "language"):
                raise LumenError("invalid_patch", "结构字段应通过内容构建接口修改", {"path": pointer})
            parent[key] = patch["value"]
        except (IndexError, KeyError, ValueError, TypeError) as error:
            raise LumenError("invalid_patch", "修改字段已不存在", {"path": pointer}) from error
    return result


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []
        self.anchor = ""
        self.segments = []
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("script", "style", "nav"):
            self.skip += 1
        if tag == "title":
            self.in_title = True
        target_anchor = attributes.get("data-feedback-block") or attributes.get("id")
        if not self.skip and target_anchor:
            self.flush()
            self.anchor = target_anchor
        if tag in ("p", "li", "td", "th", "h1", "h2", "h3", "h4", "pre", "br"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav"):
            self.skip = max(0, self.skip - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)

    def flush(self):
        text = re.sub(r"\s+", " ", " ".join(self.parts)).strip()
        if text:
            self.segments.append((self.anchor, text))
        self.parts = []

    def finish(self):
        self.flush()
        return self.title.strip(), self.segments


def parse_page(path):
    parser = PageText()
    parser.feed(Path(path).read_text(encoding="utf-8"))
    return parser.finish()


def css_unescape(value):
    def replace(match):
        escaped = match[1]
        if re.fullmatch(r"[0-9a-fA-F]{1,6}", escaped.strip()):
            codepoint = int(escaped.strip(), 16)
            return chr(codepoint) if 0 < codepoint <= 0x10ffff and not 0xd800 <= codepoint <= 0xdfff else "\ufffd"
        return "" if escaped in ("\n", "\r", "\r\n", "\f") else escaped
    return re.sub(r"\\([0-9a-fA-F]{1,6}(?:\r\n|\s)?|\r\n|[\s\S])", replace, value)


def css_resources(markup):
    """Tokenize URLs/import strings while skipping comments and other strings."""
    string = r'''(?:"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*')'''
    # Removing comments only outside strings also supports url(/* note */'a').
    markup = re.sub(r"/\*[\s\S]*?\*/|" + string,
                    lambda match: " " if match[0].startswith("/*") else match[0], markup)
    token = re.compile(r"(?P<url>(?<![-\w])url\s*\(\s*(?:" + string + r"|(?:\\[\s\S]|[^)\\])*?)\s*\))|(?P<import>@import\s+" + string + r")|" + string, re.I)
    links = []
    for match in token.finditer(markup):
        if match.lastgroup == "url":
            value = re.sub(r"^url\s*\(\s*|\s*\)$", "", match[0], flags=re.I).strip()
        elif match.lastgroup == "import":
            value = re.sub(r"^@import\s+", "", match[0], flags=re.I)
        else:
            continue
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        if value:
            links.append(css_unescape(value))
    return links


def srcset_resources(value):
    """Read candidate URLs without splitting commas inside data URLs."""
    links = []
    offset = 0
    while offset < len(value):
        while offset < len(value) and (value[offset].isspace() or value[offset] == ","):
            offset += 1
        start = offset
        while offset < len(value) and not value[offset].isspace():
            offset += 1
        url = value[start:offset]
        if not url:
            break
        links.append(url.rstrip(","))
        if url.endswith(","):
            continue
        parentheses = 0
        while offset < len(value):
            char = value[offset]
            offset += 1
            if char == "(": parentheses += 1
            elif char == ")": parentheses = max(0, parentheses - 1)
            elif char == "," and not parentheses: break
    return links


class ResourceLinks(HTMLParser):
    """Read real attributes, never mistake JavaScript assignments for assets."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.base = None
        self.in_style = False
        self.style_parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "base":
            if self.base is None and attributes.get("href") is not None:
                self.base = attributes["href"]
            return
        self.links.extend(attributes[name] for name in ("href", "src", "poster") if attributes.get(name))
        if attributes.get("srcset"):
            self.links.extend(srcset_resources(attributes["srcset"]))
        if attributes.get("style"):
            self.links.extend(css_resources(attributes["style"]))
        if tag == "style":
            self.in_style = True
            self.style_parts = []

    def handle_data(self, data):
        if self.in_style:
            self.style_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "style" and self.in_style:
            self.links.extend(css_resources("".join(self.style_parts)))
            self.in_style = False
            self.style_parts = []


class Store:
    def __init__(self, state_dir=None):
        self.root = Path(state_dir).expanduser().resolve() if state_dir else default_state_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.db_path = self.root / "library.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS page_index(document_id TEXT, page_id TEXT, title TEXT, relative_path TEXT, anchor TEXT, text TEXT);
                CREATE INDEX IF NOT EXISTS page_doc ON page_index(document_id);
                CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY, document_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS positions(document_id TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _labels(self):
        from .settings import settings
        return settings(self.root)["settings"]["labels"]

    def document_root(self, document_id):
        if not isinstance(document_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", document_id):
            raise LumenError("invalid_id", "文档编号无效")
        return self.root / "documents" / document_id

    def version_root(self, document_id, version):
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise LumenError("invalid_version", "版本号必须为正整数")
        return self.document_root(document_id) / "versions" / str(version)

    def document(self, document_id):
        self.document_root(document_id)
        if (self.root / "purged" / (document_id + ".json")).exists():
            raise LumenError("not_found", "这份文档已彻底删除", {"document_id": document_id})
        with self.connect() as db:
            row = db.execute("SELECT metadata FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise LumenError("not_found", "知识库中没有这份成果", {"document_id": document_id})
        document = json.loads(row["metadata"])
        document.setdefault("metadata_revision", 0)
        document.setdefault("trashed", False)
        document.setdefault("deleted_at", None)
        return document

    def check_version(self, document, expected_version):
        if document.get("trashed"):
            raise LumenError("document_trashed", "文档在废纸篓中，请先恢复再修改")
        if expected_version is None or expected_version != document["version"]:
            raise LumenError("conflict", "成果已经更新，请比较当前版本后再保存", {"expected_version": expected_version, "current_version": document["version"]})

    def get(self, document_id, version=None):
        document = self.document(document_id)
        if version is not None and version != document["version"]:
            historic = read_json(self.version_root(document_id, version) / "metadata.json")
            if historic is None:
                raise LumenError("not_found", "指定历史版本不存在")
            personal = {key: document[key] for key in ("title", "favorite", "archived", "notes", "project", "tags", "source_path", "sources", "metadata_revision", "title_override", "trashed", "deleted_at") if key in document}
            document = {**historic, **personal}
        folder = self.version_root(document_id, document["version"])
        pages = read_json(folder / "pages.json", [])
        for page in pages:
            page.update(absolute_path=str(folder / "site" / page["relative_path"]), version=document["version"], document_id=document_id)
        return {"document": document, "pages": pages, "content": read_json(folder / "content.json"), "feedback": read_json(folder / "feedback.json", {}), "notes": document.get("notes", "")}

    def list(self, query=None, project=None, kind=None, tag=None, favorite=None, archived=False, trashed=False, sort="updated", descending=True):
        with self.connect() as db:
            rows = db.execute("SELECT metadata FROM documents").fetchall()
        documents = [json.loads(row["metadata"]) for row in rows]
        for document in documents:
            document.setdefault("metadata_revision", 0)
            document.setdefault("trashed", False)
            document.setdefault("deleted_at", None)
        documents = [doc for doc in documents if not (self.root / "purged" / (doc["id"] + ".json")).exists()]
        if trashed is not None:
            documents = [doc for doc in documents if doc["trashed"] == bool(trashed)]
        if archived is not None:
            documents = [doc for doc in documents if doc["archived"] == bool(archived)]
        if favorite is not None:
            documents = [doc for doc in documents if doc["favorite"] == bool(favorite)]
        if query:
            documents = [doc for doc in documents if query.casefold() in " ".join([doc["title"], doc["project"], *doc["tags"], doc.get("notes", "")]).casefold()]
        if project:
            documents = [doc for doc in documents if doc["project"] == project]
        if kind:
            documents = [doc for doc in documents if doc["kind"] == kind]
        if tag:
            documents = [doc for doc in documents if tag in doc["tags"]]
        if sort not in ("updated", "created", "title", "deleted", "recent") or not isinstance(descending, bool):
            raise LumenError("invalid_sort", "排序支持 updated/created/title/deleted/recent")
        if sort == "recent":
            with self.connect() as db:
                positions = {row["document_id"]:json.loads(row["value"]).get("read_at", "") for row in db.execute("SELECT * FROM positions")}
            for doc in documents:
                if doc["id"] not in positions:
                    positions[doc["id"]]=read_json(self.document_root(doc["id"])/"position.json",{}).get("read_at","")
            key = lambda doc: positions.get(doc["id"], "")
        else:
            field = {"updated":"updated_at","created":"created_at","title":"title","deleted":"deleted_at"}[sort]
            key = lambda doc: (doc.get(field) or "").casefold()
        documents.sort(key=key, reverse=descending)
        if sort == "updated" and not trashed:
            documents.sort(key=lambda doc: doc["favorite"], reverse=True)
        return {"documents": documents}

    def _publish(self, document, staged, pages, reason, idempotency_key=None):
        document_id = document["id"]
        from .management import apply_page_overrides
        apply_page_overrides(document, staged / "site", pages)
        destination = self.version_root(document_id, document["version"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise LumenError("conflict", "该版本已经存在，未覆盖历史")
        atomic_json(staged / "pages.json", pages)
        document["entry_path"] = str(destination / "site" / document.get("entry_relative", "index.html"))
        document["page_count"] = len(pages)
        atomic_json(staged / "metadata.json", {**document, "reason": reason})
        # Files become durable first. If the process dies before index commit,
        # the current indexed revision remains valid; recover_index sees only
        # complete versions with metadata and pages manifests.
        staged.rename(destination)
        atomic_json(self.document_root(document_id) / "properties.json", document)
        try:
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO documents VALUES(?,?)", (document_id, json.dumps(document, ensure_ascii=False)))
                self._index(db, document, pages, destination / "site")
                if idempotency_key:
                    db.execute("INSERT OR IGNORE INTO idempotency VALUES(?,?)", (idempotency_key, document_id))
        except Exception:
            # No published document may point at a half indexed revision.
            # The complete files are retained for deliberate recovery.
            raise
        return {"document": document}

    def _index(self, db, document, pages, site):
        db.execute("DELETE FROM page_index WHERE document_id=?", (document["id"],))
        for page in pages:
            _, segments = parse_page(site / page["relative_path"])
            for anchor, text in segments:
                db.execute("INSERT INTO page_index VALUES(?,?,?,?,?,?)", (document["id"], page["id"], page["title"], page["relative_path"], anchor, text))

    def _new_metadata(self, document_id, title, kind, existing=None, **values):
        document = {**(existing or {}), "id": document_id, "title": (existing or {}).get("title_override", title), "kind": kind,
                "project": values.get("project", (existing or {}).get("project", "")) or "",
                "tags": list(dict.fromkeys(values.get("tags", (existing or {}).get("tags", [])) or [])),
                "favorite": (existing or {}).get("favorite", False), "archived": (existing or {}).get("archived", False),
                "metadata_revision": (existing or {}).get("metadata_revision", 0),
                "notes": (existing or {}).get("notes", ""), "version": (existing or {}).get("version", 0) + 1,
                "created_at": (existing or {}).get("created_at", now()), "updated_at": now(), "entry_relative": "index.html"}
        document["metadata_revision"] = metadata_revision(existing, document)
        return document

    def build(self, content, kind="portal", document_id=None, expected_version=None, project=None, tags=None, sources=None, idempotency_key=None):
        from .rendering import normalize_content, render_document
        normalized = normalize_content(content, kind)
        with library_lock(self.root):
            if idempotency_key:
                with self.connect() as db:
                    row = db.execute("SELECT document_id FROM idempotency WHERE key=?", (idempotency_key,)).fetchone()
                if row:
                    return {"document": self.document(row[0]), "validation": {"ok": True, "reused": True}}
            existing = self.document(document_id) if document_id else None
            if existing:
                self.check_version(existing, expected_version)
            document_id = document_id or "doc-" + uuid.uuid4().hex[:20]
            generated = deepcopy(normalized)
            feedback = {}
            if existing:
                old = self.version_root(document_id, existing["version"])
                current = read_json(old / "content.json")
                base = read_json(old / "generated_content.json", current)
                if current is not None:
                    normalized, conflicts = merge_content(base, current, normalized)
                    if conflicts:
                        raise LumenError("merge_conflict", "新内容与你的修改冲突，已保留当前版本", {"conflicts": conflicts})
                feedback = read_json(old / "feedback.json", {})
            values = {key: value for key, value in {"project": project, "tags": tags}.items() if value is not None}
            document = self._new_metadata(document_id, normalized["title"], kind, existing, **values)
            document.update(structured=True, source_path=(existing or {}).get("source_path"), sources=sources if sources is not None else (existing or {}).get("sources", []))
            stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.root))
            try:
                rendered = render_document(normalized, stage / "site", document_id, document["version"], feedback=feedback, labels=self._labels())
                document["entry_relative"] = rendered.get("entry", "index.html")
                atomic_json(stage / "content.json", normalized)
                atomic_json(stage / "generated_content.json", generated)
                atomic_json(stage / "feedback.json", feedback)
                result = self._publish(document, stage, rendered["pages"], "agent-update" if existing else "build", idempotency_key)
                result["validation"] = rendered.get("validation", {"ok": True})
                return result
            finally:
                if stage.exists():
                    shutil.rmtree(stage)

    def import_document(self, path, kind=None, project=None, tags=None, title=None, include_test=False, on_duplicate="existing", expected_version=None, document_id=None):
        if on_duplicate not in {"existing","update","copy"}:
            raise LumenError("invalid_import", "重复导入方式为 existing/update/copy")
        path = Path(path).expanduser().resolve()
        source_root=None
        if path.is_dir():
            folder=path
            exported=read_json(folder/"metadata.json") if (folder/"metadata.json").is_file() and (folder/"site").is_dir() else None
            if isinstance(exported,dict):
                source_root=(folder/"site").resolve(); path=(source_root/exported.get("entry_relative","index.html")).resolve()
                if not path.is_relative_to(source_root): raise LumenError("path_rejected", "导出目录的入口越界")
            else:
                path = folder / "index.html" if (folder/"index.html").is_file() else folder / "site" / "index.html"
                source_root=path.parent
        if not path.is_file() or path.suffix.lower() not in (".html", ".htm"):
            raise LumenError("invalid_source", "请选择现有 HTML 或包含 index.html 的门户目录")
        if path.stat().st_size > 30 * 1024 * 1024:
            raise LumenError("package_limit", "单个HTML页面超过30MB编辑保存预算，未导入无法保存的页面")
        # Discovery and manual import must open the same managed knowledge item.
        # Serialize only this source; the library lock remains the publish owner.
        identity = hashlib.sha256(str(path).encode()).hexdigest()
        with library_lock(self.root / "imports" / identity):
            if document_id:
                if on_duplicate!="update": raise LumenError("invalid_import", "指定文档更新需明确 on_duplicate=update 和 expected_version")
                self.check_version(self.document(document_id),expected_version)
                return self._import_document(path,kind,project,tags,title,include_test,document_id=document_id,expected_version=expected_version,source_root=source_root)
            existing = next((item for item in self.list(archived=None,trashed=None)["documents"] if item.get("source_path") == str(path) or any(s.get("path")==str(path) for s in item.get("sources",[]) if isinstance(s,dict))), None)
            if existing and on_duplicate=="existing":
                result = self.get(existing["id"])
                result.update(reused=True, notice="已有这份来源，已打开库中版本并保留修改；更新请使用生成或构建的更新入口。")
                return result
            if existing and on_duplicate=="update":
                return self._import_document(path,kind,project,tags,title,include_test,document_id=existing["id"],expected_version=expected_version,source_root=source_root)
            return self._import_document(path, kind, project, tags, title or (existing["title"]+" 副本" if existing else None), include_test,source_root=source_root)

    def _import_document(self, path, kind=None, project=None, tags=None, title=None, include_test=False, document_id=None, expected_version=None, source_root=None):
        raw = path.read_text(encoding="utf-8")
        title_match = re.search(r"<title\b[^>]*>(.*?)</title>", raw, re.I | re.S)
        classification = path.name + " " + (title_match[1] if title_match else "")
        explicit_test = bool(re.search(r'<meta[^>]+name=["\']lumen-role["\'][^>]+content=["\']test["\']', raw, re.I))
        if not include_test and (explicit_test or re.search(r"pelican|鹈鹕|svg[-_ ]?test", classification, re.I)):
            raise LumenError("test_excluded", "测试页面不进入默认知识库，可显式 include_test 导入")
        content_path = path.parent / "content.json"
        if content_path.is_file():
            candidate = read_json(content_path)
            if isinstance(candidate, dict) and isinstance(candidate.get("pages"), list):
                result = self.build(candidate, kind or "portal", document_id=document_id,expected_version=expected_version,project=project, tags=tags, sources=[{"path": str(path), "role": "imported-original"}])
                self.metadata(result["document"]["id"], source_path=str(path))
                result["document"] = self.document(result["document"]["id"])
                return result
        from .rendering import enhance_html
        with library_lock(self.root):
            existing=self.document(document_id) if document_id else None
            if existing: self.check_version(existing,expected_version)
            document_id = document_id or "doc-" + uuid.uuid4().hex[:20]
            parsed_title, _ = parse_page(path)
            document = self._new_metadata(document_id, title or parsed_title or path.stem, kind or (existing or {}).get("kind") or "report", existing,project=project,tags=tags)
            sources=deepcopy((existing or {}).get("sources",[]))
            if not any(s.get("path")==str(path) for s in sources if isinstance(s,dict)): sources.append({"path":str(path),"role":"edited-copy" if existing else "imported-original"})
            document.update(structured=False, source_path=(existing or {}).get("source_path") or str(path), sources=sources, entry_relative=path.name)
            document["metadata_revision"]=metadata_revision(existing,document)
            stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.root))
            try:
                site = stage / "site"
                site.mkdir()
                files, warnings = self._copy_dependencies(path, site,source_root)
                pages = []
                feedback=read_json(self.version_root(document_id,existing["version"])/"feedback.json",{}) if existing else {}
                old_pages=read_json(self.version_root(document_id,existing["version"])/"pages.json",[]) if existing else []
                old_ids={p["relative_path"]:p["id"] for p in old_pages}
                old_entry=next((p for p in old_pages if p["relative_path"]==existing.get("entry_relative")),None) if existing else None
                alias_target=(existing or {}).get("entry_alias_target")
                if existing and existing.get("structured"):
                    old_content=read_json(self.version_root(document_id,existing["version"])/"content.json",{})
                    home=next((p for p in old_pages if p["id"]==old_content.get("home")),None)
                    if home and home["relative_path"]!=existing.get("entry_relative") and path.name==existing.get("entry_relative"):
                        alias_target=home["relative_path"]
                        previous_entry=self.version_root(document_id,existing["version"])/"site"/existing["entry_relative"]
                        if raw!=previous_entry.read_text(encoding="utf-8"):
                            copied_home=site/alias_target
                            previous_home=self.version_root(document_id,existing["version"])/"site"/alias_target
                            if copied_home.is_file() and previous_home.is_file() and copied_home.read_text(encoding="utf-8") not in {previous_home.read_text(encoding="utf-8"),raw}:
                                raise LumenError("edited_copy_conflict", "入口与首页副本都已分别修改，请合并为同一份首页内容后重新导入")
                            (site/alias_target).write_text(raw,encoding="utf-8"); files.add(alias_target)
                ignored_aliases=set()
                if alias_target and path.name==(existing or {}).get("entry_relative"):
                    from html import escape
                    target=quote(alias_target,safe="/.")
                    (site/path.name).write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url='+escape(target,quote=True)+'"><a href="'+escape(target,quote=True)+'">打开当前页面</a>',encoding="utf-8")
                    document["entry_alias_target"]=alias_target; ignored_aliases.add(path.name)
                if existing:
                    for key,value in existing.get("page_path_overrides",{}).items():
                        alias=key+".html"
                        if alias!=value: ignored_aliases.add(alias)
                for relative in sorted(files):
                    target = site / relative
                    if relative in ignored_aliases or target.suffix.lower() not in (".html", ".htm"):
                        continue
                    page_id = old_ids.get(relative) or (old_entry["id"] if old_entry and relative==path.name else None) or "page-" + hashlib.sha256(relative.encode()).hexdigest()[:12]
                    page_title, _ = parse_page(target)
                    enhanced = enhance_html(target.read_text(encoding="utf-8"), document_id, page_id, document["version"], labels=self._labels(),feedback=feedback.get(page_id))
                    if len(enhanced.encode("utf-8")) > 30 * 1024 * 1024:
                        raise LumenError("package_limit", "关联HTML页面含编辑器超过30MB保存预算，未导入不完整可编辑包")
                    target.write_text(enhanced, encoding="utf-8")
                    pages.append({"id": page_id, "title": page_title or target.stem, "relative_path": relative})
                order={p["id"]:i for i,p in enumerate(old_pages)}
                pages.sort(key=lambda p:order.get(p["id"],-1 if p["relative_path"]==document.get("entry_alias_target",document["entry_relative"]) else len(order)))
                atomic_json(stage / "feedback.json", feedback)
                result = self._publish(document, stage, pages, "import")
                result["validation"] = {"ok": not warnings, "files": len(files), "warnings": warnings}
                return result
            finally:
                if stage.exists():
                    shutil.rmtree(stage)

    def _copy_dependencies(self, entry, site, source_root=None):
        root = Path(source_root or entry.parent).resolve()
        pending = [entry]
        seen = set()
        warnings = []
        total = 0
        while pending:
            source = pending.pop()
            resolved = source.resolve()
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                warnings.append({"path": str(source), "reason": "outside-package"})
                continue
            # Reject links in parent directories as well as links at the leaf.
            if not source.is_file() or any(part.is_symlink() for part in [source, *source.parents] if part != root and root in part.parents):
                warnings.append({"path": str(source), "reason": "missing-or-symlink"})
                continue
            if resolved in seen:
                continue
            total += source.stat().st_size
            if len(seen) >= 2000 or total > 300 * 1024 * 1024:
                raise LumenError("package_limit", "关联文件超出单份知识包预算，未导入不完整包")
            seen.add(resolved)
            target = site / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if source.suffix.lower() in (".html", ".htm", ".css"):
                text = source.read_text(encoding="utf-8")
                base = source.as_uri()
                if source.suffix.lower() == ".css":
                    links = css_resources(text)
                else:
                    parser = ResourceLinks()
                    parser.feed(text)
                    parser.close()
                    links = parser.links
                    if parser.base is not None:
                        base = urljoin(base, parser.base)
                    # Absolute bases into the source package must become portable
                    # before the original directory can be removed or moved.
                    target.write_text(portable_snapshot_base(text, root, relative), encoding="utf-8")
                for link in links:
                    original = urlsplit(link)
                    if not original.path or (original.scheme and original.scheme != "file") or (original.netloc and original.scheme != "file"):
                        continue
                    parsed = urlsplit(urljoin(base, link))
                    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
                        continue
                    referenced = Path(unquote(parsed.path))
                    pending.append(referenced)
        return {str(source.relative_to(root)) for source in seen}, warnings

    def save_document(self, document_id, page_id, expected_version, patches=None, feedback=None, html=None):
        from .rendering import render_document, enhance_html
        with library_lock(self.root):
            existing = self.document(document_id)
            self.check_version(existing, expected_version)
            old = self.version_root(document_id, existing["version"])
            pages = read_json(old / "pages.json", [])
            page = next((page for page in pages if page["id"] == page_id), None)
            if not page:
                raise LumenError("invalid_page", "该章节不属于当前成果")
            all_feedback = read_json(old / "feedback.json", {})
            if feedback is not None:
                if not isinstance(feedback, dict):
                    raise LumenError("invalid_feedback", "意见记录必须为对象")
                all_feedback[page_id] = feedback
            document = {**existing, "version": existing["version"] + 1, "updated_at": now()}
            stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.root))
            try:
                if existing["structured"]:
                    content = apply_patches(read_json(old / "content.json"), patches or [])
                    rendered = render_document(content, stage / "site", document_id, document["version"], feedback=all_feedback, labels=self._labels())
                    pages = rendered["pages"]
                    document["title"] = existing.get("title_override", content["title"])
                    atomic_json(stage / "content.json", content)
                    atomic_json(stage / "generated_content.json", read_json(old / "generated_content.json", content))
                    validation = rendered.get("validation", {"ok": True})
                else:
                    if not isinstance(html, str) or not html.strip() or len(html.encode()) > 30 * 1024 * 1024:
                        raise LumenError("invalid_html", "保存缺少完整页面快照")
                    shutil.copytree(old / "site", stage / "site")
                    for other in pages:
                        target = stage / "site" / other["relative_path"]
                        source = html if other["id"] == page_id else target.read_text(encoding="utf-8")
                        source = portable_snapshot_base(source, old / "site", other["relative_path"])
                        source = enhance_html(source, document_id, other["id"], document["version"], labels=self._labels(), feedback=all_feedback.get(other["id"]))
                        target.write_text(source, encoding="utf-8")
                    validation = {"ok": True, "snapshot": True}
                document["metadata_revision"] = metadata_revision(existing, document)
                atomic_json(stage / "feedback.json", all_feedback)
                result = self._publish(document, stage, pages, "user-save")
                result["validation"] = validation
                return result
            finally:
                if stage.exists():
                    shutil.rmtree(stage)

    def metadata(self, document_id, expected_version=None, expected_metadata_revision=None, **values):
        allowed = {"title", "project", "tags", "favorite", "archived", "notes", "source_path"}
        if set(values) - allowed:
            raise LumenError("invalid_metadata", "未知成果属性", {"fields": sorted(set(values) - allowed)})
        with library_lock(self.root):
            document = self.document(document_id)
            if expected_version is not None:
                self.check_version(document, expected_version)
            current_revision = document["metadata_revision"]
            # Content versions do not identify notes/classification changes. Old
            # conditional clients must get a token instead of silently overwriting.
            if (expected_version is not None and expected_metadata_revision is None) or (expected_metadata_revision is not None and (not isinstance(expected_metadata_revision, int) or isinstance(expected_metadata_revision, bool) or expected_metadata_revision != current_revision)):
                raise LumenError("conflict", "成果属性已经更新；请先读取当前属性修订号，再比较后保存", {"expected_metadata_revision": expected_metadata_revision, "current_metadata_revision": current_revision})
            for key, value in values.items():
                if key in ("favorite", "archived") and not isinstance(value, bool):
                    raise LumenError("invalid_metadata", "收藏/归档必须为布尔值")
                if key == "tags" and (not isinstance(value, list) or any(not isinstance(tag, str) for tag in value)):
                    raise LumenError("invalid_metadata", "标签必须为文字列表")
                if key not in ("favorite", "archived", "tags", "source_path") and not isinstance(value, str):
                    raise LumenError("invalid_metadata", "成果属性必须为文字")
            if not values:
                return {"document": document}
            updated = {**document, **values}
            if "title" in values:
                updated["title_override"] = values["title"]
            if updated == document:
                return {"document": document}
            updated["metadata_revision"] = metadata_revision(document, updated)
            document = {**updated, "updated_at": now()}
            # Personal notes, classification and import provenance outlive the
            # derived SQLite index without rewriting historical page packages.
            atomic_json(self.document_root(document_id) / "properties.json", document)
            with self.connect() as db:
                db.execute("UPDATE documents SET metadata=? WHERE id=?", (json.dumps(document, ensure_ascii=False), document_id))
            return {"document": document}

    def versions(self, document_id):
        self.document(document_id)
        versions = []
        root = self.document_root(document_id) / "versions"
        folders = [folder for folder in root.iterdir() if folder.is_dir() and re.fullmatch(r"[1-9]\d*", folder.name)]
        for folder in sorted(folders, key=lambda path: int(path.name), reverse=True):
            data = read_json(folder / "metadata.json")
            if data:
                versions.append({"version": data["version"], "created_at": data["updated_at"], "reason": data["reason"], "entry_path": data["entry_path"]})
        return {"versions": versions}

    def compare(self, document_id, from_version, to_version=None):
        before = self.get(document_id, from_version)
        after = self.get(document_id, to_version)
        changes = []

        def walk(left, right, path=""):
            if left == right:
                return
            if isinstance(left, dict) and isinstance(right, dict):
                for key in sorted(set(left) | set(right)):
                    walk(left.get(key), right.get(key), path + "/" + key)
            elif isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
                for index in range(len(left)):
                    walk(left[index], right[index], path + "/" + str(index))
            else:
                changes.append({"path": path or "/", "before": left, "after": right})

        if before["content"] is not None and after["content"] is not None:
            walk(before["content"], after["content"])
        else:
            before_pages = {page["relative_path"]: page for page in before["pages"]}
            after_pages = {page["relative_path"]: page for page in after["pages"]}
            for path in sorted(set(before_pages) | set(after_pages)):
                old_text = parse_page(before_pages[path]["absolute_path"])[1] if path in before_pages else None
                new_text = parse_page(after_pages[path]["absolute_path"])[1] if path in after_pages else None
                walk(old_text, new_text, "/pages/" + path)
        walk(before["feedback"], after["feedback"], "/feedback")
        return {"document_id": document_id, "from_version": before["document"]["version"], "to_version": after["document"]["version"], "changes": changes, "summary": "共 " + str(len(changes)) + " 处变化"}

    def restore(self, document_id, version, expected_version):
        with library_lock(self.root):
            current = self.document(document_id)
            self.check_version(current, expected_version)
            old = self.version_root(document_id, version)
            if not old.is_dir():
                raise LumenError("not_found", "指定的历史版本不存在")
            stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.root))
            try:
                shutil.copytree(old, stage, dirs_exist_ok=True)
                document = {**read_json(old / "metadata.json"), "version": current["version"] + 1, "updated_at": now(), **{key: current[key] for key in ("favorite", "archived", "notes", "project", "tags", "source_path", "sources", "source_history", "metadata_revision", "trashed", "deleted_at") if key in current}}
                if "title_override" in current:
                    document.update(title_override=current["title_override"], title=current["title_override"])
                document["metadata_revision"] = metadata_revision(current, document)
                document.pop("reason", None)
                pages = read_json(stage / "pages.json")
                content = read_json(stage / "content.json")
                if content is not None:
                    from .rendering import render_document
                    shutil.rmtree(stage / "site")
                    rendered = render_document(content, stage / "site", document_id, document["version"], feedback=read_json(stage / "feedback.json", {}), labels=self._labels())
                    pages = rendered["pages"]
                else:
                    from .rendering import enhance_html
                    for page in pages:
                        target = stage / "site" / page["relative_path"]
                        target.write_text(enhance_html(target.read_text(), document_id, page["id"], document["version"], labels=self._labels(), feedback=read_json(stage / "feedback.json", {}).get(page["id"])), encoding="utf-8")
                return self._publish(document, stage, pages, "restore-v" + str(version))
            finally:
                if stage.exists():
                    shutil.rmtree(stage)

    def search(self, query, limit=20):
        if not isinstance(query, str) or not query.strip():
            raise LumenError("invalid_query", "请输入搜索词")
        limit = max(1, min(int(limit), 1000))
        literal = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self.connect() as db:
            rows = db.execute("SELECT p.*,d.metadata FROM page_index p JOIN documents d ON d.id=p.document_id WHERE p.text LIKE ? ESCAPE '\\' OR p.title LIKE ? ESCAPE '\\'", ("%" + literal + "%", "%" + literal + "%")).fetchall()
        results = []
        for row in rows:
            document = json.loads(row["metadata"])
            if document["archived"] or document.get("trashed") or (self.root / "purged" / (document["id"] + ".json")).exists():
                continue
            text = row["text"]
            index = text.casefold().find(query.strip().casefold())
            start = max(0, index - 65)
            results.append({"document_id": document["id"], "page_id": row["page_id"], "title": document["title"], "page_title": row["title"], "path": str(self.version_root(document["id"], document["version"]) / "site" / row["relative_path"]), "anchor": row["anchor"], "snippet": text[start:start + 220], "version": document["version"]})
        return {"matches": results[:limit], "count": len(results), "truncated": len(results) > limit}

    def feedback(self, document_id, page_id=None):
        current = self.get(document_id)
        feedback = current["feedback"]
        return {"feedback": feedback.get(page_id, {}) if page_id else feedback, "notes": current["notes"]}

    def position(self, document_id, page_id=None, scroll_y=None, anchor=None):
        self.document(document_id)
        with self.connect() as db:
            row = db.execute("SELECT value FROM positions WHERE document_id=?", (document_id,)).fetchone()
            position_file = self.document_root(document_id) / "position.json"
            position = json.loads(row[0]) if row else read_json(position_file, {})
            if page_id is not None or scroll_y is not None or anchor is not None:
                position.update({key: value for key, value in {"page_id": page_id, "scroll_y": scroll_y, "anchor": anchor, "read_at":now()}.items() if value is not None})
                atomic_json(position_file, position)
                db.execute("INSERT OR REPLACE INTO positions VALUES(?,?)", (document_id, json.dumps(position, ensure_ascii=False)))
        return {"position": {key:value for key,value in position.items() if key != "read_at"}}

    def export(self, document_id, output, version=None):
        import zipfile
        document = self.document(document_id)
        selected_version = document["version"] if version is None else version
        source = self.version_root(document_id, selected_version)
        metadata = read_json(source / "metadata.json")
        pages = read_json(source / "pages.json")
        if source.is_symlink() or not isinstance(metadata, dict) or metadata.get("id") != document_id or metadata.get("version") != selected_version or not isinstance(pages, list) or not pages:
            raise LumenError("not_found", "指定版本不存在或文件不完整", {"version": selected_version})
        required = [source / "site" / metadata.get("entry_relative", "index.html"), source / "feedback.json"]
        if metadata.get("structured"):
            required.append(source / "content.json")
        for page in pages:
            relative = page.get("relative_path") if isinstance(page, dict) else None
            if not isinstance(relative, str) or not relative:
                raise LumenError("corrupt_file", "版本的章节清单不完整")
            required.append(source / "site" / relative)
        source_root = source.resolve()
        for path in required:
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source_root):
                raise LumenError("not_found", "指定版本不存在或文件不完整", {"version": selected_version, "path": str(path)})
        output = Path(output).expanduser().absolute()
        from .reading import check_export_target
        check_export_target(self,output)
        if os.path.lexists(output):
            raise LumenError("file_exists", "导出目标已存在，未覆盖")
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".lumen-export-", suffix=".zip", dir=output.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in source.rglob("*"):
                    if path.is_symlink():
                        raise LumenError("corrupt_file", "版本中含有符号链接，未导出", {"path": str(path)})
                    if path.is_file():
                        archive.write(path, path.relative_to(source))
            # Publish a complete ZIP exclusively; even a competing export cannot
            # replace a target created after the earlier existence check.
            try:
                os.link(temporary, output)
            except FileExistsError:
                raise LumenError("file_exists", "导出目标已存在，未覆盖")
            return {"path": str(output), "version": selected_version, "bytes": output.stat().st_size}
        finally:
            temporary.unlink(missing_ok=True)

    def rebuild_index(self):
        with library_lock(self.root):
            count = 0
            with self.connect() as db:
                db.execute("DELETE FROM page_index")
                for folder in (self.root / "documents").glob("*/versions/*"):
                    data = read_json(folder / "metadata.json")
                    if not data or not (folder / "site" / data.get("entry_relative", "index.html")).is_file():
                        continue
                    if (self.root / "purged" / (data["id"] + ".json")).exists():
                        continue
                    row = db.execute("SELECT metadata FROM documents WHERE id=?", (data["id"],)).fetchone()
                    current = json.loads(row[0]) if row else None
                    if not current or data["version"] > current["version"]:
                        data.pop("reason", None)
                        db.execute("INSERT OR REPLACE INTO documents VALUES(?,?)", (data["id"], json.dumps(data, ensure_ascii=False)))
                for row in db.execute("SELECT metadata FROM documents").fetchall():
                    document = json.loads(row[0])
                    if (self.root / "purged" / (document["id"] + ".json")).exists():
                        db.execute("DELETE FROM documents WHERE id=?", (document["id"],))
                        db.execute("DELETE FROM positions WHERE document_id=?", (document["id"],))
                        db.execute("DELETE FROM idempotency WHERE document_id=?", (document["id"],))
                        continue
                    document.setdefault("metadata_revision", 0)
                    properties = read_json(self.document_root(document["id"]) / "properties.json")
                    if properties and properties.get("id") == document["id"] and properties.get("version") == document["version"]:
                        document = properties
                        document.setdefault("metadata_revision", 0)
                        db.execute("UPDATE documents SET metadata=? WHERE id=?", (json.dumps(document, ensure_ascii=False), document["id"]))
                    folder = self.version_root(document["id"], document["version"])
                    self._index(db, document, read_json(folder / "pages.json", []), folder / "site")
                    count += 1
            return {"documents": count}
