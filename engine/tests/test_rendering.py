"""Source mapping, offline delivery, identity, and safe feedback compiler checks."""
import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
import unittest

from lumen.rendering import enhance_html, normalize_content, render_document, script_json, validate_site


def sample():
    return {"title": "Agent 成果库", "home": "overview", "pages": [
        {"id": "overview", "title": "整体理解", "summary": "先理解结论", "blocks": [
            {"id": "purpose", "type": "prose", "title": "用途", "text": "第一段 **解释**。\n\n第二段。"},
            {"type": "steps", "title": "工作过程", "items": [{"title": "先读来源", "text": ["资料 A", "资料 B"]}, "生成后检查"]},
            {"type": "table", "title": "能力对照", "headers": ["能力", "作用"], "rows": [["阅读", "理解成果"], ["编辑", ["修改", "保存"]]]},
            {"type": "diagram", "title": "关系", "nodes": [{"id": "reader", "label": "阅读", "page": "details"}, {"id": "source", "label": "来源"}], "edges": [{"from": "source", "to": "reader"}], "caption": "正常门户关系图保留"},
            {"type": "links", "title": "逐层阅读", "items": [{"page": "details", "label": "深入细节", "description": "查看依据"}]},
            {"type": "evidence", "title": "依据", "items": [{"label": "资料", "value": "来源事实", "path": "/项目/资料.md", "url": "https://example.com/evidence", "state": "已核实"}]},
            {"type": "callout", "text": "适用边界"},
            {"type": "code", "title": "接口", "text": "lumen build --content content.json"},
            {"type": "faq", "title": "常见疑问", "items": [{"question": "如何保存？", "answer": "在 App 中按 ⌘S。"}]},
        ]},
        {"id": "details", "title": "来源与细节", "parent": "overview", "summary": "继续读", "blocks": [{"type": "prose", "text": "细节正文"}]},
    ]}


class _Fields(HTMLParser):
    def __init__(self):
        super().__init__(); self.fields = []; self.ids = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("data-lumen-source"):
            self.fields.append((tag, attrs["data-lumen-source"], json.loads(attrs["data-lumen-original"])))
            self.ids.append(attrs["data-feedback-block"])


class RenderingTests(unittest.TestCase):
    def test_all_three_modes_compile_offline_with_valid_source_fields(self):
        for mode in ("portal", "report", "teach"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                data = normalize_content(sample(), mode)
                result = render_document(data, temporary, "doc-stable", 4)
                self.assertTrue(result["validation"]["ok"])
                self.assertEqual(result["entry"], "index.html")
                self.assertEqual(result["validation"]["external_dependencies"], 0)
                self.assertEqual(len(result["pages"]), 2)
                html = (Path(temporary) / "index.html").read_text()
                self.assertIn("<svg", html)
                self.assertIn('src="assets/lumen-icon.png"', html)
                self.assertEqual((Path(temporary) / "assets/lumen-icon.png").read_bytes(),
                                 (Path(__file__).resolve().parents[1] / "lumen/assets/lumen-icon.png").read_bytes())
                self.assertIn("window.LumenReader", html)
                self.assertIn('data-lumen-source="/pages/0/title"', html)
                self.assertRegex(html, r'<code class="source-path" data-feedback-object[^>]*data-lumen-source="/pages/0/blocks/5/items/0/path"')
                parser = _Fields(); parser.feed(html)
                self.assertEqual(len(set(parser.ids)), len(parser.ids))
                self.assertGreater(len(parser.fields), 20)
                self.assertIn(("h1", "/pages/0/title", "整体理解"), parser.fields)
                self.assertIn(("p", "/pages/0/summary", "先理解结论"), parser.fields)
                for _, pointer, original in parser.fields:
                    value = data
                    for part in pointer.strip("/").split("/"):
                        value = value[int(part)] if isinstance(value, list) else value[part]
                    self.assertEqual(value, original)
                self.assertNotRegex(html, r'<(?:script|link)[^>]+(?:src|href)=["\']https?://')

    def test_normalization_does_not_mutate_and_identities_survive_rebuild(self):
        original = sample(); before = copy.deepcopy(original)
        normalized = normalize_content(original)
        self.assertEqual(original, before)
        self.assertEqual(normalized, normalize_content(normalized))
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            render_document(normalized, folder / "v1", "same-doc", 1)
            normalized["pages"][0]["blocks"][0]["text"] = "人工修改后的内容\n\n第二段。"
            render_document(normalized, folder / "v2", "same-doc", 2)
            ids = []
            for version in ("v1", "v2"):
                parser = _Fields(); parser.feed((folder / version / "overview.html").read_text())
                ids.append(parser.ids)
            self.assertEqual(ids[0], ids[1])

    def test_feedback_survives_rebuild_and_dangerous_text_remains_data(self):
        danger = "</script><img src=x onerror=alert(1)>-->"
        state = {"page_id": "overview", "round": 1, "comments": [{"id": "u1", "body": danger}], "blocks": {}}
        with tempfile.TemporaryDirectory() as temporary:
            render_document(sample(), temporary, "doc", 3, {"overview": state})
            page = (Path(temporary) / "overview.html").read_text()
            packed = re.search(r'id="html-feedback-state" type="application/json">(.*?)</script>', page, re.S)[1]
            self.assertEqual(json.loads(packed), state)
            self.assertNotIn("<img src=x", packed)
            init = json.loads(re.search(r'id="html-feedback-init" type="application/json">(.*?)</script>', page, re.S)[1])
            self.assertEqual((init["document_id"], init["page_id"], init["version"]), ("doc", "overview", 3))
            self.assertIn("lumenSave", page)
        self.assertEqual(json.loads(script_json({"danger": danger}))["danger"], danger)

    def test_page_and_block_reordering_keeps_feedback_on_the_same_content(self):
        data = normalize_content(sample())
        data["pages"][0]["blocks"][0]["text"] = "被批注的解释"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            render_document(data, root / "before", "doc", 1)
            before = _Fields(); before.feed((root / "before" / "overview.html").read_text())
            marked = next(identity for identity, (_, pointer, value) in zip(before.ids, before.fields)
                          if pointer.endswith("/text") and value == "被批注的解释")
            comments = {"overview": {"page_id": "overview", "comments": [{"id": "u1", "block_id": marked, "body": "这里补上依据"}], "blocks": {}}}
            data["pages"][0]["blocks"] = list(reversed(data["pages"][0]["blocks"]))
            data["pages"] = list(reversed(data["pages"]))
            render_document(data, root / "after", "doc", 2, comments)
            page = (root / "after" / "overview.html").read_text()
            after = _Fields(); after.feed(page)
            located = [(pointer, value) for identity, (_, pointer, value) in zip(after.ids, after.fields) if identity == marked]
            self.assertEqual(located, [("/pages/1/blocks/8/text", "被批注的解释")])
            state = json.loads(re.search(r'id="html-feedback-state" type="application/json">(.*?)</script>', page, re.S)[1])
            self.assertEqual(state["comments"][0]["block_id"], marked)
            self.assertEqual(state["comments"][0]["body"], "这里补上依据")

    def test_existing_html_mount_is_idempotent_and_keeps_native_objects(self):
        original = '<!doctype html><html><head><title>旧页面</title></head><body><main><h1>旧标题</h1><p>旧正文</p><p contenteditable="true">既有编辑器</p><svg><text>架构图</text></svg></main></body></html>'
        one = enhance_html(original, "old-doc", "page", 1)
        two = enhance_html(one, "old-doc", "page", 2)
        self.assertEqual(two.count('id="html-feedback-core"'), 1)
        self.assertIn('contenteditable="true">既有编辑器', two)
        self.assertIn("<svg><text>架构图</text></svg>", two)
        init = json.loads(re.search(r'id="html-feedback-init" type="application/json">(.*?)</script>', two, re.S)[1])
        self.assertEqual(init["version"], 2)

    def test_runtime_labels_change_presentation_without_mutating_content(self):
        data = normalize_content(sample())
        baseline = copy.deepcopy(data)
        with tempfile.TemporaryDirectory() as temporary:
            render_document(data, temporary, "doc", 1, labels={"save": "存入我的知识库"})
            page = (Path(temporary) / "overview.html").read_text()
            init = json.loads(re.search(r'id="html-feedback-init" type="application/json">(.*?)</script>', page, re.S)[1])
            self.assertEqual(init["labels"]["save"], "存入我的知识库")
            self.assertEqual(data, baseline)

    def test_invalid_content_fails_before_writing_pages(self):
        cases = []
        data = sample(); data["pages"][1]["parent"] = "missing"; cases.append(data)
        data = sample(); data["pages"][0]["parent"] = "details"; cases.append(data)
        data = sample(); data["pages"][0]["blocks"][0]["type"] = "html"; cases.append(data)
        data = sample(); data["pages"][0]["blocks"][3]["edges"][0]["to"] = "missing"; cases.append(data)
        data = sample(); data["pages"][0]["blocks"][5]["items"][0]["url"] = "javascript:alert(1)"; cases.append(data)
        for data in cases:
            with tempfile.TemporaryDirectory() as temporary:
                out = Path(temporary) / "site"
                with self.assertRaises(ValueError):
                    render_document(data, out, "doc", 1)
                self.assertFalse(out.exists())

    def test_real_validator_detects_missing_resource_and_anchor(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            render_document(sample(), out, "doc", 1)
            (out / "assets" / "portal.js").unlink()
            with self.assertRaisesRegex(ValueError, "portal.js"):
                validate_site(out, ["overview.html"])
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            render_document(sample(), out, "doc", 1)
            path = out / "overview.html"
            path.write_text(path.read_text().replace('href="#main"', 'href="#missing-anchor"'))
            with self.assertRaisesRegex(ValueError, "missing anchor"):
                validate_site(out, ["overview.html", "details.html"])


if __name__ == "__main__":
    unittest.main()
