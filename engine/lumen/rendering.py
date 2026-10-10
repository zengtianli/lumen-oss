"""Lumen's offline, standardized HTML compiler and editable reader.

The content source is canonical. JSON Pointer annotations let the store commit
human edits to that source rather than treating a browser draft as a saved file.
The renderer and editor assets are bundled here. Runtime reads only this package.
"""
from collections import defaultdict, deque
from datetime import date
import copy
import hashlib
import html
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import shutil
import unicodedata
from urllib.parse import urlsplit, unquote

ASSETS = Path(__file__).resolve().parent / "assets"
ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
BLOCK_TYPES = {"prose", "steps", "table", "diagram", "links", "evidence", "callout", "code", "faq"}
KINDS = {"portal", "report", "teach"}


def esc(value):
    return html.escape(str(value), quote=True)


def script_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("--", "\\u002d\\u002d")


def inline(value):
    value = esc(value)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)


def href(page):
    return esc(page) + ".html"


def safe_url(value):
    value = str(value)
    if not value or any(ord(c) < 32 for c in value) or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme.lower() in ("http", "https", "file", "mailto", "tel") or (not parsed.scheme and not parsed.netloc)


def normalize_content(data, kind="portal"):
    """Validate and fill stable identities without mutating the caller's source."""
    if kind not in KINDS:
        raise ValueError("Unknown document kind: " + str(kind))
    if not isinstance(data, dict):
        raise ValueError("Content must be a JSON object")
    data = copy.deepcopy(data)
    if not isinstance(data.get("title"), str) or not data["title"].strip():
        raise ValueError("Content requires a nonempty title")
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("Content requires nonempty pages")
    data["kind"] = kind
    seen = set()
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("id"), str) or not ID.fullmatch(page["id"]):
            raise ValueError("Page IDs must be lowercase URL-safe identifiers")
        if page["id"] in seen:
            raise ValueError("Duplicate page ID: " + page["id"])
        seen.add(page["id"])
        if not isinstance(page.get("title"), str) or not page["title"].strip():
            raise ValueError("Page requires a title: " + page["id"])
        page.setdefault("summary", "")
        page.setdefault("section", "main")
        page.setdefault("blocks", [])
        if not isinstance(page["summary"], str) or not isinstance(page["blocks"], list):
            raise ValueError("Invalid summary or blocks in " + page["id"])
        blocks = set()
        for index, block in enumerate(page["blocks"]):
            if not isinstance(block, dict) or block.get("type") not in BLOCK_TYPES:
                raise ValueError("Unsupported content block in " + page["id"])
            block.setdefault("id", "block-" + str(index + 1))
            if not isinstance(block["id"], str) or not ID.fullmatch(block["id"]) or block["id"] in blocks:
                raise ValueError("Block IDs must be unique URL-safe identifiers within a page")
            blocks.add(block["id"])
            if block["type"] in ("prose", "callout", "code"):
                block.setdefault("text", "")
            if block["type"] == "steps":
                for item in block.get("items", []):
                    if isinstance(item, dict):
                        item.setdefault("title", "")
                        item.setdefault("text", "")
            if block["type"] == "evidence":
                for item in block.get("items", []):
                    if isinstance(item, dict):
                        item.setdefault("value", "")
    data.setdefault("home", pages[0]["id"])
    if data["home"] not in seen:
        raise ValueError("Unknown home page")
    if not data.get("sections"):
        data["sections"] = [{"id": name, "title": ("内容" if name == "main" else name)}
                            for name in dict.fromkeys(p["section"] for p in pages)]
    sections = data["sections"]
    if not isinstance(sections, list) or any(not isinstance(s, dict) or not isinstance(s.get("id"), str)
                                           or not isinstance(s.get("title"), str) for s in sections):
        raise ValueError("Sections require id and title")
    section_ids = [s["id"] for s in sections]
    if len(set(section_ids)) != len(section_ids):
        raise ValueError("Section IDs must be unique")
    by_id = {p["id"]: p for p in pages}
    for page in pages:
        if page["section"] not in section_ids:
            raise ValueError("Unknown section: " + str(page["section"]))
        if not isinstance(page.get("related", []), list):
            raise ValueError("related must be a page ID array")
        refs = [page.get("parent"), *page.get("related", [])]
        for block in page["blocks"]:
            kind_ = block["type"]
            if kind_ in ("prose", "callout", "code"):
                if not isinstance(block.get("text", ""), (str, list)):
                    raise ValueError("Block text must be a string or string array")
                if isinstance(block.get("text"), list) and any(not isinstance(t, str) for t in block["text"]):
                    raise ValueError("Text array must contain strings")
            if kind_ in ("steps", "links", "evidence", "faq") and not isinstance(block.get("items", []), list):
                raise ValueError("Block items must be an array")
            if kind_ == "links":
                refs.extend(item["page"] for item in block.get("items", []))
            if kind_ == "table":
                if not isinstance(block.get("headers", []), list) or not isinstance(block.get("rows", []), list):
                    raise ValueError("Table headers and rows must be arrays")
                if any(not isinstance(row, list) for row in block.get("rows", [])):
                    raise ValueError("Table row must be an array")
            if kind_ == "diagram":
                nodes = block.get("nodes", [])
                if not isinstance(nodes, list) or not nodes:
                    raise ValueError("Diagram requires nodes")
                node_ids = [n["id"] for n in nodes]
                if len(set(node_ids)) != len(node_ids):
                    raise ValueError("Diagram node IDs must be unique")
                refs.extend(n.get("page") for n in nodes)
                for node in nodes:
                    for dimension in ("x", "y"):
                        if dimension in node and (not isinstance(node[dimension], (int, float)) or not math.isfinite(node[dimension])):
                            raise ValueError("Diagram positions must be finite numbers")
                if any(e["from"] not in node_ids or e["to"] not in node_ids for e in block.get("edges", [])):
                    raise ValueError("Diagram has a dangling edge")
        if any(ref and ref not in seen for ref in refs):
            raise ValueError("Dangling page reference in " + page["id"])
        visited, current = set(), page
        while current.get("parent"):
            if current["id"] in visited:
                raise ValueError("Cyclic page ancestry")
            visited.add(current["id"])
            current = by_id[current["parent"]]
    # Reject non-JSON values and NaN before any output is written.
    json.dumps(data, ensure_ascii=False, allow_nan=False)
    return data


def source_attrs(path, original, part=0, stable_identity=None):
    identity = "field-" + hashlib.sha256(((stable_identity or path) + ":" + str(part)).encode()).hexdigest()[:16]
    return (' data-html-editable data-feedback-block="' + identity +
            '" data-lumen-source="' + esc(path) +
            '" data-lumen-original="' + esc(json.dumps(original, ensure_ascii=False, allow_nan=False)) +
            '" data-lumen-part="' + str(part) + '"')


def field(tag, value, path, attributes="", formatting=True, stable_identity=None):
    return ("<" + tag + attributes + source_attrs(path, value, stable_identity=stable_identity) + ">" +
            (inline(value).replace("\n", "<br>") if formatting else esc(value)) + "</" + tag + ">")


def paragraphs(value, path, stable_identity=None):
    if isinstance(value, list):
        return "".join(field("p", text, path + "/" + str(i), stable_identity=(stable_identity + "/" + str(i)) if stable_identity else None) for i, text in enumerate(value))
    segments = re.split(r"\n\s*\n", str(value))
    return "".join("<p" + source_attrs(path, value, i, stable_identity) + ">" +
                   inline(text).replace("\n", "<br>") + "</p>" for i, text in enumerate(segments))


def wrapped(text, columns=26):
    lines, current, size = [], "", 0
    for char in str(text):
        width = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if size + width > columns and current:
            lines.append(current)
            current, size = "", 0
        current += char
        size += width
    return [*lines, current] if current else lines


def diagram(block, number):
    nodes = [dict(n) for n in block["nodes"]]
    edges = block.get("edges", [])
    if not all("x" in n and "y" in n for n in nodes):
        degree, children, rank = defaultdict(int), defaultdict(list), defaultdict(int)
        for edge in edges:
            degree[edge["to"]] += 1
            children[edge["from"]].append(edge["to"])
        queue = deque(n["id"] for n in nodes if not degree[n["id"]])
        visited = set()
        while queue:
            current = queue.popleft()
            visited.add(current)
            for child in children[current]:
                rank[child] = max(rank[child], rank[current] + 1)
                degree[child] -= 1
                if not degree[child]:
                    queue.append(child)
        columns = defaultdict(list)
        for node in nodes:
            columns[rank[node["id"]]].append(node)
        height = max(len(col) for col in columns.values()) * 150
        for level, col in columns.items():
            for index, node in enumerate(col):
                node.setdefault("x", 130 + 330 * level)
                node.setdefault("y", (index + 1) * height / (len(col) + 1))
    for node in nodes:
        node["lines"] = wrapped(node.get("label", node["id"]))
        node["captions"] = wrapped(node.get("caption", ""), 32)
        node["height"] = max(86, 30 + len(node["lines"]) * 23 + len(node["captions"]) * 18)
    lookup = {n["id"]: n for n in nodes}
    left = min(n["x"] - 130 for n in nodes) - 24
    top = min(n["y"] - n["height"] / 2 for n in nodes) - 40
    right = max(n["x"] + 130 for n in nodes) + 24
    bottom = max(n["y"] + n["height"] / 2 for n in nodes) + 40
    out = [f'<figure class="diagram"><svg role="group" aria-label="{esc(block.get("title", "可点击的关系图"))}" viewBox="{left} {top} {right-left} {bottom-top}">']
    marker = f"arrow-{number}"
    out.append(f'<defs><marker id="{marker}" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0 0 L9 4.5 L0 9" fill="#9aafc7"/></marker></defs>')
    for edge in edges:
        a, b = lookup[edge["from"]], lookup[edge["to"]]
        x1, x2 = a["x"] + 116, b["x"] - 116
        if b["x"] == a["x"]:
            x1, x2 = a["x"], b["x"]
            direction = 1 if b["y"] >= a["y"] else -1
            y1 = a["y"] + direction * a["height"] / 2
            y2 = b["y"] - direction * b["height"] / 2
        else:
            y1, y2 = a["y"], b["y"]
            if b["x"] < a["x"]:
                x1, x2 = a["x"] - 116, b["x"] + 116
        mid = (x1 + x2) / 2
        support = edge.get("kind") == "support"
        out.append(f'<path class="diagram-edge{" support" if support else ""}" d="M{x1} {y1} C{mid} {y1} {mid} {y2} {x2} {y2}"' + ("" if support else f' marker-end="url(#{marker})"') + "/>" )
        if edge.get("label"):
            out.append(f'<text class="diagram-label" x="{mid}" y="{(y1+y2)/2-11}" text-anchor="middle">{esc(edge["label"])}</text>')
    for node in nodes:
        target = f'<a href="{href(node["page"])}" aria-label="进入：{esc(node.get("label", node["id"]))}">' if node.get("page") else '<g>'
        out.append(target + f'<g class="diagram-node" transform="translate({node["x"]-116} {node["y"]-node["height"]/2})"><title>{esc(node.get("label",node["id"]))}</title><rect width="232" height="{node["height"]}" rx="14"/>')
        for i, line in enumerate(node["lines"]):
            out.append(f'<text x="116" y="{29+i*23}" text-anchor="middle" class="diagram-title">{esc(line)}</text>')
        for i, line in enumerate(node["captions"]):
            y = 30 + len(node["lines"]) * 23 + i * 18
            out.append(f'<text x="116" y="{y}" text-anchor="middle" class="diagram-caption">{esc(line)}</text>')
        out.append('</g>' + ('</a>' if node.get("page") else '</g>'))
    out.append('</svg>')
    if block.get("caption"):
        out.append('<figcaption>' + inline(block["caption"]) + '</figcaption>')
    out.append('</figure>')
    return "".join(out)


def render_block(block, index, by_id, page_index, page_id=None):
    path = "/pages/" + str(page_index) + "/blocks/" + str(index)
    page_id = page_id or list(by_id)[page_index]
    stable_root = "page/" + page_id + "/block/" + block["id"]
    def f(tag, value, pointer, attributes="", formatting=True):
        return field(tag, value, pointer, attributes, formatting, stable_root + pointer[len(path):])
    def ps(value, pointer):
        return paragraphs(value, pointer, stable_root + pointer[len(path):])
    kind = block["type"]
    title = f("h2", block["title"], path + "/title", ' id="b-' + esc(block["id"]) + '"') if block.get("title") else ""
    if kind == "prose":
        body = ps(block.get("text", ""), path + "/text")
    elif kind == "steps":
        items = []
        for i, item in enumerate(block.get("items", [])):
            pointer = path + "/items/" + str(i)
            if isinstance(item, dict):
                item_body = f("strong", item.get("title", ""), pointer + "/title") + ps(item.get("text", ""), pointer + "/text")
                items.append("<li>" + item_body + "</li>")
            else:
                items.append(f("li", item, pointer))
        body = '<ol class="steps">' + "".join(items) + "</ol>"
    elif kind == "table":
        headers = "".join(f("th", value, path + "/headers/" + str(i)) for i, value in enumerate(block.get("headers", [])))
        rows = []
        for r, row in enumerate(block.get("rows", [])):
            cells = []
            for c, value in enumerate(row):
                pointer = path + "/rows/" + str(r) + "/" + str(c)
                cells.append("<td>" + ps(value, pointer) + "</td>" if isinstance(value, list) else f("td", value, pointer))
            rows.append("<tr>" + "".join(cells) + "</tr>")
        body = '<div class="table-scroll"><table><thead><tr>' + headers + '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"
    elif kind == "diagram":
        body = diagram(block, index)
        body = body.replace('<figure class="diagram"', '<figure class="diagram" data-feedback-object data-feedback-block="' + esc(block["id"]) + '"', 1)
        if block.get("caption"):
            body = re.sub(r"<figcaption>.*?</figcaption>", f("figcaption", block["caption"], path + "/caption"), body, flags=re.S)
    elif kind == "links":
        items = []
        for i, item in enumerate(block.get("items", [])):
            pointer = path + "/items/" + str(i)
            label = f("strong", item["label"], pointer + "/label") if "label" in item else "<strong>" + esc(by_id[item["page"]]["title"]) + "</strong>"
            desc = f("span", item["description"], pointer + "/description") if "description" in item else "<span>" + esc(by_id[item["page"]].get("summary", "")) + "</span>"
            items.append('<li><a href="' + href(item["page"]) + '">' + label + desc + '<b aria-hidden="true">→</b></a></li>')
        body = '<ol class="reading-links">' + "".join(items) + "</ol>"
    elif kind == "evidence":
        pieces = ['<dl class="evidence">']
        for i, item in enumerate(block.get("items", [])):
            pointer = path + "/items/" + str(i)
            pieces.append(f("dt", item["label"], pointer + "/label") + "<dd>")
            if item.get("state"):
                pieces.append(f("span", item["state"], pointer + "/state", ' class="state"') + " ")
            pieces.append(ps(item.get("value", ""), pointer + "/value"))
            if item.get("path"):
                pieces.append(f("code", item["path"], pointer + "/path", ' class="source-path" data-feedback-object', False))
            if item.get("url"):
                if not safe_url(item["url"]):
                    raise ValueError("Unsafe evidence URL")
                pieces.append('<a class="source-link" data-lumen-external href="' + esc(item["url"]) + '">查看原证据 ↗</a>')
            pieces.append("</dd>")
        body = "".join(pieces) + "</dl>"
    elif kind == "callout":
        tone = "warning" if block.get("tone") == "warning" else "info"
        body = '<aside class="callout ' + tone + '">' + ps(block.get("text", ""), path + "/text") + "</aside>"
    elif kind == "code":
        body = "<pre>" + f("code", block.get("text", ""), path + "/text", formatting=False) + "</pre>"
    elif kind == "faq":
        items = []
        for i, item in enumerate(block.get("items", [])):
            pointer = path + "/items/" + str(i)
            items.append('<details class="faq">' + f("summary", item["question"], pointer + "/question") + ps(item["answer"], pointer + "/answer") + "</details>")
        body = "".join(items)
    else:
        raise ValueError("Unsupported block type: " + str(kind))
    return '<section class="article-section" data-lumen-block="' + esc(block["id"]) + '">' + title + body + "</section>"


def enhance_html(source, document_id, page_id, version, *, feedback=None, labels=None, src=None, native_editor=None):
    """Mount one editable reader; imported pages keep their original structure."""
    match = re.search(r"<title[^>]*>(.*?)</title>", source, re.I | re.S)
    title = html.unescape(re.sub(r"<[^>]+>", "", match.group(1))) if match else str(page_id)
    if native_editor is None:
        old_init = re.search(r'<script\b[^>]*\bid=["\']html-feedback-init["\'][^>]*>(.*?)</script\s*>', source, re.I | re.S)
        try:
            native_editor = bool(old_init and json.loads(old_init.group(1)).get("native_editor"))
        except (ValueError, TypeError):
            native_editor = False
    if feedback is None:
        old = re.search(r'<script\b[^>]*\bid=["\']html-feedback-state["\'][^>]*>(.*?)</script\s*>', source, re.I | re.S)
        if old:
            try:
                feedback = json.loads(old.group(1))
            except (ValueError, TypeError):
                feedback = None
    for identity in ("html-feedback-style", "html-feedback-init", "html-feedback-core", "html-feedback-state"):
        source = re.sub(r'<(script|style)\b[^>]*\bid=["\']' + identity + r'["\'][^>]*>.*?</\1\s*>', "", source, flags=re.I | re.S)
    init = {"title": title, "src": src or "", "document_id": str(document_id), "page_id": str(page_id),
            "version": int(version), "build": hashlib.sha256(source.encode()).hexdigest()[:16],
            "labels": labels or {}, "native_editor": bool(native_editor)}
    addon = '<style id="html-feedback-style">' + (ASSETS / "edit-feedback.css").read_text() + "</style>\n"
    addon += '<script id="html-feedback-init" type="application/json">' + script_json(init) + "</script>\n"
    if feedback:
        addon += '<script id="html-feedback-state" type="application/json">' + script_json(feedback) + "</script>\n"
    addon += '<script id="html-feedback-core">' + (ASSETS / "edit-feedback.js").read_text() + "</script>\n"
    close = re.search(r"</body\s*>", source, re.I)
    return source[:close.start()] + addon + source[close.start():] if close else source + addon


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if "data-lumen-external" in attrs:
            return
        for name in ("href", "src"):
            if attrs.get(name):
                self.links.append(attrs[name])


def validate_site(out, filenames):
    out = Path(out)
    missing = []
    count = 0
    parsed = {}
    for filename in filenames:
        parser = _Links()
        parser.feed((out / filename).read_text())
        parsed[filename] = parser
    for filename, parser in parsed.items():
        for target in parser.links:
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            count += 1
            path = (out / unquote(url.path or filename)).resolve()
            try:
                path.relative_to(out.resolve())
            except ValueError:
                missing.append(filename + " → path outside document: " + target)
                continue
            if not path.is_file():
                missing.append(filename + " → " + target)
            elif url.fragment and path.suffix == ".html":
                other = parsed.get(str(path.relative_to(out.resolve())))
                if other and unquote(url.fragment) not in other.ids:
                    missing.append(filename + " → missing anchor: " + target)
    if missing:
        raise ValueError("Incomplete generated document: " + "; ".join(missing))
    return {"ok": True, "pages": len(filenames), "local_links_checked": count, "missing": [], "external_dependencies": 0}

def render_document(data, out, document_id, version, feedback=None, labels=None):
    data = normalize_content(data, data.get("kind", "portal"))
    by_id = {p["id"]: p for p in data["pages"]}
    output = out
    pages = data["pages"]
    home = data.get("home", pages[0]["id"])
    sections = data.get("sections") or [{"id": name, "title": name} for name in dict.fromkeys(p.get("section", "") for p in pages)]
    section_titles = {s["id"]: s["title"] for s in sections}
    search = [{"id": p["id"], "title": p["title"], "section": section_titles.get(p.get("section"), ""), "summary": p.get("summary", ""), "body": json.dumps(p.get("blocks", []), ensure_ascii=False)} for p in pages]
    search_json = json.dumps(search, ensure_ascii=False).replace("<", "\\u003c")
    output = Path(output)
    # Render every page before writes, so malformed content cannot publish a partial site.
    rendered = {}
    for index, page in enumerate(pages):
        navigation = []
        for section in sections:
            entries = [p for p in pages if p.get("section") == section["id"]]
            if not entries:
                continue
            active = page.get("section") == section["id"]
            navigation.append('<details class="nav-group"' + (' open' if active or page["id"] == home else '') + '><summary>' + esc(section["title"]) + '</summary>')
            for item in entries:
                depth, parent = 0, item.get("parent")
                while parent:
                    depth += 1
                    parent = by_id[parent].get("parent")
                navigation.append('<a class="nav-page" href="' + href(item["id"]) + '" style="--depth:' + str(min(depth, 3)) + '"' + (' aria-current="page"' if item["id"] == page["id"] else '') + '>' + esc(item["title"]) + '</a>')
            navigation.append('</details>')
        chain, ancestor = [], page.get("parent")
        while ancestor:
            chain.insert(0, by_id[ancestor])
            ancestor = by_id[ancestor].get("parent")
        crumbs = '<a href="index.html">首页</a>' + ''.join('<span> / </span><a href="' + href(p["id"]) + '">' + esc(p["title"]) + '</a>' for p in chain if p["id"] != home)
        if page["id"] != home:
            crumbs += '<span> / </span><span aria-current="page">' + esc(page["title"]) + '</span>'
        toc = ''.join('<a href="#b-' + esc(b["id"]) + '">' + esc(b["title"]) + '</a>' for i, b in enumerate(page.get("blocks", [])) if b.get("title"))
        body = ''.join(render_block(block, i, by_id, index, page["id"]) for i, block in enumerate(page.get("blocks", [])))
        related = ''.join('<a href="' + href(ref) + '">' + esc(by_id[ref]["title"]) + ' →</a>' for ref in page.get("related", []))
        turns = '<nav class="page-turns" aria-label="上下篇">'
        for offset, label in [(-1, '上一篇'), (1, '下一篇')]:
            target = index + offset
            if 0 <= target < len(pages):
                item = pages[target]
                turns += '<a href="' + href(item["id"]) + '"><small>' + label + '</small><strong>' + esc(item["title"]) + '</strong></a>'
            else:
                turns += '<span></span>'
        turns += '</nav>'
        title = data.get("title", "知识门户")
        rendered[page["id"] + '.html'] = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(page['title'])} · {esc(title)}</title><meta name="description" content="{esc(page.get('summary',''))}"><link rel="stylesheet" href="assets/portal.css"><script src="assets/portal.js" defer></script></head><body class="{'lumen-reading' if data.get('kind') == 'report' else ''}"><a class="skip-link" href="#main">跳到正文</a><header class="topbar"><a class="brand" href="index.html"><img class="brand-icon" src="assets/lumen-icon.png" alt="" width="32" height="32"><span>{esc(title)}</span></a><button id="menu-toggle" class="menu-toggle" aria-controls="sidebar" aria-expanded="false">目录</button><div class="search-wrap"><label class="search-box"><span aria-hidden="true">⌕</span><input id="global-search" type="search" aria-label="搜索全部页面" placeholder="搜索主题、关系与来源…" autocomplete="off"><kbd>⌘ K</kbd></label><div id="search-results" class="search-results" role="listbox" aria-label="搜索结果" hidden></div></div><span class="scope">{esc(data.get('scope','本地阅读快照'))}</span></header><div class="site-layout"><button id="sidebar-backdrop" class="sidebar-backdrop" aria-label="关闭目录" hidden></button><aside id="sidebar" class="sidebar"><div class="sidebar-intro"><strong>逐层阅读</strong><p>{esc(data.get('subtitle','主题、关系与来源逐层阅读'))}</p></div><nav aria-label="门户目录">{''.join(navigation)}</nav><div class="sidebar-foot">纯本地静态门户<br>更新：{esc(data.get('updated_at',date.today().isoformat()))}</div></aside><main id="main"><nav class="breadcrumbs" aria-label="面包屑">{crumbs}</nav><div class="document-layout"><article><header class="page-header"><span class="eyebrow">{esc(section_titles.get(page.get('section'),''))}</span>{field('h1',page['title'],'/pages/'+str(index)+'/title',stable_identity='page/'+page['id']+'/title')}{field('p',page.get('summary',''),'/pages/'+str(index)+'/summary',' class="lead"',stable_identity='page/'+page['id']+'/summary')}</header><div class="article-body">{body}</div>{('<section class="related"><h2>相关页面</h2>'+related+'</section>') if related else ''}{turns}<footer class="page-footer">{esc(data.get('footer','Lumen · 知页 · 可编辑、批注并保存'))}</footer></article><aside class="page-toc"><strong>本页内容</strong>{toc}</aside></div></main></div><script type="application/json" id="portal-index">{search_json}</script></body></html>'''
        filename = page["id"] + '.html'
        page_feedback = (feedback or {}).get(page["id"]) if isinstance(feedback, dict) else None
        rendered[filename] = enhance_html(rendered[filename], document_id, page["id"], version,
                                          feedback=page_feedback, labels=labels if labels is not None else data.get("ui_labels"),
                                          src=str(output / filename))
    rendered['index.html'] = rendered[home + '.html']
    source_assets = ASSETS
    for filename in ("portal.css", "portal.js", "edit-feedback.css", "edit-feedback.js", "lumen-icon.png"):
        if not (source_assets / filename).is_file():
            raise ValueError("Missing bundled asset: " + filename)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "assets"
    destination.mkdir(exist_ok=True)
    for filename in ("portal.css", "portal.js", "lumen-icon.png"):
        if (source_assets / filename).resolve() != (destination / filename).resolve():
            shutil.copy2(source_assets / filename, destination / filename)
    for filename, text in rendered.items():
        (output / filename).write_text(text)
    validation = validate_site(output, list(rendered))
    return {"entry": "index.html", "pages": [{"id": p["id"], "title": p["title"], "relative_path": p["id"] + ".html"} for p in pages],
            "validation": validation}
