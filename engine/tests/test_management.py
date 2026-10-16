import json
from pathlib import Path
import tempfile
import unittest

from lumen.service import dispatch
from lumen.store import Store


CONTENT={"title":"项目说明","home":"one","pages":[{"id":"one","title":"第一页","blocks":[{"id":"intro","type":"prose","text":"管理测试内容"}]},{"id":"two","title":"第二页","blocks":[{"type":"prose","text":"第二页结论"}]}]}


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name).resolve()
        self.state=self.root/"state"; self.store=Store(self.state)
        self.source=self.root/"原始.html"; self.source.write_text('<html><title>原始</title><p>保留原件</p></html>')
        self.doc=self.store.build(CONTENT,sources=[{"path":str(self.source)}])["document"]

    def tearDown(self): self.temp.cleanup()

    def call(self,operation,**params):
        result=dispatch(operation,params,str(self.state))
        self.assertTrue(result["ok"],result)
        return result["result"]

    def test_trash_rebuild_restore_all_personal_state(self):
        doc=self.store.metadata(self.doc["id"],notes="笔记",favorite=True,project="项目",tags=["主题"])["document"]
        self.store.position(doc["id"],page_id="two",scroll_y=150)
        self.call("trash",document_id=doc["id"],expected_version=doc["version"],expected_metadata_revision=doc["metadata_revision"])
        self.assertEqual(self.store.list()["documents"],[])
        self.assertEqual(self.store.search("管理测试")["matches"],[])
        self.assertEqual(len(self.store.list(trashed=True,archived=None)["documents"]),1)
        # Losing the entire derived database must not resurrect deleted items.
        self.store.db_path.unlink(); self.store=Store(self.state); self.store.rebuild_index()
        self.assertEqual(self.store.list()["documents"],[])
        restored=self.call("untrash",document_id=doc["id"])["document"]
        for key in ("id","version","notes","favorite","project","tags"): self.assertEqual(restored[key],doc[key])
        self.assertEqual(self.store.position(doc["id"])["position"]["page_id"],"two")
        self.assertTrue(self.source.exists())

    def test_purge_guard_and_no_resurrection(self):
        failed=dispatch("purge",{"document_id":self.doc["id"],"confirmed":True},str(self.state))
        self.assertEqual(failed["error"]["code"],"not_trashed")
        self.call("trash",document_id=self.doc["id"])
        self.assertFalse(dispatch("purge",{"document_id":self.doc["id"]},str(self.state))["ok"])
        self.call("purge",document_id=self.doc["id"],confirmed=True)
        self.assertFalse(self.store.document_root(self.doc["id"]).exists())
        self.assertTrue(self.source.exists())
        self.store.rebuild_index()
        self.assertEqual(self.store.list(trashed=None,archived=None)["documents"],[])
        self.assertFalse(dispatch("build",{"document_id":self.doc["id"],"expected_version":1,"content":CONTENT},str(self.state))["ok"])
        self.call("purge",document_id=self.doc["id"],confirmed=True)

    def test_trash_blocks_old_content_writers(self):
        self.call("trash",document_id=self.doc["id"])
        result=dispatch("build",{"document_id":self.doc["id"],"expected_version":1,"content":CONTENT},str(self.state))
        self.assertEqual(result["error"]["code"],"document_trashed")

    def test_rename_preserves_id_versions_and_conflicts(self):
        result=self.call("rename",document_id=self.doc["id"],title=" 新标题 ",expected_version=1,expected_metadata_revision=0)["document"]
        self.assertEqual(result["id"],self.doc["id"]); self.assertEqual(result["version"],1); self.assertEqual(result["title"],"新标题")
        stale=dispatch("rename",{"document_id":self.doc["id"],"title":"旧窗口","expected_version":1,"expected_metadata_revision":0},str(self.state))
        self.assertFalse(stale["ok"])
        self.assertFalse(dispatch("rename",{"document_id":self.doc["id"],"title":" "},str(self.state))["ok"])
        synced=self.call("rename",document_id=self.doc["id"],title="同步正文",sync_content_title=True,expected_version=1,expected_metadata_revision=1)["document"]
        self.assertEqual(synced["version"],2)
        self.assertEqual(self.store.get(synced["id"])["content"]["title"],"同步正文")
        self.assertEqual(self.store.get(synced["id"],1)["content"]["title"],"项目说明")

    def test_paths_resolve_current_page_and_old_version(self):
        before=self.call("paths",document_id=self.doc["id"],page_id="two")
        self.assertTrue(before["current_html"]["path"].endswith("two.html"))
        self.assertTrue(before["entry_html"]["path"].endswith("index.html"))
        self.assertEqual(before["sources"][0]["path"],str(self.source))
        self.store.save_document(self.doc["id"],"two",1,patches=[])
        current=self.call("paths",document_id=self.doc["id"],page_id="two")
        old=self.call("paths",document_id=self.doc["id"],page_id="two",version=1)
        self.assertNotEqual(current["current_html"]["path"],old["current_html"]["path"])
        self.source.unlink()
        self.assertFalse(self.call("paths",document_id=self.doc["id"])["sources"][0]["exists"])

    def test_duplicate_has_independent_identity_and_opt_in_notes(self):
        self.store.metadata(self.doc["id"],notes="私有笔记")
        self.store.save_document(self.doc["id"],"one",1,feedback={"comments":[{"body":"意见"}]})
        copy=self.call("duplicate",document_id=self.doc["id"])["document"]
        self.assertNotEqual(copy["id"],self.doc["id"]); self.assertEqual(copy["notes"],"")
        self.assertEqual(self.store.get(copy["id"])["feedback"],{})
        full=self.call("duplicate",document_id=self.doc["id"],include_notes=True,include_feedback=True,include_history=True)["document"]
        self.assertEqual(full["notes"],"私有笔记"); self.assertEqual(len(self.store.versions(full["id"])["versions"]),2)
        self.assertIn("one",self.store.get(full["id"])["feedback"])

    def test_file_rename_survives_save_and_old_version(self):
        renamed=self.call("rename-file",document_id=self.doc["id"],page_id="two",filename="第二章.html",expected_version=1)["document"]
        current=self.store.get(renamed["id"])
        page=next(p for p in current["pages"] if p["id"]=="two")
        self.assertEqual(page["relative_path"],"第二章.html")
        self.assertTrue(Path(page["absolute_path"]).is_file())
        self.assertTrue((self.store.version_root(renamed["id"],1)/"site"/"two.html").is_file())
        self.store.save_document(renamed["id"],"two",2,patches=[])
        saved=self.store.get(renamed["id"])
        self.assertEqual(next(p for p in saved["pages"] if p["id"]=="two")["relative_path"],"第二章.html")
        self.assertTrue((self.store.version_root(renamed["id"],3)/"site"/"two.html").is_file())

    def test_source_relink_history_survives_rebuild(self):
        doc=self.store.metadata(self.doc["id"],source_path=str(self.source))["document"]
        moved=self.root/"新原始.html"; self.source.rename(moved)
        self.call("relink-source",document_id=doc["id"],source=str(moved),expected_version=1,expected_metadata_revision=doc["metadata_revision"])
        self.store.db_path.unlink(); self.store=Store(self.state); self.store.rebuild_index()
        info=self.call("paths",document_id=doc["id"])
        self.assertEqual([s["path"] for s in info["sources"]],[str(moved)])
        self.assertEqual(self.store.document(doc["id"])["source_history"][0]["path"],str(self.source))

    def test_batch_reports_partial_success(self):
        result=self.call("batch",document_ids=[self.doc["id"],"doc-missing"],action="trash")
        self.assertEqual(result["succeeded"],1); self.assertEqual(result["failed"],1)
        self.assertEqual(result["results"][1]["error"]["code"],"not_found")

    def test_duplicate_import_explicit_update_and_copy(self):
        imported=self.store.import_document(self.source)["document"]
        self.source.write_text('<html><title>原始</title><p>新来源内容</p></html>')
        self.assertEqual(self.store.import_document(self.source)["document"]["version"],1)
        updated=self.store.import_document(self.source,on_duplicate="update",expected_version=1)["document"]
        self.assertEqual(updated["id"],imported["id"]); self.assertEqual(updated["version"],2)
        copy=self.store.import_document(self.source,on_duplicate="copy")["document"]
        self.assertNotEqual(copy["id"],imported["id"])
        self.assertTrue(self.source.exists())

    def test_external_edit_copy_updates_same_document_and_stable_pages(self):
        doc=self.store.metadata(self.doc["id"],notes="原来的笔记",source_path=str(self.source))["document"]
        self.store.save_document(doc["id"],"one",1,feedback={"comments":[{"body":"保留批注"}]})
        folder=self.root/"编辑副本"
        self.call("export-folder",document_id=doc["id"],output=str(folder))
        page=folder/"site"/"two.html"; page.write_text(page.read_text().replace("第二页结论","编辑器修改后的结论"))
        imported=self.store.import_document(folder,document_id=doc["id"],on_duplicate="update",expected_version=2)["document"]
        self.assertEqual(imported["id"],doc["id"]); self.assertEqual(imported["version"],3); self.assertEqual(imported["notes"],"原来的笔记")
        self.assertEqual(imported["source_path"],str(self.source)); self.assertTrue(self.source.exists())
        current=self.store.get(doc["id"])
        self.assertEqual([p["id"] for p in current["pages"]],["one","two"])
        self.assertIn("one",current["feedback"])
        text=self.call("read",document_id=doc["id"],scope="document")["text"]
        self.assertIn("编辑器修改后的结论",text)
        self.assertEqual(len(self.store.versions(doc["id"])["versions"]),3)

    def test_conflicting_external_home_edits_leave_original_unchanged(self):
        folder=self.root/"conflicting-copy"
        self.call("export-folder",document_id=self.doc["id"],output=str(folder))
        for name,value in (("index.html","入口独立改动"),("one.html","首页独立改动")):
            page=folder/"site"/name; page.write_text(page.read_text().replace("管理测试内容",value))
        failed=dispatch("import",{"path":str(folder),"document_id":self.doc["id"],"on_duplicate":"update","expected_version":1},str(self.state))
        self.assertEqual(failed["error"]["code"],"edited_copy_conflict")
        self.assertEqual(self.store.document(self.doc["id"])["version"],1)

    def test_purge_rejects_symlink_before_tombstone(self):
        self.call("trash",document_id=self.doc["id"])
        root=self.store.document_root(self.doc["id"]); moved=root.with_name("original-managed-root"); root.rename(moved); root.symlink_to(moved,target_is_directory=True)
        result=dispatch("purge",{"document_id":self.doc["id"],"confirmed":True},str(self.state))
        self.assertEqual(result["error"]["code"],"path_rejected")
        self.assertFalse((self.state/"purged"/(self.doc["id"]+".json")).exists())
        self.assertTrue((moved/"versions"/"1"/"site"/"index.html").is_file())


if __name__=="__main__": unittest.main()
