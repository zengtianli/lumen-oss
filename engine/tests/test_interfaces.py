import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumen.api import create_server
from lumen.cli import main, schema
from lumen.mcp import handle_message, run_stdio, tool_specs
from lumen.service import ACTIONS, dispatch
from lumen.types import LumenError
from lumen import jobs

CONTENT = {"title": "接口验收", "home": "overview", "pages": [{"id": "overview", "title": "整体理解", "summary": "接口解释", "blocks": [{"id": "purpose", "type": "prose", "text": "同一份知识通过 App、CLI、API 和 MCP 阅读。"}]}]}


class Interfaces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = str(Path(self.tmp.name) / "state")

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--state-dir", self.state, *args, "--json"])
        return code, json.loads(output.getvalue())

    def build(self):
        result = dispatch("build", {"content": CONTENT}, self.state)
        self.assertTrue(result["ok"], result)
        return result["result"]["document"]

    def test_cli_rpc_and_natural_commands_share_actual_library(self):
        code, result = self.cli("build", "--content", json.dumps(CONTENT, ensure_ascii=False))
        self.assertEqual(code, 0, result)
        document = result["result"]["document"]
        code, reread = self.cli("rpc", "get", "--params", json.dumps({"document_id": document["id"]}))
        self.assertEqual(code, 0)
        self.assertEqual(reread["result"]["content"]["title"], CONTENT["title"])
        code, found = self.cli("search", "同一份知识")
        self.assertEqual(code, 0)
        self.assertEqual(found["result"]["matches"][0]["document_id"], document["id"])

    def test_cli_state_flag_after_command_and_conflict_nonzero(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["status", "--state-dir", self.state, "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["result"]["state_dir"], str(Path(self.state).resolve()))
        document = self.build()
        code, result = self.cli("save", document["id"], "overview", "--expected-version", "999", "--patches", "[]")
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])

    def test_cli_bad_json_is_structured_failure(self):
        code, result = self.cli("rpc", "status", "--params", "[")
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "invalid_request")

    def test_rpc_large_json_parameter_file_avoids_argv_limit(self):
        content = {"title": "大页面参数文件", "home": "overview", "pages": [{"id": "overview", "title": "完整正文", "blocks": [{"id": "body", "type": "prose", "text": "x" * 1_200_000}]}]}
        payload = Path(self.tmp.name) / "large-params.json"
        payload.write_text(json.dumps({"content": content}), encoding="utf-8")
        payload.chmod(0o600)
        entry = Path(__file__).resolve().parents[1] / "entry.py"
        process = subprocess.run([sys.executable, str(entry), "rpc", "build", "--params", str(payload), "--state-dir", self.state, "--json"], capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        document = json.loads(process.stdout)["result"]["document"]
        result = dispatch("get", {"document_id": document["id"]}, self.state)
        self.assertEqual(len(result["result"]["content"]["pages"][0]["blocks"][0]["text"]), 1_200_000)
        self.assertIn("file", schema()["params_transport"])

    def test_api_auth_rpc_readback_and_origin(self):
        token = "test-local-token-" + "x" * 32
        server = create_server(self.state, token=token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:" + str(server.server_address[1])
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(base + "/api/status")
        self.assertEqual(rejected.exception.code, 401)
        request = urllib.request.Request(base + "/api/rpc", data=json.dumps({"action": "build", "params": {"content": CONTENT}}).encode(), headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        result = json.load(urllib.request.urlopen(request))
        self.assertTrue(result["ok"])
        doc_id = result["result"]["document"]["id"]
        request = urllib.request.Request(base + "/api/documents/" + doc_id, headers={"Authorization": "Bearer " + token})
        self.assertEqual(json.load(urllib.request.urlopen(request))["result"]["document"]["title"], CONTENT["title"])
        request = urllib.request.Request(base + "/api/status", headers={"Authorization": "Bearer " + token, "Origin": "https://example.org"})
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request)
        self.assertEqual(rejected.exception.code, 403)
        with self.assertRaises(LumenError):
            create_server(self.state, host="0.0.0.0")

    def test_api_schema_and_bad_request(self):
        server = create_server(self.state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:" + str(server.server_address[1])
        headers = {"Authorization": "Bearer " + server.lumen_token}
        request = urllib.request.Request(base + "/api/schema", headers=headers)
        result = json.load(urllib.request.urlopen(request))
        self.assertEqual(result["result"]["content_example"]["home"], "overview")
        request = urllib.request.Request(base + "/api/rpc", data=b"[]", headers=headers)
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request)
        self.assertEqual(rejected.exception.code, 400)

    def test_mcp_stdio_build_actual_library_and_rpc_errors(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "lumen_build", "arguments": {"content": CONTENT}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "lumen_get", "arguments": {}}},
        ]
        output = io.StringIO()
        run_stdio(self.state, io.StringIO("\n".join(json.dumps(message) for message in messages)), output)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 4)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertFalse(responses[2]["result"]["isError"])
        doc_id = responses[2]["result"]["structuredContent"]["result"]["document"]["id"]
        self.assertTrue(dispatch("get", {"document_id": doc_id}, self.state)["ok"])
        self.assertEqual(responses[3]["error"]["code"], -32602)

    def test_tool_schema_covers_every_business_action(self):
        self.assertEqual({tool["name"][6:] for tool in tool_specs()}, set(ACTIONS))
        self.assertEqual(schema()["model_policy"], "用户共享配置；不固定型号；已有内容使用 build 不调用模型")

    def test_generate_cli_and_mcp_use_settings_without_launching_model(self):
        source = Path(self.tmp.name) / "source.txt"
        source.write_text("配置控制来源预算。" * 100, encoding="utf-8")
        configured = dispatch("settings", {"values": {"default_kind": "teach", "generation": {"timeout_seconds": 120, "max_source_bytes": 32}}}, self.state)
        self.assertTrue(configured["ok"], configured)
        def launch_readback(task_id, state_dir):
            return jobs._public(jobs._read(task_id, state_dir))
        with patch.object(jobs, "launch_task", side_effect=launch_readback):
            code, result = self.cli("generate", "--source", str(source), "--request", "解释来源")
            self.assertEqual(code, 0, result)
            task = result["result"]["task"]
            self.assertEqual(task["params"]["kind"], "teach")
            self.assertEqual(task["timeout_seconds"], 120)
            self.assertEqual(task["params"]["max_source_bytes"], 32)
            code, result = self.cli("generate", "--source", str(source), "--request", "解释来源", "--kind", "report", "--timeout-seconds", "45", "--max-source-bytes", "16")
            self.assertEqual(code, 0, result)
            self.assertEqual(result["result"]["task"]["params"]["kind"], "report")
            self.assertEqual(result["result"]["task"]["params"]["max_source_bytes"], 16)
            response = handle_message({"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": "lumen_generate", "arguments": {"source": str(source), "request": "解释来源", "kind": None, "timeout_seconds": None, "max_source_bytes": None}}}, self.state)
            self.assertFalse(response["result"]["isError"], response)
            task = response["result"]["structuredContent"]["result"]["task"]
            self.assertEqual(task["params"]["kind"], "teach")
            self.assertEqual(task["timeout_seconds"], 120)
            self.assertEqual(task["params"]["max_source_bytes"], 32)
        tools = {tool["name"]: tool for tool in schema()["tools"]}
        budget = tools["lumen_generate"]["inputSchema"]["properties"]["max_source_bytes"]
        self.assertEqual((budget["minimum"], budget["maximum"]), (1, 400000))

    def test_api_generate_uses_same_defaults_and_explicit_source_budget(self):
        source = Path(self.tmp.name) / "api-source.txt"
        source.write_text("0123456789" * 20)
        dispatch("settings", {"values": {"generation": {"timeout_seconds": 120, "max_source_bytes": 32}}}, self.state)
        server = create_server(self.state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:" + str(server.server_address[1]) + "/api/rpc"
        payload = {"action": "generate", "params": {"source": str(source), "request": "解释来源", "max_source_bytes": 16}}
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + server.lumen_token, "Content-Type": "application/json"})
        with patch.object(jobs, "launch_task", side_effect=lambda task_id, state_dir: jobs._public(jobs._read(task_id, state_dir))):
            result = json.load(urllib.request.urlopen(request))
        self.assertTrue(result["ok"], result)
        task = result["result"]["task"]
        self.assertEqual(task["timeout_seconds"], 120)
        snapshot = json.loads(Path(task["snapshot_path"]).read_text())
        self.assertEqual(snapshot["files"][0]["text"], "0123456789012345")
        self.assertEqual(snapshot["text_bytes"], 16)

    def test_metadata_cli_and_mcp_expose_revision_and_reject_stale_notes(self):
        document = self.build()
        original_revision = document["metadata_revision"]
        code, first = self.cli("metadata", document["id"], "--expected-version", str(document["version"]),
                               "--expected-metadata-revision", str(original_revision), "--notes", "最新笔记")
        self.assertEqual(code, 0, first)
        self.assertEqual(first["result"]["document"]["metadata_revision"], original_revision + 1)
        response = handle_message({"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {"name": "lumen_metadata", "arguments": {"document_id": document["id"], "expected_version": document["version"], "expected_metadata_revision": original_revision, "notes": "旧客户端笔记"}}}, self.state)
        self.assertTrue(response["result"]["isError"], response)
        failure = response["result"]["structuredContent"]["error"]
        self.assertEqual(failure["details"]["current_metadata_revision"], original_revision + 1)
        current = dispatch("get", {"document_id": document["id"]}, self.state)["result"]
        self.assertEqual(current["notes"], "最新笔记")
        code, result = self.cli("metadata", document["id"], "--favorite")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["result"]["document"]["favorite"])
        self.assertEqual(result["result"]["document"]["metadata_revision"], original_revision + 2)
        metadata_schema = next(tool for tool in schema()["tools"] if tool["name"] == "lumen_metadata")
        self.assertIn("expected_metadata_revision", metadata_schema["inputSchema"]["properties"])

    def test_guides_are_queryable_without_loading_skills(self):
        code, result = self.cli("guide")
        self.assertEqual(code, 0, result)
        topics = result["result"]["topics"]
        self.assertTrue(topics)
        code, body = self.cli("guide", topics[0]["id"])
        self.assertEqual(code, 0, body)
        self.assertTrue(body["result"]["body"].strip())
        self.assertEqual(body["result"]["runtime_owner"], "Lumen")
        response = handle_message({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "lumen_guide", "arguments": {"topic": topics[0]["id"]}}}, self.state)
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"]["result"]["body"], body["result"]["body"])

    def test_discover_is_bounded_read_only_and_excludes_svg_tests(self):
        source = Path(self.tmp.name) / "knowledge"
        source.mkdir()
        (source / "report.html").write_text('<html><head><title>重要成果</title></head><body><h1>成果解释</h1></body></html>')
        (source / "svg-test.html").write_text('<html><head><title>svg test</title></head><body>测试</body></html>')
        code, result = self.cli("discover", "--roots", str(source), "--limit", "20")
        self.assertEqual(code, 0, result)
        candidates = result["result"]["candidates"]
        self.assertEqual([Path(row["path"]).name for row in candidates], ["report.html"])
        self.assertEqual(dispatch("list", {}, self.state)["result"]["documents"], [])
        response = handle_message({"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "lumen_discover", "arguments": {"roots": [str(source)], "limit": 20}}}, self.state)
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(len(response["result"]["structuredContent"]["result"]["candidates"]), 1)

    def test_real_stdio_process_has_no_stdout_noise(self):
        entry = Path(__file__).resolve().parents[1] / "entry.py"
        request = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "lumen_status", "arguments": {}}})
        process = subprocess.run([sys.executable, str(entry), "mcp", "--state-dir", self.state], input=request + "\n", capture_output=True, text=True, timeout=5)
        self.assertEqual(process.returncode, 0, process.stderr)
        response = json.loads(process.stdout)
        self.assertEqual(response["id"], 7)
        self.assertTrue(response["result"]["structuredContent"]["ok"])


if __name__ == "__main__":
    unittest.main()
