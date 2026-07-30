import importlib.util
import io
import json
import os
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


def manager_repo_pair(base: Path) -> tuple[Path, Path]:
    source = base / "manager-source"
    checkout = base / "manager-checkout"
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "branch", "-M", "main"], check=True)
    (source / "version.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "version.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-m", "Initial"], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(source), str(checkout)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"], check=True)
    return source, checkout


def commit_version(repo: Path, version: str) -> None:
    (repo / "version.txt").write_text(f"{version}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "version.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", version], check=True, capture_output=True)


class ManagerTests(unittest.TestCase):
    def test_add_user_local_bin_to_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            user_bin = home / ".local" / "bin"
            user_bin.mkdir(parents=True)
            with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}):
                manager.add_user_local_bin_to_path(home)
                self.assertEqual(os.environ["PATH"], f"{user_bin}:/usr/bin")

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

    def test_connect_existing_library_only_writes_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            write_skill(library / "skills" / "alpha")
            subprocess.run(["git", "init", str(library)], check=True, capture_output=True)
            before = sorted(str(path.relative_to(library)) for path in library.rglob("*") if ".git" not in path.parts)
            with mock.patch.object(manager, "require_gh_auth"), mock.patch.object(
                manager, "verify_private_repo", return_value={"isPrivate": True}
            ) as verify:
                code = manager.main([
                    "connect", "--repo", "OWNER/my-skills",
                    "--library-dir", str(library), "--state-file", str(state), "--apply",
                ])
            after = sorted(str(path.relative_to(library)) for path in library.rglob("*") if ".git" not in path.parts)
            self.assertEqual(code, 0)
            self.assertEqual(before, after)
            self.assertEqual(json.loads(state.read_text())["private_repo"], "OWNER/my-skills")
            verify.assert_called_once_with("OWNER/my-skills")

    def test_repo_info_uses_fields_supported_by_older_gh(self):
        result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"nameWithOwner":"OWNER/my-skills","isPrivate":true}\n',
            stderr="",
        )
        with mock.patch.object(manager, "run", return_value=result) as execute:
            info = manager.repo_info("OWNER/my-skills")
        self.assertTrue(info["isPrivate"])
        command = execute.call_args.args[0]
        self.assertEqual(command[-1], "nameWithOwner,isPrivate")

    def test_manager_status_detects_and_applies_fast_forward_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, checkout = manager_repo_pair(Path(tmp))
            self.assertEqual(manager.manager_update_status(checkout)["state"], "current")
            commit_version(source, "v2")
            self.assertEqual(manager.manager_update_status(checkout)["state"], "update-available")
            code = manager.main(["update-manager", "--manager-dir", str(checkout)])
            self.assertEqual(code, 0)
            self.assertEqual((checkout / "version.txt").read_text(), "v1\n")
            code = manager.main(["update-manager", "--manager-dir", str(checkout), "--apply"])
            self.assertEqual(code, 0)
            self.assertEqual((checkout / "version.txt").read_text(), "v2\n")
            self.assertEqual(manager.manager_update_status(checkout)["state"], "current")

    def test_manager_update_stops_when_local_history_is_ahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, checkout = manager_repo_pair(Path(tmp))
            commit_version(checkout, "local")
            self.assertEqual(manager.manager_update_status(checkout)["state"], "local-ahead")
            code = manager.main(["update-manager", "--manager-dir", str(checkout), "--apply"])
            self.assertEqual(code, 2)
            self.assertEqual((checkout / "version.txt").read_text(), "local\n")

    def test_manager_update_stops_on_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, checkout = manager_repo_pair(Path(tmp))
            commit_version(source, "v2")
            (checkout / "local.txt").write_text("keep\n", encoding="utf-8")
            self.assertEqual(manager.manager_update_status(checkout)["state"], "update-available")
            code = manager.main(["update-manager", "--manager-dir", str(checkout), "--apply"])
            self.assertEqual(code, 2)
            self.assertEqual((checkout / "version.txt").read_text(), "v1\n")
            self.assertTrue((checkout / "local.txt").is_file())

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

    def test_track_source_preview_does_not_change_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            manager.ensure_library_files(library, "OWNER/my-skills")
            manager.write_json(state, {
                "schema_version": 1,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })
            code = manager.main([
                "track-source",
                "--name", "example-skill",
                "--kind", "github",
                "--source", "OWNER/public-skills",
                "--path", "skills/example-skill",
                "--ref", "main",
                "--state-file", str(state),
            ])
            self.assertEqual(code, 0)
            manifest = json.loads((library / "library.json").read_text())
            self.assertEqual(manifest["sources"], [])

    def test_track_source_apply_records_portable_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            manager.ensure_library_files(library, "OWNER/my-skills")
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
                    "track-source",
                    "--name", "example-skill",
                    "--kind", "github",
                    "--source", "OWNER/public-skills",
                    "--path", "skills/example-skill",
                    "--ref", "v1",
                    "--state-file", str(state),
                    "--apply",
                ])
            self.assertEqual(code, 0)
            sources = manager.load_library_manifest(library)["sources"]
            self.assertEqual(sources, [{
                "name": "example-skill",
                "kind": "github",
                "source": "OWNER/public-skills",
                "path": "skills/example-skill",
                "ref": "v1",
            }])
            verify.assert_called_once_with("OWNER/my-skills")

    def test_track_source_can_preview_and_apply_version_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            manager.ensure_library_files(library, "OWNER/my-skills")
            subprocess.run(["git", "init", str(library)], check=True, capture_output=True)
            manifest = manager.load_library_manifest(library)
            manifest["sources"] = [{
                "name": "example-skill",
                "kind": "github",
                "source": "OWNER/public-skills",
                "ref": "v1",
            }]
            manager.write_json(library / "library.json", manifest)
            manager.write_json(state, {
                "schema_version": 1,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })
            command = [
                "track-source",
                "--name", "example-skill",
                "--kind", "github",
                "--source", "OWNER/public-skills",
                "--ref", "v2",
                "--state-file", str(state),
            ]
            self.assertEqual(manager.main(command), 0)
            self.assertEqual(manager.load_library_manifest(library)["sources"][0]["ref"], "v1")
            with mock.patch.object(manager, "require_gh_auth"), mock.patch.object(
                manager, "verify_private_repo", return_value={"isPrivate": True}
            ):
                self.assertEqual(manager.main(command + ["--apply"]), 0)
            self.assertEqual(manager.load_library_manifest(library)["sources"][0]["ref"], "v2")

    def test_track_source_rejects_machine_local_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            manager.ensure_library_files(library, "OWNER/my-skills")
            manager.write_json(state, {
                "schema_version": 1,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })
            code = manager.main([
                "track-source",
                "--name", "example-skill",
                "--kind", "other",
                "--source", "/local/example-skill",
                "--state-file", str(state),
            ])
            self.assertEqual(code, 2)
            self.assertEqual(manager.load_library_manifest(library)["sources"], [])

    def test_track_source_rejects_embedded_credentials(self):
        with self.assertRaises(manager.ManagerError):
            manager.validate_source_entry({
                "name": "example-skill",
                "kind": "github",
                "source": "https://user:secret@example.com/skills.git",
            })

    def test_sources_json_returns_portable_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            manager.ensure_library_files(library, "OWNER/my-skills")
            manifest = manager.load_library_manifest(library)
            manifest["sources"] = [{
                "name": "example-skill",
                "kind": "github",
                "source": "OWNER/public-skills",
            }]
            manager.write_json(library / "library.json", manifest)
            manager.write_json(state, {
                "schema_version": 1,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })
            output = io.StringIO()
            with redirect_stdout(output):
                code = manager.main(["sources", "--state-file", str(state), "--json"])
            self.assertEqual(code, 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["sources"][0]["source"], "OWNER/public-skills")

    def test_restore_preview_reports_source_managed_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            target = base / "target"
            manager.ensure_library_files(library, "OWNER/my-skills")
            write_skill(library / "skills" / "alpha")
            manifest = manager.load_library_manifest(library)
            manifest["sources"] = [{
                "name": "example-skill",
                "kind": "github",
                "source": "OWNER/public-skills",
            }]
            manager.write_json(library / "library.json", manifest)
            subprocess.run(["git", "init", str(library)], check=True, capture_output=True)
            output = io.StringIO()
            with redirect_stdout(output):
                code = manager.main([
                    "restore",
                    "--repo", "OWNER/my-skills",
                    "--library-dir", str(library),
                    "--target", str(target),
                    "--state-file", str(base / "state.json"),
                ])
            self.assertEqual(code, 0)
            self.assertIn("reconcile 1 source-managed skills", output.getvalue())
            self.assertFalse(target.exists())

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

    def test_restore_preview_detects_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            target = base / "target"
            write_skill(library / "skills" / "alpha")
            subprocess.run(["git", "init", str(library)], check=True, capture_output=True)
            write_skill(target / "alpha")
            code = manager.main([
                "restore", "--repo", "OWNER/my-skills",
                "--library-dir", str(library), "--target", str(target),
                "--state-file", str(base / "state.json"),
            ])
            self.assertEqual(code, 2)

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
            manager.write_json(library / "library.json", {
                "schema_version": 1,
                "repository": "OWNER/my-skills",
                "sources": [{
                    "name": "example-skill",
                    "kind": "github",
                    "source": "OWNER/public-skills",
                }],
            })
            output = io.StringIO()
            with redirect_stdout(output):
                code = manager.main(["status", "--state-file", str(state), "--json"])
            self.assertEqual(code, 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["skills"], ["alpha"])
            self.assertEqual(data["sources"][0]["name"], "example-skill")


if __name__ == "__main__":
    unittest.main()
