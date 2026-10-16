import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from lumen.jobs import create_ask, run_task
from lumen.mcp import handle_message
from lumen.service import dispatch
from lumen.store import Store


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.state=self.root/"state"; self.store=Store(self.state)

    def tearDown(self): self.temp.cleanup()

    def call(self,action,**params):
        result=dispatch(action,params,str(self.state)); self.assertTrue(result["ok"],result); return result["result"]

    def content(self,count=8):
        return {"title":"阅读说明","home":"p0","pages":[{"id":"p"+str(i),"title":"第 "+str(i+1)+" 页","blocks":[{"type":"prose","text":"章节内容 "+str(i)},{"type":"table","headers":["指标","数值"],"rows":[["流量",i+10]]},{"type":"code","text":"line1\nline2","language":"python"}]} for i in range(count)]}

    def test_all_pages_tables_code_no_silent_truncation(self):
        doc=self.store.build(self.content())["document"]
        result=self.call("read",document_id=doc["id"],scope="document")
        self.assertEqual(result["coverage"]["extracted_pages"],8); self.assertTrue(result["coverage"]["complete"])
        for i in range(8): self.assertIn("章节内容 "+str(i),result["text"])
        self.assertIn("| 流量 | 17 |",result["text"]); self.assertIn("```python\nline1\nline2",result["text"])
        truncated=self.call("read",document_id=doc["id"],scope="document",max_chars=100)
        self.assertTrue(truncated["coverage"]["truncated"]); self.assertFalse(truncated["coverage"]["complete"])
        self.assertFalse(dispatch("export-text",{"document_id":doc["id"],"scope":"document","max_chars":100,"output":str(self.root/"bad.md")},str(self.state))["ok"])
        self.assertFalse((self.root/"bad.md").exists())

    def test_imported_html_preserves_structures_and_flags_images(self):
        original=self.root/"sample.html"; image=self.root/"pic.png"; image.write_bytes(b"test-image")
        original.write_text('<html><title>报告</title><body><nav>重复导航</nav><main><h1>结论</h1><p>正文内容</p><table><tr><th>项目</th><th>数值</th></tr><tr><td rowspan="2">流量</td><td>12.5</td></tr><tr><td>18</td></tr></table><pre><code class="language-py">a = 1\nb = 2</code></pre><img src="pic.png" alt="流量曲线"><canvas aria-label="动态图"></canvas><script>不应交给AI</script></main></body></html>')
        doc=self.store.import_document(original)["document"]
        self.assertIsNone(self.store.get(doc["id"])["content"])
        result=self.call("read",document_id=doc["id"],scope="document")
        self.assertIn("正文内容",result["text"]); self.assertIn("| 流量 | 18 |",result["text"]); self.assertIn("a = 1\nb = 2",result["text"])
        self.assertNotIn("不应交给AI",result["text"]); self.assertNotIn("重复导航",result["text"])
        self.assertTrue(result["coverage"]["complete"]); self.assertFalse(result["coverage"]["visual_complete"])
        self.assertEqual(result["pages"][0]["tables"][0]["rows"][1][0]["rowspan"],2)
        self.assertEqual({o["kind"] for o in result["coverage"]["omissions"]},{"image","canvas"})

    def test_missing_page_reports_incomplete_and_does_not_export_full(self):
        doc=self.store.build(self.content(2))["document"]
        (self.store.version_root(doc["id"],1)/"site"/"p1.html").unlink()
        result=self.call("read",document_id=doc["id"],scope="document")
        self.assertEqual(result["coverage"]["extracted_pages"],1); self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["errors"][0]["page_id"],"p1")
        self.assertFalse(dispatch("prepare-ai",{"document_id":doc["id"],"scope":"document"},str(self.state))["ok"])

    def test_notes_opt_in_and_local_material_is_version_bound(self):
        doc=self.store.build(self.content(1))["document"]
        self.store.metadata(doc["id"],notes="私有笔记")
        with patch.dict("os.environ",{"LUMEN_STATE_DIR":str(self.state)}):
            material=self.call("prepare-ai",document_id=doc["id"],page_id="p0",scope="document")
        self.assertNotIn("私有笔记",Path(material["path"]).read_text())
        self.assertIn("lumen read "+doc["id"]+" --version 1",material["copy_text"])
        self.assertIn("--scope document",material["copy_text"]); self.assertNotIn("--page-id",material["copy_text"])
        self.assertIn("--state-dir",material["copy_text"])
        notes=self.call("prepare-ai",document_id=doc["id"],include_notes=True)
        self.assertIn("私有笔记",Path(notes["path"]).read_text())
        self.assertIn("--include-notes",notes["copy_text"])
        self.store.metadata(doc["id"],notes="更新笔记")
        updated=self.call("prepare-ai",document_id=doc["id"],include_notes=True)
        self.assertNotEqual(notes["path"],updated["path"]); self.assertIn("更新笔记",Path(updated["path"]).read_text())
        selected=self.call("prepare-ai",document_id=doc["id"],page_id="p0",scope="selection",selection="只读这个数值")
        self.assertNotIn("读取命令：lumen read",selected["copy_text"])
        self.assertIn("只读这个数值",Path(selected["path"]).read_text())

    def test_attachment_is_portable_and_mcp_and_cli_read_agree(self):
        doc=self.store.build(self.content(2))["document"]
        doc=self.store.save_document(doc["id"],"p0",1,feedback={"comments":[{"body":"私有批注不应默认带出"}]})["document"]
        output=self.root/"阅读.zip"
        result=self.call("prepare-ai",document_id=doc["id"],scope="document",mode="attachment",output=str(output))
        with zipfile.ZipFile(output) as archive:
            self.assertIn("html/index.html",archive.namelist()); self.assertIn("content.md",archive.namelist())
            manifest=json.loads(archive.read("manifest.json")); self.assertEqual(manifest["coverage"]["extracted_pages"],2)
            self.assertNotIn(str(self.state),json.dumps(manifest))
            self.assertFalse(any("私有批注不应默认带出" in archive.read(n).decode("utf-8") for n in archive.namelist() if n.endswith((".html",".md"))))
        included=self.root/"含批注.zip"
        self.call("prepare-ai",document_id=doc["id"],scope="document",mode="attachment",output=str(included),include_feedback=True)
        with zipfile.ZipFile(included) as archive: self.assertIn("私有批注不应默认带出",archive.read("content.md").decode())
        params={"document_id":doc["id"],"scope":"document"}
        mcp=handle_message({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lumen_read","arguments":params}},str(self.state))
        cli=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/"entry.py"),"read",doc["id"],"--scope","document","--state-dir",str(self.state),"--json"],capture_output=True,text=True)
        self.assertEqual(cli.returncode,0,cli.stderr)
        self.assertEqual(json.loads(cli.stdout)["result"]["text"],mcp["result"]["structuredContent"]["result"]["text"])

    def test_ai_task_preview_does_not_write_or_register_document(self):
        doc=self.store.build(self.content(1))["document"]
        task=create_ask({"document_id":doc["id"],"request":"概括当前页"},str(self.state),launch=False)
        answer={"title":"AI 回答","home":"answer","pages":[{"id":"answer","title":"结论","blocks":[{"type":"prose","text":"流量为 10，见第 1 页。"}]}]}
        def model(task,folder,state): (folder/"draft.json").write_text(json.dumps(answer,ensure_ascii=False))
        result=run_task(task["id"],str(self.state),model_runner=model)
        self.assertEqual(result["status"],"succeeded",result)
        self.assertTrue(result["result"]["preview_only"]); self.assertIn("流量为 10",result["result"]["answer"])
        self.assertEqual(self.store.document(doc["id"])["version"],1)
        self.assertEqual(len(self.store.list()["documents"]),1)

    def test_rendered_view_is_explicit_and_does_not_poison_saved_cache(self):
        doc=self.store.build(self.content(1))["document"]
        rendered=self.call("read",document_id=doc["id"],page_id="p0",rendered_html='<html><body><main><h1>动态视图</h1><p>脚本生成的数值 42</p></main></body></html>')
        self.assertIn("脚本生成的数值 42",rendered["text"]); self.assertEqual(rendered["pages"][0]["reader"],"rendered_view")
        saved=self.call("read",document_id=doc["id"],page_id="p0")
        self.assertNotIn("动态视图",saved["text"]); self.assertEqual(saved["pages"][0]["reader"],"structured_content")
        self.assertFalse(dispatch("read",{"document_id":doc["id"],"scope":"document","rendered_html":"<p>动态</p>"},str(self.state))["ok"])

    def test_standalone_html_inlines_unquoted_resources_and_preserves_script(self):
        import base64
        (self.root/"pic.png").write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jF3sAAAAASUVORK5CYII='))
        (self.root/"nested.css").write_text('p{color:blue}')
        (self.root/"style.css").write_text('@import "nested.css"; body{background-image:url(pic.png)}')
        html=self.root/"single.html"
        html.write_text('<!doctype html><html><head><title>独立页</title><link rel=stylesheet href=style.css></head><body><main><p id="p">独立正文</p><img src=pic.png><a href="single.html#p">回正文</a></main><script>const src="literal-must-remain";</script></body></html>')
        doc=self.store.import_document(html)["document"]
        exported=self.root/"exported.html"
        result=self.call("export-page",document_id=doc["id"],output=str(exported))
        text=exported.read_text()
        self.assertTrue(result["standalone"]); self.assertIn('data:image/png;base64,',text); self.assertIn('p{color:blue}',text)
        self.assertIn('const src="literal-must-remain";',text); self.assertIn('href="#p"',text)
        self.assertNotIn('href=style.css',text); self.assertNotIn('src=pic.png',text)

    def test_multi_page_requires_package_and_complete_folder_opens(self):
        doc=self.store.build(self.content(2))["document"]
        failed=dispatch("export-page",{"document_id":doc["id"],"output":str(self.root/"bad.html")},str(self.state))
        self.assertEqual(failed["error"]["code"],"needs_package"); self.assertFalse((self.root/"bad.html").exists())
        result=self.call("export-folder",document_id=doc["id"],output=str(self.root/"portable"))
        self.assertTrue(Path(result["entry_path"]).is_file()); self.assertTrue((self.root/"portable"/"site"/"p1.html").is_file())
        self.assertFalse(dispatch("export-folder",{"document_id":doc["id"],"output":str(self.root/"portable")},str(self.state))["ok"])

    def test_export_refuses_to_mutate_managed_history(self):
        doc=self.store.build(self.content(1))["document"]
        target=self.store.version_root(doc["id"],1)/"site"/"new.md"
        failed=dispatch("export-text",{"document_id":doc["id"],"output":str(target)},str(self.state))
        self.assertEqual(failed["error"]["code"],"managed_output"); self.assertFalse(target.exists())


if __name__=="__main__": unittest.main()
