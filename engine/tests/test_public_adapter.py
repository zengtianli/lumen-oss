"""Verify that the public text lane is bounded and cannot pick a private model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import llm_client
from lumen import agent


class PublicAdapterTests(unittest.TestCase):
    def test_codex_invocation_inherits_model_sends_source_on_stdin_and_records_session(self):
        seen = {}
        def launch(command, **kwargs):
            seen.update(command=command, **kwargs)
            return type("Child", (), {"returncode": 0})()
        def communicate(child, prompt, **kwargs):
            seen["prompt"] = prompt
            answer = Path(seen["command"][seen["command"].index("--output-last-message") + 1])
            answer.write_text('{"title":"fictional","pages":[]}')
            return json.dumps({"type": "thread.started", "thread_id": "fictional-session"}), ""
        receipt = {}
        with patch.object(llm_client.shutil, "which", return_value="/example/codex"), \
             patch.object(llm_client, "_codex_text_flags", return_value=[]), \
             patch.object(llm_client.subprocess, "Popen", side_effect=launch), \
             patch.object(llm_client, "communicate_bounded", side_effect=communicate):
            result = llm_client.chat("system", "fictional source", provider="codex", origin_receipt=receipt)
        self.assertNotIn("--model", seen["command"])
        self.assertNotIn("fictional source", seen["command"])
        self.assertIn("fictional source", seen["prompt"])
        self.assertIn("read-only", seen["command"])
        self.assertTrue(seen["start_new_session"])
        self.assertEqual(receipt["session_id"], "fictional-session")
        self.assertIn("fictional", result)
        self.assertFalse(Path(seen["cwd"]).exists())

    def test_claude_error_flag_is_rejected_even_with_zero_exit(self):
        child = type("Child", (), {"returncode": 0})()
        with patch.object(llm_client.shutil, "which", return_value="/example/claude"), \
             patch.object(llm_client.subprocess, "Popen", return_value=child), \
             patch.object(llm_client, "communicate_bounded", return_value=('{"is_error":true,"result":"fake failure"}', "")):
            with self.assertRaises(ConnectionError):
                llm_client.chat("s", "m", provider="claude")

    def test_timeout_terminates_process_and_has_no_private_diagnostic(self):
        seen = {}
        real_popen = subprocess.Popen
        def launch_real(command, **kwargs):
            seen["child"] = real_popen([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)
            return seen["child"]
        with patch.object(llm_client.shutil, "which", return_value="/example/claude"), \
             patch.object(llm_client.subprocess, "Popen", side_effect=launch_real):
            with self.assertRaises(ConnectionError) as failure:
                llm_client.chat("s", "m", provider="claude", timeout=.2)
        self.assertIsNotNone(seen["child"].poll())
        self.assertIn("budget", str(failure.exception))

    def test_missing_cli_fails_without_launch_or_automatic_login(self):
        with patch.object(llm_client.shutil, "which", return_value=None), \
             patch.object(llm_client.subprocess, "Popen") as launch:
            with self.assertRaises(ConnectionError):
                llm_client.chat("s", "m", provider="codex")
            launch.assert_not_called()

    def test_explicit_adapter_directory_has_priority_over_bundled_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "llm_client.py").write_text('def provider_settings(): return {"preferred":"fixture"}\n')
            with patch.dict(os.environ, {"LUMEN_LLM_CLIENT_DIR": temporary}):
                self.assertEqual(agent.shared_client().provider_settings()["preferred"], "fixture")
