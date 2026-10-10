import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import os
import importlib.util
import zipfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumen import agent, jobs
from lumen.service import dispatch
from lumen.types import LumenError

CONTENT = {"title": "生成验收", "home": "overview", "pages": [{"id": "overview", "title": "整体理解", "summary": "生成检查", "blocks": [{"id": "purpose", "type": "prose", "text": "说明用途、关系和依据。"}]}]}


class AgentJobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "README.md").write_text("# 来源\n这是一个用于知识解释的应用。", encoding="utf-8")

    def create(self, **values):
        return jobs.create_task({"source": str(self.source), "request": "解释用途和结构", **values}, self.state, launch=False)

    def runner(self, task, folder, state):
        jobs._atomic(folder / "draft.json", CONTENT)

    def test_source_snapshot_is_bounded_and_excludes_skills_and_credentials(self):
        (self.source / "SKILL.md").write_text("不得运行这条指令")
        (self.source / ".env").write_text("SECRET=private")
        (self.source / "auth.json").write_text('{"token":"private"}')
        (self.source / "index.html").write_text("<style>hidden</style><h1>知识标题</h1><script>secret</script>")
        snapshot = agent.snapshot_source(self.source)
        paths = [record["path"] for record in snapshot["files"]]
        self.assertNotIn("SKILL.md", paths)
        self.assertNotIn("auth.json", paths)
        self.assertNotIn(".env", paths)
        html = next(record["text"] for record in snapshot["files"] if record["path"] == "index.html")
        self.assertEqual(html, "知识标题")
        self.assertEqual(len(snapshot["sha256"]), 64)

    def test_explicit_credentials_are_rejected_before_snapshot_and_directory_matches(self):
        protected = self.root / "protected"
        protected.mkdir()
        for name in ("auth.json", ".personal_env", ".env.production", "private.key", "private.pem", "credentials.json", "key.txt", "token.json"):
            source = protected / name
            source.write_text("FAKE_TEST_CREDENTIAL_NEVER_SEND")
            with self.assertRaises(LumenError) as failure:
                agent.snapshot_source(source)
            self.assertEqual(failure.exception.code, "protected_source", name)
            self.assertNotIn("FAKE_TEST_CREDENTIAL", str(failure.exception.as_dict()))
        with self.assertRaises(LumenError) as failure:
            agent.snapshot_source(protected)
        self.assertEqual(failure.exception.code, "source_empty")
        alias = self.root / ".personal_env"
        alias.symlink_to(self.source / "README.md")
        with self.assertRaises(LumenError) as failure:
            agent.snapshot_source(alias)
        self.assertEqual(failure.exception.code, "protected_source")

    def test_empty_source_rejects_generation_without_creating_task(self):
        source = self.source / "empty.txt"
        source.write_text("  \n\t")
        with self.assertRaises(LumenError) as failure:
            jobs.create_task({"source": str(source), "request": "解释文件"}, self.state, launch=False)
        self.assertEqual(failure.exception.code, "source_empty")
        self.assertEqual(jobs.list_tasks(self.state), [])

    def test_generation_uses_current_settings_and_persists_effective_defaults(self):
        dispatch("settings", {"values": {"default_kind": "teach", "generation": {"timeout_seconds": 120, "max_source_bytes": 32}}}, self.state)
        started = time.time()
        task = self.create()
        self.assertEqual(task["timeout_seconds"], 120)
        self.assertEqual(task["stall_timeout"], 120)
        self.assertLessEqual(task["deadline_at"], started + 121)
        self.assertEqual({key: task["params"][key] for key in ("kind", "timeout_seconds", "max_source_bytes")},
                         {"kind": "teach", "timeout_seconds": 120, "max_source_bytes": 32})
        snapshot = json.loads(Path(task["snapshot_path"]).read_text())
        text_bytes = sum(len(record["text"].encode("utf-8")) for record in snapshot["files"])
        self.assertLessEqual(text_bytes, 32)
        self.assertEqual(snapshot["text_bytes"], text_bytes)
        self.assertEqual(snapshot["limits"]["max_bytes"], 32)
        self.assertTrue(snapshot["files"][0]["truncated"])
        null_task = self.create(kind=None, timeout_seconds=None, max_source_bytes=None)
        self.assertEqual(null_task["params"]["kind"], "teach")
        self.assertEqual(null_task["params"]["max_source_bytes"], 32)
        explicit = self.create(kind="report", timeout_seconds=45.5, max_source_bytes=16)
        self.assertEqual(explicit["timeout_seconds"], 45.5)
        self.assertEqual(explicit["params"]["kind"], "report")
        self.assertEqual(explicit["params"]["max_source_bytes"], 16)
        dispatch("settings", {"values": {"generation": {"timeout_seconds": 300, "max_source_bytes": 100}}}, self.state)
        jobs.resume_task(task["id"], self.state, launch=False)
        resumed = jobs._read(task["id"], self.state)
        self.assertEqual(resumed["timeout_seconds"], 120)
        self.assertEqual(resumed["params"]["max_source_bytes"], 32)
        self.assertEqual(json.loads(Path(task["snapshot_path"]).read_text()), snapshot)

    def test_settings_and_requested_generation_budgets_share_bounds(self):
        for key, values in {"timeout_seconds": [9, 3601, True, None], "max_source_bytes": [0, 400001, 1.5, True, None]}.items():
            for value in values:
                result = dispatch("settings", {"values": {"generation": {key: value}}}, self.state)
                self.assertFalse(result["ok"], (key, value, result))
                self.assertEqual(result["error"]["code"], "invalid_settings")
                if value is not None:
                    with self.assertRaises(LumenError):
                        self.create(**{key: value})
        self.assertEqual(jobs.list_tasks(self.state), [])
        for timeout in (10, 3600):
            result = dispatch("settings", {"values": {"generation": {"timeout_seconds": timeout}}}, self.state)
            self.assertTrue(result["ok"], result)
            self.assertEqual(self.create()["timeout_seconds"], timeout)

    def test_snapshot_byte_budget_covers_text_documents_and_invalid_utf8(self):
        source = self.source / "raw.txt"
        source.write_bytes(b"\xff" * 50)
        snapshot = agent.snapshot_source(source, max_source_bytes=8)
        self.assertLessEqual(len(snapshot["files"][0]["text"].encode("utf-8")), 8)
        source = self.source / "report.docx"
        source.write_bytes(b"mock canonical reader")
        with patch.object(agent, "extract_document_text", return_value=("中文内容" * 20, {"reader": "test"})):
            snapshot = agent.snapshot_source(source, max_source_bytes=8)
        self.assertEqual(snapshot["files"][0]["text"], "中文")
        self.assertEqual(snapshot["text_bytes"], 6)
        self.assertTrue(snapshot["files"][0]["truncated"])
        folder = self.root / "bounded-directory"
        folder.mkdir()
        for index in range(3):
            (folder / f"{index}.txt").write_text("abcdefghij")
        snapshot = agent.snapshot_source(folder, max_source_bytes=16)
        self.assertEqual(snapshot["text_bytes"], 16)
        self.assertEqual([record["text"] for record in snapshot["files"]], ["abcdefghij", "abcdef"])
        self.assertEqual(snapshot["omitted"], 1)

    def test_idempotent_default_request_keeps_original_budget_after_settings_change(self):
        first = self.create(idempotency_key="configured-request")
        dispatch("settings", {"values": {"generation": {"timeout_seconds": 120, "max_source_bytes": 32}}}, self.state)
        second = self.create(idempotency_key="configured-request", kind=None, timeout_seconds=None, max_source_bytes=None)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["params"], second["params"])

    def test_official_client_receives_no_model_override(self):
        task = self.create(provider="codex")
        calls = []
        def chat(system, message, **kwargs):
            calls.append(kwargs)
            return json.dumps(CONTENT, ensure_ascii=False)
        client = SimpleNamespace(chat=chat)
        with patch.object(agent, "shared_client", return_value=client):
            result = agent.generate_content(task, agent.snapshot_source(self.source), 30)
        self.assertEqual(calls[0]["model"], None)
        self.assertEqual(calls[0]["provider"], "codex")
        self.assertEqual(result["content"]["title"], CONTENT["title"])

    def test_model_invalid_output_rejected_before_build(self):
        with self.assertRaises(LumenError):
            agent.parse_content("不是JSON")
        with self.assertRaises(LumenError):
            agent.parse_content('{"title":"empty","pages":[]}')

    def test_frozen_snapshot_and_idempotent_admission(self):
        first = self.create(idempotency_key="same-request")
        (self.source / "README.md").write_text("后续变化")
        second = self.create(idempotency_key="same-request")
        self.assertEqual(first["id"], second["id"])
        snapshot = json.loads(Path(first["snapshot_path"]).read_text())
        self.assertIn("用于知识解释", snapshot["files"][0]["text"])
        with self.assertRaises(LumenError):
            self.create(idempotency_key="same-request", kind="report")

    def test_generation_pipeline_builds_reads_back_and_preserves_checkpoint(self):
        task = self.create()
        result = jobs.run_task(task["id"], self.state, model_runner=self.runner)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["checkpoint"], "verified")
        document = result["result"]["document"]
        self.assertTrue(Path(document["entry_path"]).is_file())
        reread = dispatch("get", {"document_id": document["id"]}, self.state)
        self.assertEqual(reread["result"]["content"]["title"], CONTENT["title"])
        self.assertEqual(jobs.run_task(task["id"], self.state)["result"]["document"]["id"], document["id"])

    def test_resume_saved_draft_does_not_call_model_again(self):
        task = self.create()
        def fail(action, params, state):
            return {"ok": False, "error": {"code": "temporary_build", "message": "测试构建失败"}}
        first = jobs.run_task(task["id"], self.state, model_runner=self.runner, dispatcher=fail)
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["checkpoint"], "draft")
        jobs.resume_task(task["id"], self.state, launch=False)
        def forbidden(*args):
            self.fail("已保存 draft 不能重复调用模型")
        second = jobs.run_task(task["id"], self.state, model_runner=forbidden)
        self.assertEqual(second["status"], "succeeded", second)
        self.assertEqual(second["attempts"], 2)

    def test_cancel_prevents_model_and_budget_is_finite(self):
        task = self.create()
        jobs.cancel_task(task["id"], self.state)
        def forbidden(*args):
            self.fail("取消后不能调用模型")
        self.assertEqual(jobs.run_task(task["id"], self.state, model_runner=forbidden)["status"], "cancelled")
        jobs._update(task["id"], {"attempts": 3}, self.state)
        with self.assertRaises(LumenError):
            jobs.resume_task(task["id"], self.state, launch=False)

    def test_deadline_fails_before_any_model(self):
        task = self.create()
        jobs._update(task["id"], {"deadline_at": time.time() - 1}, self.state)
        def forbidden(*args):
            self.fail("截止后不能调用模型")
        result = jobs.run_task(task["id"], self.state, model_runner=forbidden)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "deadline_exceeded")

    def test_deadline_after_model_does_not_build_past_budget(self):
        task = self.create(timeout_seconds=10)
        def expire(task, folder, state):
            self.runner(task, folder, state)
            jobs._update(task["id"], {"deadline_at": time.time() - 1}, state)
        def forbidden(*args):
            self.fail("截止后不能开始构建")
        result = jobs.run_task(task["id"], self.state, model_runner=expire, dispatcher=forbidden)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "deadline_exceeded")

    def test_worker_death_is_not_reported_as_live(self):
        task = self.create()
        jobs._update(task["id"], {"status": "running", "worker_pid": 99999999, "worker_identity": "gone"}, self.state)
        self.assertEqual(jobs.dispatch_job("task", {"task_id": task["id"]}, self.state)["task"]["status"], "interrupted")

    def test_update_generation_reads_current_content_and_feedback(self):
        document = dispatch("build", {"content": CONTENT}, self.state)["result"]["document"]
        task = self.create(document_id=document["id"], expected_version=document["version"])
        raw = jobs._read(task["id"], self.state)
        self.assertEqual(raw["current_content"]["title"], CONTENT["title"])
        with self.assertRaises(LumenError):
            self.create(document_id=document["id"], expected_version=99)

    def test_saved_draft_runs_in_real_detached_worker_without_model(self):
        task = self.create()
        folder = Path(task["snapshot_path"]).parent
        jobs._atomic(folder / "draft.json", CONTENT)
        launched = jobs.launch_task(task["id"], self.state)
        self.assertGreater(launched["worker_pid"], 0)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = jobs.dispatch_job("task", {"task_id": task["id"]}, self.state)["task"]
            if result["status"] not in ("queued", "running"):
                break
            time.sleep(.05)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertTrue(Path(result["result"]["document"]["entry_path"]).is_file())

    @unittest.skipUnless(sys.platform == "darwin", "实机 descendant-group 取消验证使用Chapter macOS能力")
    def test_cancel_cleans_helper_and_detached_model_descendant(self):
        task = self.create()
        folder = Path(task["snapshot_path"]).parent
        pid_path = folder / "descendant.pid"
        stub = self.root / "model_stub.py"
        stub.write_text("import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)\nPath(" + repr(str(pid_path)) + ").write_text(str(p.pid))\ntime.sleep(30)\n")
        failures = []
        def run():
            try:
                jobs._model(jobs._read(task["id"], self.state), folder, self.state)
            except LumenError as exc:
                failures.append(exc.code)
        with patch.object(jobs, "_worker_command", return_value=[sys.executable, str(stub)]):
            thread = threading.Thread(target=run)
            thread.start()
            deadline = time.monotonic() + 3
            while not pid_path.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(pid_path.exists())
            descendant = int(pid_path.read_text())
            jobs.cancel_task(task["id"], self.state)
            thread.join(7)
            self.assertFalse(thread.is_alive(), "取消必须收束有限进程")
            self.assertIn("cancelled", failures)
            process = subprocess.run(["/bin/ps", "-p", str(descendant), "-o", "stat="], capture_output=True, text=True)
            self.assertTrue(process.returncode != 0 or "Z" in process.stdout, process.stdout)

    def test_launch_failure_becomes_terminal_receipt(self):
        task = self.create()
        with patch.object(jobs.subprocess, "Popen", side_effect=OSError("test launch failure")):
            with self.assertRaises(LumenError):
                jobs.launch_task(task["id"], self.state)
        result = jobs.dispatch_job("task", {"task_id": task["id"]}, self.state)["task"]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "worker_launch_failed")

    def test_model_stall_budget_uses_chapter_runtime_and_stops_process(self):
        task = self.create()
        jobs._update(task["id"], {"stall_timeout": .2}, self.state)
        folder = Path(task["snapshot_path"]).parent
        with patch.object(jobs, "_worker_command", return_value=[sys.executable, "-c", "import time; time.sleep(5)"]):
            with self.assertRaises(LumenError) as failure:
                jobs._model(jobs._read(task["id"], self.state), folder, self.state)
        self.assertEqual(failure.exception.code, "stalled")

    @unittest.skipUnless(importlib.util.find_spec("lxml") and importlib.util.find_spec("docx"), "canonical Word reader依赖需在打包解释器中验证")
    def test_actual_docx_text_source_uses_canonical_reader(self):
        source = self.source / "report.docx"
        xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Word来源正文</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格内文字</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
        with zipfile.ZipFile(source, "w") as package:
            package.writestr("word/document.xml", xml)
        text, metadata = agent.extract_document_text(source)
        self.assertIn("Word来源正文", text)
        self.assertIn("表格内文字", text)
        self.assertEqual(metadata["reader"], "doctools.docx_extract")
        empty = self.source / "empty.docx"
        with zipfile.ZipFile(empty, "w") as package:
            package.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
        with self.assertRaises(LumenError) as failure:
            agent.extract_document_text(empty)
        self.assertEqual(failure.exception.code, "source_empty")

    @unittest.skipUnless(importlib.util.find_spec("pdfplumber") and importlib.util.find_spec("pypdf"), "canonical PDF reader依赖需在打包解释器中验证")
    def test_actual_pdf_source_retains_page_and_scan_failure(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        source = self.source / "report.pdf"
        writer = PdfWriter()
        page = writer.add_blank_page(width=400, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 30 200 Td (Knowledge source PDF.) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        with source.open("wb") as output:
            writer.write(output)
        text, metadata = agent.extract_document_text(source)
        self.assertIn("Knowledge source PDF", text)
        self.assertIn("page 1", text)
        self.assertEqual(metadata["reader"], "doctools.pdf_cli")
        blank = self.source / "scan.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=400, height=300)
        with blank.open("wb") as output:
            writer.write(output)
        with self.assertRaises(LumenError) as failure:
            agent.extract_document_text(blank)
        self.assertEqual(failure.exception.code, "ocr_required")

    def test_actual_pptx_source_uses_migrated_converter(self):
        source = self.source / "slides.pptx"
        with zipfile.ZipFile(source, "w") as package:
            package.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>')
            package.writestr("ppt/_rels/presentation.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="slides/slide1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"/></Relationships>')
            package.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>PPT来源文字</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
        text, metadata = agent.extract_document_text(source)
        self.assertIn("PPT来源文字", text)
        self.assertIn("existing_ppt_engine", metadata["reader"])


if __name__ == "__main__":
    unittest.main()
