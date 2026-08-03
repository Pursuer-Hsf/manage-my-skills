import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [
    ROOT / "README.md",
    ROOT / "docs" / "README.zh-CN.md",
    ROOT / "docs" / "README.ja.md",
]
MANAGER_SKILL = ROOT / "skills" / "manage-my-skills" / "SKILL.md"
MANAGER_AGENT_CONFIG = ROOT / "skills" / "manage-my-skills" / "agents" / "openai.yaml"
LOGO = ROOT / "assets" / "manage-my-skills-logo.png"
SOCIAL_PREVIEW = ROOT / "assets" / "social-preview.png"
LIFECYCLE_DIAGRAM = ROOT / "assets" / "skill-lifecycle.png"
LIFECYCLE_SOURCE = ROOT / "assets" / "skill-lifecycle.svg"
HOW_TO_USE_MARKERS = {
    ROOT / "README.md": [
        "## How to Use",
        "### First-time setup",
        "### Routine maintenance",
        "### Agent suggests capture or update",
        "### Synchronize servers",
        "### Restore on a new machine",
    ],
    ROOT / "docs" / "README.zh-CN.md": [
        "## 如何使用",
        "### 第一次配置",
        "### 日常维护",
        "### Agent 主动提醒沉淀或更新",
        "### 同步多台服务器",
        "### 在新机器恢复",
    ],
    ROOT / "docs" / "README.ja.md": [
        "## 使い方",
        "### 初回設定",
        "### 日常メンテナンス",
        "### Agent から新規作成・更新を提案",
        "### 複数サーバーを同期",
        "### 新しいマシンで復元",
    ],
}
POSITIONING_MARKERS = {
    ROOT / "README.md": [
        "The lifecycle manager for personal Agent skills.",
        "Turn reusable work into private skills.",
        "Keep public skills tied to their sources.",
        "## Three Paths, One Inventory",
        "The Agent proposes a sanitized preview",
        "records the canonical source, path, and optional ref",
        "same desired skills state",
        "checks the manager repository for updates",
    ],
    ROOT / "docs" / "README.zh-CN.md": [
        "个人 Agent skills 的生命周期管理器。",
        "把可复用工作沉淀为私有 skills",
        "让公开 skills 保持原始来源",
        "## 三条路径，一份清单",
        "Agent 先给出脱敏预览",
        "只记录 canonical source、路径和可选版本",
        "同一份“想拥有的 skills”状态",
        "管理器仓库更新",
    ],
    ROOT / "docs" / "README.ja.md": [
        "個人 Agent skills のライフサイクル管理。",
        "再利用できる作業を非公開 skill にし",
        "公開 skill は元のソースに紐付けたまま",
        "## 3 つの経路、1 つのインベントリ",
        "サニタイズ済みのプレビューを提案",
        "canonical source、パス、任意の ref だけを記録",
        "同じ「必要な skills」の状態",
        "マネージャーの更新を確認",
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

    def test_logo_asset_is_used_in_all_readmes(self):
        self.assertTrue(LOGO.is_file())
        self.assertTrue(LOGO.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        for document in DOCUMENTS:
            text = document.read_text(encoding="utf-8")
            self.assertIn("manage-my-skills-logo.png", text)

    def test_community_files_and_social_preview_exist(self):
        self.assertTrue(SOCIAL_PREVIEW.is_file())
        self.assertTrue(SOCIAL_PREVIEW.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        for path in [
            ROOT / "CONTRIBUTING.md",
            ROOT / "CODE_OF_CONDUCT.md",
            ROOT / "CHANGELOG.md",
            ROOT / ".github" / "SECURITY.md",
            ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml",
            ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml",
            ROOT / ".github" / "pull_request_template.md",
        ]:
            self.assertTrue(path.is_file(), path)

    def test_lifecycle_diagram_exists_and_is_embedded_in_all_readmes(self):
        self.assertTrue(LIFECYCLE_SOURCE.is_file())
        self.assertTrue(LIFECYCLE_DIAGRAM.is_file())
        self.assertTrue(LIFECYCLE_DIAGRAM.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        for document in DOCUMENTS:
            self.assertIn("skill-lifecycle.png", document.read_text(encoding="utf-8"))

    def test_all_languages_include_how_to_use_examples(self):
        for document, markers in HOW_TO_USE_MARKERS.items():
            text = document.read_text(encoding="utf-8")
            missing = [marker for marker in markers if marker not in text]
            self.assertEqual(missing, [], f"{document}: missing {missing}")

    def test_all_languages_emphasize_the_full_management_scope(self):
        for document, markers in POSITIONING_MARKERS.items():
            text = document.read_text(encoding="utf-8")
            missing = [marker for marker in markers if marker not in text]
            self.assertEqual(missing, [], f"{document}: missing {missing}")

    def test_user_documentation_does_not_expose_internal_cli_usage(self):
        for document in DOCUMENTS:
            text = document.read_text(encoding="utf-8")
            found = [marker for marker in INTERNAL_CLI_MARKERS if marker in text]
            self.assertEqual(found, [], f"{document}: exposes internal CLI {found}")

    def test_manager_skill_defines_proactive_capture_safely(self):
        text = MANAGER_SKILL.read_text(encoding="utf-8")
        for marker in [
            "## Suggest Reusable Skill Capture Or Update",
            "offer once",
            "Do not create, update, import, or synchronize anything until the user agrees.",
            "Import and synchronize only after separate approval.",
            "track-source",
            "sources",
            "manager-status",
            "re-read this `SKILL.md`",
            "Update this public manager only by fast-forward.",
            "not as a background daemon",
        ]:
            self.assertIn(marker, text)

        agent_config = MANAGER_AGENT_CONFIG.read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: true", agent_config)

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
