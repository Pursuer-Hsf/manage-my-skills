import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [
    ROOT / "README.md",
    ROOT / "docs" / "README.zh-CN.md",
    ROOT / "docs" / "README.ja.md",
]
COMMANDS = {
    "doctor", "scan", "setup", "connect", "import",
    "status", "sync", "restore", "update-manager",
}
LOCAL_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")
PRIVATE_MARKERS = [
    re.compile(r"/Users/[^/\s]+/"),
    re.compile(r"/home/[^/\s]+/"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"),
]


class DocumentationTests(unittest.TestCase):
    def test_language_documents_exist_and_link_to_each_other(self):
        for document in DOCUMENTS:
            self.assertTrue(document.is_file(), document)
            text = document.read_text(encoding="utf-8")
            self.assertIn("English", text)
            self.assertIn("简体中文", text)
            self.assertIn("日本語", text)

    def test_all_languages_cover_the_public_cli(self):
        for document in DOCUMENTS:
            text = document.read_text(encoding="utf-8")
            missing = sorted(command for command in COMMANDS if f"`{command}`" not in text)
            self.assertEqual(missing, [], f"{document}: missing {missing}")

    def test_local_markdown_links_resolve(self):
        for document in DOCUMENTS:
            text = document.read_text(encoding="utf-8")
            for raw_target in LOCAL_LINK.findall(text):
                target = raw_target.split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                resolved = (document.parent / target).resolve()
                self.assertTrue(resolved.exists(), f"{document}: broken link {raw_target}")

    def test_public_docs_do_not_contain_private_machine_markers(self):
        for document in DOCUMENTS:
            text = document.read_text(encoding="utf-8")
            for marker in PRIVATE_MARKERS:
                self.assertIsNone(marker.search(text), f"{document}: matched {marker.pattern}")


if __name__ == "__main__":
    unittest.main()
