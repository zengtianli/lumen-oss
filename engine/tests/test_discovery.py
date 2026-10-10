from pathlib import Path
import tempfile
import unittest
from lumen.service import dispatch


class DiscoveryTests(unittest.TestCase):
    def test_portal_is_one_entry_and_test_html_is_excluded_without_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = root / "sources"; sources.mkdir()
            (sources / "report.html").write_text("<title>理解测试用例</title><main><p>解释成果</p></main>")
            (sources / "pelican.html").write_text("<title>鹈鹕骑车SVG测试</title>")
            portal = sources / "架构门户"; portal.mkdir()
            (portal / "index.html").write_text("<title>架构门户</title><main><p>整体</p></main>")
            (portal / "detail.html").write_text("<title>内部章节</title><main><p>细节</p></main>")
            (portal / "content.json").write_text('{"pages":[{"id":"overview"},{"id":"detail"}]}')
            before = {str(p): p.read_bytes() for p in sources.rglob("*") if p.is_file()}
            result = dispatch("discover", {"roots": [str(sources)]}, root / "state")
            self.assertTrue(result["ok"], result)
            entries = result["result"]["candidates"]
            self.assertEqual({item["title"] for item in entries}, {"理解测试用例", "架构门户"})
            self.assertEqual(next(item for item in entries if item["title"] == "架构门户")["page_count"], 2)
            self.assertFalse(dispatch("list", {}, root / "state")["result"]["documents"])
            self.assertEqual(before, {str(p): p.read_bytes() for p in sources.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
