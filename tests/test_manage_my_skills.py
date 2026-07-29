import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "manage-my-skills" / "scripts" / "manage_my_skills.py"
SPEC = importlib.util.spec_from_file_location("manage_my_skills", SCRIPT)
manager = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = manager
SPEC.loader.exec_module(manager)


def write_skill(path: Path, body: str = "# Example\n") -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example user-owned skill.\n---\n\n" + body,
        encoding="utf-8",
    )


class ManagerTests(unittest.TestCase):
    def test_scan_discovers_and_classifies_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".agents" / "skills"
            write_skill(root / "alpha")
            records = manager.discover([root])
            self.assertEqual([item.name for item in records], ["alpha"])
            self.assertEqual(records[0].source, "shared-local")

    def test_setup_preview_does_not_create_library_or_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            code = manager.main([
                "setup", "--repo", "OWNER/my-skills",
                "--library-dir", str(library), "--state-file", str(state),
            ])
            self.assertEqual(code, 0)
            self.assertFalse(library.exists())
            self.assertFalse(state.exists())

    def test_import_preview_does_not_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            library = base / "library"
            state = base / "state.json"
            write_skill(source)
            manager.write_json(state, {
                "schema_version": 1,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })
            code = manager.main(["import", str(source), "--state-file", str(state)])
            self.assertEqual(code, 0)
            self.assertFalse((library / "skills" / "source").exists())

    def test_import_blocks_secret_like_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            library = base / "library"
            state = base / "state.json"
            write_skill(source, "password = 'this-is-not-safe'\n")
            manager.write_json(state, {
                "schema_version": 1,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })
            code = manager.main([
                "import", str(source), "--state-file", str(state), "--apply",
            ])
            self.assertEqual(code, 2)
            self.assertFalse((library / "skills" / "source").exists())

    def test_import_rejects_link_outside_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            library = base / "library"
            state = base / "state.json"
            write_skill(source)
            outside = base / "outside.txt"
            outside.write_text("private\n", encoding="utf-8")
            (source / "outside.txt").symlink_to(outside)
            manager.write_json(state, {
                "schema_version": 1,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })
            code = manager.main(["import", str(source), "--state-file", str(state)])
            self.assertEqual(code, 2)
            self.assertFalse((library / "skills" / "source").exists())

    def test_import_apply_requires_verified_private_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            library = base / "library"
            state = base / "state.json"
            write_skill(source)
            subprocess.run(["git", "init", str(library)], check=True, capture_output=True)
            manager.write_json(state, {
                "schema_version": 1,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })
            with mock.patch.object(manager, "require_gh_auth"), mock.patch.object(
                manager, "verify_private_repo", return_value={"isPrivate": True}
            ) as verify:
                code = manager.main([
                    "import", str(source), "--state-file", str(state), "--apply",
                ])
            self.assertEqual(code, 0)
            verify.assert_called_once_with("OWNER/my-skills")
            self.assertTrue((library / "skills" / "source" / "SKILL.md").is_file())

    def test_restore_refuses_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            target = base / "target"
            write_skill(library / "skills" / "alpha")
            subprocess.run(["git", "init", str(library)], check=True, capture_output=True)
            write_skill(target / "alpha")
            with mock.patch.object(manager, "require_gh_auth"), mock.patch.object(
                manager, "verify_private_repo", return_value={"isPrivate": True}
            ):
                code = manager.main([
                    "restore", "--repo", "OWNER/my-skills",
                    "--library-dir", str(library), "--target", str(target),
                    "--state-file", str(base / "state.json"), "--apply",
                ])
            self.assertEqual(code, 2)
            self.assertFalse((target / "alpha").is_symlink())

    def test_restore_preflight_avoids_partial_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            target = base / "target"
            write_skill(library / "skills" / "alpha")
            write_skill(library / "skills" / "zeta")
            subprocess.run(["git", "init", str(library)], check=True, capture_output=True)
            write_skill(target / "zeta")
            with mock.patch.object(manager, "require_gh_auth"), mock.patch.object(
                manager, "verify_private_repo", return_value={"isPrivate": True}
            ):
                code = manager.main([
                    "restore", "--repo", "OWNER/my-skills",
                    "--library-dir", str(library), "--target", str(target),
                    "--state-file", str(base / "state.json"), "--apply",
                ])
            self.assertEqual(code, 2)
            self.assertFalse((target / "alpha").exists())

    def test_staged_allowlist_rejects_other_top_level_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
            (repo / "unexpected.txt").write_text("no\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "unexpected.txt"], check=True)
            with self.assertRaises(manager.ManagerError):
                manager.assert_allowed_staged_paths(repo)

    def test_status_json_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            write_skill(library / "skills" / "alpha")
            manager.write_json(state, {
                "schema_version": 1,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })
            output = io.StringIO()
            with redirect_stdout(output):
                code = manager.main(["status", "--state-file", str(state), "--json"])
            self.assertEqual(code, 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["skills"], ["alpha"])


if __name__ == "__main__":
    unittest.main()
