"""Real file persistence, conflict protection, recovery and portable output."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from lumen.service import dispatch
from lumen.store import Store, merge_content


def sample():
    return {"title": "理解 Agent", "home": "overview", "pages": [
        {"id": "overview", "title": "整体", "summary": "从整体进入模块", "blocks": [
            {"id": "intro", "type": "prose", "title": "职责", "text": ["Agent 负责判断，程序负责执行。", "原始依据留在项目。"]},
            {"id": "links", "type": "links", "items": [{"page": "detail", "label": "继续理解", "description": "具体流程"}]}]},
        {"id": "detail", "title": "详细流程", "parent": "overview", "blocks": [
            {"id": "steps", "type": "steps", "items": [{"title": "读取资料", "text": "沿原项目入口读取事实"}, {"title": "解释成果", "text": "生成知识库页面"}]}]}
    ]}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "state"

    def tearDown(self):
        self.temporary.cleanup()

    def call(self, action, **params):
        result = dispatch(action, params, self.root)
        self.assertTrue(result["ok"], result)
        return result["result"]

    def test_human_save_reopen_agent_merge_conflict_restore_and_export(self):
        doc = self.call("build", content=sample(), project="agent", tags=["架构"])["document"]
        doc_id = doc["id"]
        first = self.call("get", document_id=doc_id)
        self.assertEqual(len(first["pages"]), 2)
        self.assertTrue(Path(doc["entry_path"]).is_file())
        state = {"comments": [{"id": "comment-1", "body": "请解释执行边界", "thread": []}]}
        doc = self.call("save", document_id=doc_id, page_id="overview", expected_version=1,
                        patches=[{"path": "/pages/0/blocks/0/text/0", "original": sample()["pages"][0]["blocks"][0]["text"][0], "value": "我的理解：先判断，再执行。"}], feedback=state)["document"]
        # Fresh Store/process view, never a cache-only persistence claim.
        reopened = Store(self.root).get(doc_id)
        self.assertEqual(reopened["content"]["pages"][0]["blocks"][0]["text"][0], "我的理解：先判断，再执行。")
        self.assertEqual(reopened["feedback"]["overview"], state)
        updated = deepcopy(sample())
        updated["pages"][1]["blocks"][0]["items"][1]["text"] = "生成 HTML 并自动入库。"
        doc = self.call("build", content=updated, document_id=doc_id, expected_version=2)["document"]
        self.assertEqual(self.call("get", document_id=doc_id)["content"]["pages"][0]["blocks"][0]["text"][0], "我的理解：先判断，再执行。")
        conflict = deepcopy(updated)
        conflict["pages"][0]["blocks"][0]["text"][0] = "Agent 新改的同一句话。"
        rejected = dispatch("build", {"content": conflict, "document_id": doc_id, "expected_version": 3}, self.root)
        self.assertEqual(rejected["error"]["code"], "merge_conflict")
        self.assertEqual(Store(self.root).document(doc_id)["version"], 3)
        stale = dispatch("save", {"document_id": doc_id, "page_id": "overview", "expected_version": 2}, self.root)
        self.assertEqual(stale["error"]["code"], "conflict")
        self.assertGreater(len(self.call("compare", document_id=doc_id, from_version=1)["changes"]), 0)
        restored = self.call("restore", document_id=doc_id, version=1, expected_version=3)["document"]
        self.assertEqual(restored["version"], 4)
        self.assertEqual(len(self.call("versions", document_id=doc_id)["versions"]), 4)
        self.assertIn("Agent 负责判断", self.call("get", document_id=doc_id)["content"]["pages"][0]["blocks"][0]["text"][0])
        out = Path(self.temporary.name) / "knowledge.zip"
        self.call("export", document_id=doc_id, output=str(out))
        with zipfile.ZipFile(out) as archive:
            self.assertIn("site/index.html", archive.namelist())
            self.assertIn("content.json", archive.namelist())

    def test_plain_html_import_resources_and_snapshot_save(self):
        source = Path(self.temporary.name) / "source"
        source.mkdir()
        (source / "style.css").write_text("body{color:#123}")
        (source / "index.html").write_text('<html><head><title>阅读稿</title><link rel="stylesheet" href="style.css"></head><body><main><h1>阅读稿</h1><p id="intro">我需要理解具体成果。</p><a href="detail.html">继续</a></main></body></html>')
        (source / "detail.html").write_text('<html><title>细节</title><main><p>关键依据</p></main></html>')
        doc = self.call("import", path=str(source / "index.html"))["document"]
        self.assertFalse(doc["structured"])
        self.assertEqual(doc["page_count"], 2)
        page = next(page for page in self.call("get", document_id=doc["id"])["pages"] if page["relative_path"] == "index.html")
        self.assertTrue((Path(doc["entry_path"]).parent / "style.css").is_file())
        original = Path(page["absolute_path"]).read_text()
        self.call("save", document_id=doc["id"], page_id=page["id"], expected_version=1, html=original.replace("我需要理解具体成果。", "保存我的认识。"), feedback={})
        fresh = Store(self.root).get(doc["id"])
        self.assertIn("保存我的认识。", Path(fresh["document"]["entry_path"]).read_text())
        self.assertIn("我需要理解具体成果。", (source / "index.html").read_text())

    def test_search_management_positions_and_index_recovery(self):
        doc = self.call("build", content=sample())["document"]
        self.assertTrue(self.call("search", query="沿原项目入口")["matches"])
        self.call("metadata", document_id=doc["id"], favorite=True, tags=["知识库"], notes="我自己的笔记")
        self.assertEqual(self.call("list", tag="知识库", favorite=True)["documents"][0]["id"], doc["id"])
        self.call("position", document_id=doc["id"], page_id="detail", scroll_y=430)
        self.assertEqual(self.call("position", document_id=doc["id"])["position"]["scroll_y"], 430)
        with Store(self.root).connect() as db:
            db.execute("DELETE FROM page_index")
        self.assertFalse(self.call("search", query="沿原项目入口")["matches"])
        self.call("reindex")
        self.assertTrue(self.call("search", query="沿原项目入口")["matches"])
        self.call("metadata", document_id=doc["id"], archived=True)
        self.assertFalse(self.call("list")["documents"])
        self.assertFalse(self.call("search", query="Agent")["matches"])

    def test_idempotency_and_source_path_safety(self):
        first = self.call("build", content=sample(), idempotency_key="stable-generation")["document"]
        second = self.call("build", content=sample(), idempotency_key="stable-generation")["document"]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.call("list")["documents"]), 1)
        self.assertFalse(dispatch("get", {"document_id": "../../escape"}, self.root)["ok"])
        bad = dispatch("save", {"document_id": first["id"], "page_id": "not-in-doc", "expected_version": 1}, self.root)
        self.assertEqual(bad["error"]["code"], "invalid_page")

    def test_configuration_runtime_override_and_corrupt_fallback(self):
        self.call("settings", values={"font_size": 22, "labels": {"portal": "我的门户"}})
        fresh = self.call("settings")["settings"]
        self.assertEqual(fresh["font_size"], 22)
        self.assertEqual(fresh["labels"]["portal"], "我的门户")
        config = Path(self.temporary.name) / "preferences.json"
        self.call("config-export", output=str(config))
        self.call("settings", values={"font_size": 15})
        self.call("config-import", source=str(config))
        self.assertEqual(self.call("settings")["settings"]["font_size"], 22)
        (self.root / "settings.json").write_text("broken")
        result = self.call("settings")
        self.assertEqual(result["settings"]["font_size"], 17)
        self.assertTrue(result["warning"])

    def test_merge_deletion_and_reordered_stable_ids(self):
        base = {"pages": [{"id": "a", "text": "before"}, {"id": "b", "text": "second"}]}
        current = deepcopy(base)
        current["pages"][0]["text"] = "mine"
        incoming = {"pages": [{"id": "b", "text": "new second"}, {"id": "a", "text": "before"}], "extra": "added"}
        merged, conflicts = merge_content(base, current, incoming)
        self.assertFalse(conflicts)
        self.assertEqual(merged["pages"][1]["text"], "mine")
        self.assertEqual(merged["extra"], "added")

    def test_numeric_table_edit_preserves_type_and_rejects_non_finite(self):
        content = sample()
        content["pages"][0]["blocks"].append({"id": "counts", "type": "table", "headers": ["任务", "数量"], "rows": [["已完成", 8]]})
        doc = self.call("build", content=content)["document"]
        saved = self.call("save", document_id=doc["id"], page_id="overview", expected_version=1, patches=[{"path": "/pages/0/blocks/2/rows/0/1", "original": 8, "value": 9}])
        self.assertEqual(Store(self.root).get(doc["id"])["content"]["pages"][0]["blocks"][2]["rows"][0][1], 9)
        bad = dispatch("save", {"document_id": doc["id"], "page_id": "overview", "expected_version": 2, "patches": [{"path": "/pages/0/blocks/2/rows/0/1", "original": 9, "value": float("nan")}]}, self.root)
        self.assertEqual(bad["error"]["code"], "invalid_patch")

    def test_reimport_same_source_preserves_user_version_and_reuses_item(self):
        source = Path(self.temporary.name) / "reading.html"
        source.write_text('<html><title>知识</title><main><p>原稿</p></main></html>')
        first = self.call("import", path=str(source))["document"]
        page = self.call("get", document_id=first["id"])["pages"][0]
        snapshot = Path(page["absolute_path"]).read_text().replace("原稿", "我保存的认识")
        self.call("save", document_id=first["id"], page_id=page["id"], expected_version=1, html=snapshot)
        source.write_text('<html><title>知识</title><main><p>来源变了</p></main></html>')
        reopened = self.call("import", path=str(source))
        self.assertTrue(reopened["reused"])
        self.assertEqual(reopened["document"]["id"], first["id"])
        self.assertEqual(reopened["document"]["version"], 2)
        self.assertIn("我保存的认识", Path(reopened["document"]["entry_path"]).read_text())
        self.assertEqual(len(self.call("list")["documents"]), 1)

    def test_document_index_loss_recovers_personal_properties_and_source(self):
        source = Path(self.temporary.name) / "reading.html"
        source.write_text('<html><title>持久知识</title><main><p>具体依据</p></main></html>')
        document = self.call("import", path=str(source))["document"]
        before = (Store(self.root).version_root(document["id"], 1) / "metadata.json").read_bytes()
        self.call("metadata", document_id=document["id"], favorite=True, project="我的项目", tags=["要积累"], notes="我自己的理解")
        with Store(self.root).connect() as db:
            db.execute("DELETE FROM documents")
            db.execute("DELETE FROM page_index")
        self.call("reindex")
        recovered = self.call("get", document_id=document["id"])["document"]
        self.assertTrue(recovered["favorite"])
        self.assertEqual(recovered["notes"], "我自己的理解")
        self.assertEqual(recovered["project"], "我的项目")
        self.assertEqual(recovered["source_path"], str(source.resolve()))
        self.assertTrue(self.call("search", query="具体依据")["matches"])
        self.assertEqual(before, (Store(self.root).version_root(document["id"], 1) / "metadata.json").read_bytes())
        self.assertTrue(self.call("import", path=str(source))["reused"])

    def test_saved_import_file_base_remains_portable_after_export(self):
        source = Path(self.temporary.name) / "portable"
        source.mkdir()
        (source / "index.html").write_text('<html><head><title>离线</title><link rel="stylesheet" href="style.css"></head><body><p>正文</p><a href="detail.html">细节</a></body></html>')
        (source / "style.css").write_text("body{color:navy}")
        (source / "detail.html").write_text('<html><title>细节</title><p>依据</p></html>')
        doc = self.call("import", path=str(source))["document"]
        page = next(p for p in self.call("get", document_id=doc["id"])["pages"] if p["relative_path"] == "index.html")
        snapshot = Path(page["absolute_path"]).read_text().replace("</head>", '<base href="' + Path(page["absolute_path"]).as_uri() + '"></head>')
        saved = self.call("save", document_id=doc["id"], page_id=page["id"], expected_version=1, html=snapshot)["document"]
        markup = Path(saved["entry_path"]).read_text()
        self.assertIn('<base href="index.html">', markup)
        self.assertNotIn('href="file:', markup)
        output = Path(self.temporary.name) / "offline.zip"
        self.call("export", document_id=doc["id"], output=str(output))
        with zipfile.ZipFile(output) as archive:
            exported = archive.read("site/index.html").decode()
            self.assertIn('<base href="index.html">', exported)
            self.assertIn("site/style.css", archive.namelist())
            self.assertIn("site/detail.html", archive.namelist())

    def test_library_rename_and_classification_survive_save_build_and_restore(self):
        doc = self.call("build", content=sample())["document"]
        self.call("metadata", document_id=doc["id"], title="我的阅读标题", project="当前项目", tags=["当前主题"])
        saved = self.call("save", document_id=doc["id"], page_id="overview", expected_version=1, patches=[], feedback={})["document"]
        self.assertEqual(saved["title"], "我的阅读标题")
        rebuilt = self.call("build", content=sample(), document_id=doc["id"], expected_version=2)["document"]
        self.assertEqual(rebuilt["title"], "我的阅读标题")
        restored = self.call("restore", document_id=doc["id"], version=1, expected_version=3)["document"]
        self.assertEqual((restored["title"], restored["project"], restored["tags"]), ("我的阅读标题", "当前项目", ["当前主题"]))
        version_root = Store(self.root).document_root(doc["id"]) / "versions"
        (version_root / ".DS_Store").write_bytes(b"metadata")
        (version_root / "readme.txt").write_text("extra")
        self.assertEqual(len(self.call("versions", document_id=doc["id"])["versions"]), 4)

    def test_search_targets_exact_paragraph_and_position_survives_index_loss(self):
        doc = self.call("build", content=sample())["document"]
        match = self.call("search", query="沿原项目入口读取事实")["matches"][0]
        markup = Path(match["path"]).read_text()
        self.assertIn('data-feedback-block="' + match["anchor"] + '"', markup)
        self.call("position", document_id=doc["id"], page_id="detail", scroll_y=820, anchor=match["anchor"])
        with Store(self.root).connect() as db:
            db.execute("DELETE FROM positions")
        self.assertEqual(self.call("position", document_id=doc["id"])["position"], {"page_id": "detail", "scroll_y": 820, "anchor": match["anchor"]})

    def test_over_budget_html_is_rejected_before_registration(self):
        source = Path(self.temporary.name) / "too-large.html"
        with source.open("wb") as stream:
            stream.write(b"<html><title>Large</title></html>")
            stream.truncate(30 * 1024 * 1024 + 1)
        result = dispatch("import", {"path": str(source)}, self.root)
        self.assertEqual(result["error"]["code"], "package_limit")
        self.assertEqual(self.call("list")["documents"], [])

    def test_metadata_revision_rejects_stale_notes_without_staling_content(self):
        document = self.call("build", content=sample())["document"]
        self.assertEqual(document["metadata_revision"], 0)
        first = self.call("metadata", document_id=document["id"], expected_version=1,
                          expected_metadata_revision=0, notes="第一个客户端的认识")["document"]
        self.assertEqual((first["version"], first["metadata_revision"]), (1, 1))
        second = dispatch("metadata", {"document_id": document["id"], "expected_version": 1,
                                      "expected_metadata_revision": 0, "notes": "过期的笔记"}, self.root)
        self.assertEqual(second["error"]["code"], "conflict")
        self.assertEqual(second["error"]["details"]["current_metadata_revision"], 1)
        legacy = dispatch("metadata", {"document_id": document["id"], "expected_version": 1,
                                      "notes": "旧客户端的笔记"}, self.root)
        self.assertEqual(legacy["error"]["code"], "conflict")
        self.assertEqual(Store(self.root).document(document["id"])["notes"], "第一个客户端的认识")
        favorite = self.call("metadata", document_id=document["id"], expected_version=1,
                             expected_metadata_revision=1, favorite=True)["document"]
        self.assertEqual((favorite["version"], favorite["metadata_revision"]), (1, 2))
        # A reader of the unchanged page still has a valid content token.
        saved = self.call("save", document_id=document["id"], page_id="overview", expected_version=1,
                          patches=[], feedback={})["document"]
        self.assertEqual((saved["version"], saved["metadata_revision"]), (2, 2))
        self.assertEqual(saved["notes"], "第一个客户端的认识")

    def test_metadata_revision_survives_build_restore_reindex_and_legacy_migration(self):
        document = self.call("build", content=sample())["document"]
        store = Store(self.root)
        original_content = store.get(document["id"])["content"]
        # Existing libraries did not have this field in their persisted metadata.
        properties = store.document_root(document["id"]) / "properties.json"
        old = json.loads(properties.read_text())
        old.pop("metadata_revision")
        properties.write_text(json.dumps(old))
        with store.connect() as db:
            db.execute("UPDATE documents SET metadata=? WHERE id=?", (json.dumps(old), document["id"]))
        self.assertEqual(Store(self.root).get(document["id"])["document"]["metadata_revision"], 0)
        self.assertEqual(self.call("list")["documents"][0]["metadata_revision"], 0)
        metadata = self.call("metadata", document_id=document["id"], expected_metadata_revision=0,
                             notes="持久笔记", archived=True)["document"]
        self.assertEqual(metadata["metadata_revision"], 1)
        rebuilt = self.call("build", content=sample(), document_id=document["id"], expected_version=1)["document"]
        self.assertEqual((rebuilt["metadata_revision"], rebuilt["notes"]), (1, "持久笔记"))
        restored = self.call("restore", document_id=document["id"], version=1, expected_version=2)["document"]
        self.assertEqual((restored["metadata_revision"], restored["notes"], restored["archived"]), (1, "持久笔记", True))
        self.call("metadata", document_id=document["id"], expected_version=3, expected_metadata_revision=1,
                  title="当前全局标题", project="当前项目", tags=["现在"], favorite=True)
        historic = self.call("get", document_id=document["id"], version=1)
        self.assertEqual(historic["document"]["version"], 1)
        self.assertEqual(historic["content"], original_content)
        self.assertEqual((historic["document"]["metadata_revision"], historic["document"]["notes"], historic["notes"]), (2, "持久笔记", "持久笔记"))
        self.assertEqual((historic["document"]["title"], historic["document"]["project"], historic["document"]["tags"], historic["document"]["favorite"]), ("当前全局标题", "当前项目", ["现在"], True))
        with store.connect() as db:
            db.execute("DELETE FROM documents")
        self.call("reindex")
        recovered = Store(self.root).document(document["id"])
        self.assertEqual((recovered["metadata_revision"], recovered["notes"]), (2, "持久笔记"))
        stale = dispatch("metadata", {"document_id": document["id"], "expected_metadata_revision": 0, "notes": "旧值"}, self.root)
        self.assertEqual(stale["error"]["code"], "conflict")

    def test_build_classification_changes_revision_and_rejects_metadata_only_stale_token(self):
        document = self.call("build", content=sample(), project="原项目", tags=["原标签"])["document"]
        changed = self.call("build", content=sample(), document_id=document["id"], expected_version=1,
                            project="新项目", tags=["新标签"])["document"]
        self.assertEqual((changed["version"], changed["metadata_revision"]), (2, 1))
        stale = dispatch("metadata", {"document_id": document["id"], "expected_metadata_revision": 0,
                                      "project": "原项目", "tags": ["原标签"]}, self.root)
        self.assertEqual(stale["error"]["code"], "conflict")
        self.assertEqual(stale["error"]["details"]["current_metadata_revision"], 1)
        same = self.call("build", content=sample(), document_id=document["id"], expected_version=2,
                         project="新项目", tags=["新标签"])["document"]
        self.assertEqual((same["version"], same["metadata_revision"]), (3, 1))
        unchanged = self.call("metadata", document_id=document["id"], expected_metadata_revision=1,
                              project="新项目", tags=["新标签"], favorite=False, notes="")["document"]
        self.assertEqual(unchanged, same)
        body = self.call("save", document_id=document["id"], page_id="overview", expected_version=3,
                         patches=[{"path": "/pages/0/blocks/0/text/0", "value": "只修改正文。"}])["document"]
        self.assertEqual((body["version"], body["metadata_revision"]), (4, 1))
        self.assertEqual((body["project"], body["tags"]), ("新项目", ["新标签"]))

    def test_generated_saved_and_restored_title_changes_revision_only_when_library_title_changes(self):
        document = self.call("build", content=sample())["document"]
        generated = deepcopy(sample())
        generated["title"] = "生成的新标题"
        built = self.call("build", content=generated, document_id=document["id"], expected_version=1)["document"]
        self.assertEqual((built["title"], built["metadata_revision"]), ("生成的新标题", 1))
        restored = self.call("restore", document_id=document["id"], version=1, expected_version=2)["document"]
        self.assertEqual((restored["title"], restored["metadata_revision"]), (sample()["title"], 2))
        stale = dispatch("metadata", {"document_id": document["id"], "expected_metadata_revision": 1,
                                      "title": "过期客户端的标题"}, self.root)
        self.assertEqual(stale["error"]["code"], "conflict")
        saved = self.call("save", document_id=document["id"], page_id="overview", expected_version=3,
                          patches=[{"path": "/title", "value": "手动标题"}])["document"]
        self.assertEqual((saved["version"], saved["title"], saved["metadata_revision"]), (4, "手动标题", 3))
        pinned = self.call("metadata", document_id=document["id"], expected_metadata_revision=3,
                           title="手动标题")["document"]
        self.assertEqual(pinned["metadata_revision"], 3)
        rebuilt = self.call("build", content=sample(), document_id=document["id"], expected_version=4)["document"]
        self.assertEqual((rebuilt["title"], rebuilt["metadata_revision"]), ("手动标题", 3))
        restored = self.call("restore", document_id=document["id"], version=1, expected_version=5)["document"]
        self.assertEqual((restored["title"], restored["metadata_revision"]), ("手动标题", 3))

    def test_historic_get_pairs_current_generated_title_with_current_metadata_token(self):
        document = self.call("build", content=sample())["document"]
        generated = deepcopy(sample())
        generated["title"] = "当前生成标题"
        current = self.call("build", content=generated, document_id=document["id"], expected_version=1)["document"]
        self.assertNotIn("title_override", current)
        historic = self.call("get", document_id=document["id"], version=1)
        self.assertEqual((historic["document"]["title"], historic["document"]["metadata_revision"]), ("当前生成标题", 1))
        self.assertEqual((historic["content"]["title"], historic["document"]["version"]), (sample()["title"], 1))
        self.assertTrue(all(page["version"] == 1 for page in historic["pages"]))
        # Round-tripping personal properties read alongside the history token
        # must not restore the historic generated title into the current library.
        properties = {key: historic["document"][key] for key in ("title", "project", "tags", "notes", "favorite", "archived")}
        written = self.call("metadata", document_id=document["id"],
                            expected_metadata_revision=historic["document"]["metadata_revision"], **properties)["document"]
        self.assertEqual((written["title"], written["version"], written["metadata_revision"]), ("当前生成标题", 2, 1))
        self.assertEqual(Store(self.root).get(document["id"])["content"]["title"], "当前生成标题")

    def test_import_srcset_inline_styles_css_imports_and_base_resolution(self):
        source = Path(self.temporary.name) / "assets-source"
        assets = source / "assets"
        style = assets / "style"
        style.mkdir(parents=True)
        (source / "index.html").write_text('''<html><head><title>完整资源</title><base href="assets/">
          <link href="style/main.css" rel="stylesheet"><style>/* url(ignored.png) */
          .inline{background:url("inline image.png")}</style></head><body>
          <picture><source srcset="data:image/png;base64,AAAA 1x, high.png 2x">
          <img src="low.png" srcset="low.png 1x, high.png 2x" style="background:url('attribute.png')"></picture></body></html>''')
        (style / "main.css").write_text("""/* @import 'ignored.css'; */
          @import 'theme file.css'; @import url(extra.css) screen;
          .a{background:url('../icon\\20 name.svg');content:'url(ignored-again.png)'}""")
        (style / "theme file.css").write_text(".b{background:url(../escaped\\).png)}")
        (style / "extra.css").write_text(".c{background:url(/* inline comment */'../nested.png')}")
        for name in ("low.png", "high.png", "inline image.png", "attribute.png", "icon name.svg", "escaped).png", "nested.png"):
            (assets / name).write_bytes(b"resource")
        imported = self.call("import", path=str(source))
        self.assertTrue(imported["validation"]["ok"], imported["validation"])
        site = Path(imported["document"]["entry_path"]).parent
        for name in ("low.png", "high.png", "inline image.png", "attribute.png", "icon name.svg", "escaped).png", "nested.png"):
            self.assertEqual((site / "assets" / name).read_bytes(), b"resource")
        for name in ("theme file.css", "extra.css"):
            self.assertTrue((site / "assets" / "style" / name).is_file())
        self.assertEqual(imported["validation"]["files"], 11)
        # The same base semantics work for an absolute file base and remain portable.
        other = Path(self.temporary.name) / "absolute-base"
        (other / "assets").mkdir(parents=True)
        (other / "assets" / "image.png").write_bytes(b"picture")
        (other / "index.html").write_text('<html><title>绝对基址</title><base href="' + (other / "assets").as_uri() + '/"><img src="image.png"></html>')
        absolute = self.call("import", path=str(other))
        self.assertTrue(absolute["validation"]["ok"])
        self.assertIn('<base href="assets/">', Path(absolute["document"]["entry_path"]).read_text())

    def test_import_missing_complex_resources_are_warnings_and_symlinks_stay_excluded(self):
        source = Path(self.temporary.name) / "missing-source"
        source.mkdir()
        (source / "index.html").write_text('''<html><title>资源缺失</title><style>.x{background:url(inline-missing.png)}</style>
          <img srcset="high-missing.png 2x"><link href="main.css"><img src="alias/secret.png"></html>''')
        (source / "main.css").write_text("@import 'theme-missing.css';")
        private = source / "real"
        private.mkdir()
        (private / "secret.png").write_bytes(b"private")
        (source / "alias").symlink_to(private, target_is_directory=True)
        imported = self.call("import", path=str(source))
        self.assertFalse(imported["validation"]["ok"])
        paths = {Path(item["path"]).name for item in imported["validation"]["warnings"]}
        self.assertEqual(paths, {"inline-missing.png", "high-missing.png", "theme-missing.css", "secret.png"})
        self.assertFalse((Path(imported["document"]["entry_path"]).parent / "real" / "secret.png").exists())

    def test_export_missing_invalid_incomplete_and_failed_versions_leave_no_output(self):
        document = self.call("build", content=sample())["document"]
        output = Path(self.temporary.name) / "failed.zip"
        for version, code in ((999, "not_found"), (0, "invalid_version"), (-1, "invalid_version"), (True, "invalid_version")):
            rejected = dispatch("export", {"document_id": document["id"], "output": str(output), "version": version}, self.root)
            self.assertEqual(rejected["error"]["code"], code)
            self.assertFalse(output.exists())
        with patch("zipfile.ZipFile.write", side_effect=OSError("copy failed")):
            failed = dispatch("export", {"document_id": document["id"], "output": str(output)}, self.root)
        self.assertEqual(failed["error"]["code"], "io_error")
        self.assertFalse(output.exists())
        self.assertEqual(list(output.parent.glob(".lumen-export-*")), [])
        output.write_bytes(b"existing export")
        rejected = dispatch("export", {"document_id": document["id"], "output": str(output)}, self.root)
        self.assertEqual(rejected["error"]["code"], "file_exists")
        self.assertEqual(output.read_bytes(), b"existing export")
        output.unlink()
        current = Store(self.root).get(document["id"])
        Path(current["pages"][0]["absolute_path"]).unlink()
        incomplete = dispatch("export", {"document_id": document["id"], "output": str(output)}, self.root)
        self.assertEqual(incomplete["error"]["code"], "not_found")
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
