import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [
    ROOT / "README.md",
    ROOT / "docs" / "README.zh-CN.md",
    ROOT / "docs" / "README.ja.md",
]
CONVERSATION_MARKERS = {
    ROOT / "README.md": [
        "## Conversation Guides",
        "### Start from zero",
        "### Maintain the library",
        "### Synchronize several servers",
        "### Restore on a new machine",
    ],
    ROOT / "docs" / "README.zh-CN.md": [
        "## 对话式使用教程",
        "### 从零配置",
        "### 日常检查与维护",
        "### 同步多台服务器",
        "### 在新机器恢复",
    ],
    ROOT / "docs" / "README.ja.md": [
        "## 会話形式の使い方",
        "### ゼロから設定",
        "### 日常の確認とメンテナンス",
        "### 複数サーバーを同期",
        "### 新しいマシンで復元",
    ],
}
INTERNAL_CLI_MARKERS = [
    'MANAGER="$HOME',
    'python3 "$MANAGER"',
    "--apply",
]
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

    def test_all_languages_include_conversation_guides(self):
        for document, markers in CONVERSATION_MARKERS.items():
            text = document.read_text(encoding="utf-8")
            missing = [marker for marker in markers if marker not in text]
            self.assertEqual(missing, [], f"{document}: missing {missing}")

    def test_user_documentation_does_not_expose_internal_cli_usage(self):
        for document in DOCUMENTS:
            text = document.read_text(encoding="utf-8")
            found = [marker for marker in INTERNAL_CLI_MARKERS if marker in text]
            self.assertEqual(found, [], f"{document}: exposes internal CLI {found}")

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
