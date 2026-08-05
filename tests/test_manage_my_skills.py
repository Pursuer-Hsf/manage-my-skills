import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
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


def initialize_library_checkout(path: Path, repo: str = "OWNER/my-skills") -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", f"https://github.com/{repo}.git"],
        check=True,
        capture_output=True,
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


def private_library_pair(base: Path) -> tuple[Path, Path]:
    remote = base / "private-remote.git"
    seed = base / "private-seed"
    library = base / "private-library"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(seed), "branch", "-M", "main"], check=True)
    write_skill(seed / "skills" / "alpha", "original\n")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-m", "Initial"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(remote)], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "-u", "origin", "main"], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(library)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(library), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(library), "config", "user.email", "test@example.invalid"], check=True)
    return seed, library


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

    def test_human_reports_keep_private_and_full_scan_counts_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            manager.ensure_library_files(library, "OWNER/my-skills")
            write_skill(library / "skills" / "alpha")
            initialize_library_checkout(library)
            manager.write_json(state, {
                "schema_version": manager.STATE_SCHEMA_VERSION,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })

            status_output = io.StringIO()
            with redirect_stdout(status_output):
                self.assertEqual(manager.main(["status", "--state-file", str(state)]), 0)
            self.assertIn("Private-library managed skills: 1", status_output.getvalue())
            self.assertIn("Source inventory records: 0", status_output.getvalue())

            scan_root = base / "scan-root"
            write_skill(scan_root / "beta")
            scan_output = io.StringIO()
            with redirect_stdout(scan_output):
                self.assertEqual(
                    manager.main(["scan", "--root", str(scan_root)]),
                    0,
                )
            self.assertIn("Full local scan: found 1 skills in 1 roots", scan_output.getvalue())

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
            initialize_library_checkout(library)
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

    def test_connect_rejects_mismatched_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            write_skill(library / "skills" / "alpha")
            initialize_library_checkout(library, "OWNER/other-skills")
            with mock.patch.object(manager, "require_gh_auth"), mock.patch.object(
                manager, "verify_private_repo", return_value={"isPrivate": True}
            ):
                code = manager.main([
                    "connect", "--repo", "OWNER/my-skills",
                    "--library-dir", str(library), "--state-file", str(state), "--apply",
                ])
            self.assertEqual(code, 2)
            self.assertFalse(state.exists())

    def test_github_repository_from_remote_accepts_https_and_ssh(self):
        self.assertEqual(
            manager.github_repository_from_remote("https://github.com/OWNER/my-skills.git"),
            "owner/my-skills",
        )
        self.assertEqual(
            manager.github_repository_from_remote("git@github.com:OWNER/my-skills.git"),
            "owner/my-skills",
        )

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

    def test_run_reports_timeout_without_hanging(self):
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(["git", "fetch"], 10)):
            result = manager.run(["git", "fetch"], check=False, timeout=10)
        self.assertEqual(result.returncode, 124)
        self.assertIn("timed out after 10 seconds", result.stderr)

    def test_github_network_check_reports_proxy_guidance(self):
        with mock.patch.object(manager.urllib.request, "getproxies", return_value={}), mock.patch.object(
            manager.urllib.request, "urlopen", side_effect=urllib.error.URLError("network timeout")
        ):
            ok, detail = manager.github_network_check()
        self.assertFalse(ok)
        self.assertIn("No proxy was detected", detail)
        self.assertIn("request network access", detail)

    def test_github_network_check_does_not_expose_proxy_value(self):
        response = mock.MagicMock()
        response.status = 200
        with mock.patch.object(
            manager.urllib.request, "getproxies", return_value={"https": "http://proxy.example.invalid:8080"}
        ), mock.patch.object(manager.urllib.request, "urlopen", return_value=response):
            ok, detail = manager.github_network_check()
        self.assertTrue(ok)
        self.assertIn("configured proxy", detail)
        self.assertNotIn("proxy.example.invalid", detail)

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

    def test_manager_status_does_not_require_fetch_head_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, checkout = manager_repo_pair(Path(tmp))
            commit_version(source, "v2")
            real_git = manager.git

            def fail_fetch(cwd, *args, **kwargs):
                if args and args[0] == "fetch":
                    return subprocess.CompletedProcess(
                        ["git", *args], 1, "", "FETCH_HEAD is read-only\n"
                    )
                return real_git(cwd, *args, **kwargs)

            with mock.patch.object(manager, "git", side_effect=fail_fetch):
                status = manager.manager_update_status(checkout)
            self.assertEqual(status["state"], "remote-unverified")
            self.assertIn("version is unverified", status["error"])

    def test_manager_status_distinguishes_unreachable_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, checkout = manager_repo_pair(Path(tmp))
            real_git = manager.git

            def fail_remote_checks(cwd, *args, **kwargs):
                if args and args[0] in {"fetch", "ls-remote"}:
                    return subprocess.CompletedProcess(
                        ["git", *args], 1, "", "network timeout\n"
                    )
                return real_git(cwd, *args, **kwargs)

            with mock.patch.object(manager, "git", side_effect=fail_remote_checks):
                status = manager.manager_update_status(checkout)
            self.assertEqual(status["state"], "remote-unreachable")
            self.assertIn("Cannot reach", status["error"])

    def test_github_auth_check_distinguishes_missing_login_from_unavailable_check(self):
        missing = subprocess.CompletedProcess([], 1, "", "not logged in to github.com\n")
        with mock.patch.object(manager, "run", return_value=missing):
            self.assertEqual(manager.github_auth_check(), (False, "not authenticated"))

        unavailable = subprocess.CompletedProcess([], 1, "", "network timeout\n")
        with mock.patch.object(manager, "run", return_value=unavailable):
            ok, detail = manager.github_auth_check()
            self.assertFalse(ok)
            self.assertIn("unavailable", detail)

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

    def test_refresh_library_stops_when_local_history_is_ahead(self):
        library = Path("/tmp/library")
        with mock.patch.object(manager, "worktree_dirty", return_value=False), mock.patch.object(
            manager, "ahead_behind", return_value=(1, 0)
        ), mock.patch.object(manager, "git") as git:
            with self.assertRaises(manager.ManagerError):
                manager.refresh_library_for_mutation(library)
        git.assert_called_once_with(library, "fetch", "origin")

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
            initialize_library_checkout(library)
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
            initialize_library_checkout(library)
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
            initialize_library_checkout(library)
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
            initialize_library_checkout(library)
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
            initialize_library_checkout(library)
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

    def test_restore_requires_registered_machine_when_fleet_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            target = base / "target"
            state = base / "state.json"
            write_skill(library / "skills" / "alpha")
            subprocess.run(["git", "init", str(library)], check=True, capture_output=True)
            manager.write_json(library / "fleet.json", {
                "schema_version": manager.FLEET_SCHEMA_VERSION,
                "machines": [{
                    "id": "00000000-0000-4000-8000-000000000001",
                    "label": "first-machine",
                    "roles": [],
                    "enabled": True,
                }],
            })
            manager.write_json(state, {
                "schema_version": manager.STATE_SCHEMA_VERSION,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
                "machine_id": "00000000-0000-4000-8000-000000000002",
                "machine_label": "second-machine",
                "target_root": str(target),
            })
            code = manager.main([
                "restore", "--repo", "OWNER/my-skills", "--library-dir", str(library),
                "--target", str(target), "--state-file", str(state),
            ])
            self.assertEqual(code, 2)
            self.assertFalse(target.exists())

    def test_restore_rejects_skill_link_outside_its_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            target = base / "target"
            source = library / "skills" / "alpha"
            outside = base / "outside.txt"
            write_skill(source)
            outside.write_text("private\\n", encoding="utf-8")
            (source / "outside.txt").symlink_to(outside)
            subprocess.run(["git", "init", str(library)], check=True, capture_output=True)
            code = manager.main([
                "restore", "--repo", "OWNER/my-skills", "--library-dir", str(library),
                "--target", str(target), "--state-file", str(base / "state.json"),
            ])
            self.assertEqual(code, 2)
            self.assertFalse(target.exists())

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

    def test_status_reports_pending_private_skill_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            write_skill(library / "skills" / "alpha", "original\n")
            initialize_library_checkout(library)
            subprocess.run(["git", "-C", str(library), "config", "user.name", "Test User"], check=True)
            subprocess.run(["git", "-C", str(library), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(library), "add", "."], check=True)
            subprocess.run(["git", "-C", str(library), "commit", "-m", "Initial"], check=True, capture_output=True)
            (library / "skills" / "alpha" / "SKILL.md").write_text(
                "---\nname: example\ndescription: Example user-owned skill.\n---\n\nupdated\n",
                encoding="utf-8",
            )
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
            self.assertEqual(data["pending_skill_changes"], ["alpha"])
            self.assertTrue(data["pending_sync"])

    def test_status_reports_remote_private_skill_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            seed, library = private_library_pair(base)
            state = base / "state.json"
            skill = seed / "skills" / "alpha" / "SKILL.md"
            skill.write_text(
                "---\nname: example\ndescription: Example user-owned skill.\n---\n\nupdated\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-m", "Update alpha"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(seed), "push"], check=True, capture_output=True)
            manager.write_json(state, {
                "schema_version": manager.STATE_SCHEMA_VERSION,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })
            output = io.StringIO()
            with redirect_stdout(output):
                code = manager.main(["status", "--state-file", str(state), "--json"])
            self.assertEqual(code, 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["remote"]["state"], "update-available")
            self.assertEqual(data["remote"]["behind"], 1)
            self.assertEqual(data["remote"]["skill_changes"], ["alpha"])
            self.assertTrue(data["pending_sync"])

    def test_bootstrap_preview_does_not_create_library_or_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            code = manager.main([
                "bootstrap", "--repo", "OWNER/my-skills", "--label", "test-machine",
                "--library-dir", str(library), "--state-file", str(state),
            ])
            self.assertEqual(code, 0)
            self.assertFalse(library.exists())
            self.assertFalse(state.exists())

    def test_bootstrap_apply_creates_registered_machine_and_fleet(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            target = base / "target"
            with mock.patch.object(manager, "ensure_private_checkout"), mock.patch.object(
                manager, "refresh_library_for_mutation"
            ), mock.patch.object(manager, "commit_and_push_library") as commit:
                code = manager.main([
                    "bootstrap", "--repo", "OWNER/my-skills", "--label", "test-machine",
                    "--role", "gpu", "--library-dir", str(library), "--target", str(target),
                    "--state-file", str(state), "--apply",
                ])
            self.assertEqual(code, 0)
            saved = manager.load_state(state)
            self.assertEqual(saved["schema_version"], manager.STATE_SCHEMA_VERSION)
            self.assertEqual(saved["machine_label"], "test-machine")
            self.assertEqual(saved["target_root"], str(target.resolve()))
            fleet = manager.load_fleet(library, required=True)
            self.assertEqual(fleet["machines"][0]["label"], "test-machine")
            self.assertEqual(fleet["machines"][0]["roles"], ["gpu"])
            commit.assert_called_once_with(library.resolve(), "Bootstrap personal skill library")

    def test_join_registers_second_machine_without_restoring_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            target = base / "target"
            manager.ensure_library_files(library, "OWNER/my-skills")
            manager.write_json(library / "fleet.json", manager.fleet_manifest())
            with mock.patch.object(manager, "ensure_private_checkout"), mock.patch.object(
                manager, "refresh_library_for_mutation"
            ), mock.patch.object(manager, "commit_and_push_library") as commit:
                code = manager.main([
                    "join", "--repo", "OWNER/my-skills", "--label", "server-gpu",
                    "--role", "gpu", "--library-dir", str(library), "--target", str(target),
                    "--state-file", str(state), "--apply",
                ])
            self.assertEqual(code, 0)
            self.assertFalse(target.exists())
            self.assertEqual(manager.load_state(state)["machine_label"], "server-gpu")
            self.assertEqual(manager.load_fleet(library)["machines"][0]["label"], "server-gpu")
            commit.assert_called_once_with(library.resolve(), "Register machine server-gpu")

    def test_fleet_rejects_connection_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            library.mkdir()
            manager.write_json(library / "fleet.json", {
                "schema_version": 1,
                "machines": [{
                    "id": "00000000-0000-4000-8000-000000000001",
                    "label": "server-gpu",
                    "ssh_alias": "server-gpu",
                }],
            })
            with self.assertRaises(manager.ManagerError):
                manager.load_fleet(library, required=True)

    def test_staged_allowlist_permits_fleet_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
            manager.write_json(repo / "fleet.json", manager.fleet_manifest())
            subprocess.run(["git", "-C", str(repo), "add", "fleet.json"], check=True)
            manager.assert_allowed_staged_paths(repo)

    def test_library_commit_includes_fleet_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            subprocess.run(["git", "init", str(library)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(library), "config", "user.name", "Test User"], check=True)
            subprocess.run(["git", "-C", str(library), "config", "user.email", "test@example.invalid"], check=True)
            manager.ensure_library_files(library, "OWNER/my-skills")
            manager.ensure_fleet(library)
            self.assertTrue(manager.git_commit_if_needed(library, "Initialize fleet"))
            files = subprocess.run(
                ["git", "-C", str(library), "show", "--format=", "--name-only", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertIn("fleet.json", files)
            self.assertIn("library.json", files)

    def test_machine_status_reports_registered_local_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            state = base / "state.json"
            machine_id = "00000000-0000-4000-8000-000000000001"
            manager.ensure_library_files(library, "OWNER/my-skills")
            manager.write_json(library / "fleet.json", {
                "schema_version": 1,
                "machines": [{
                    "id": machine_id,
                    "label": "test-machine",
                    "roles": [],
                    "enabled": True,
                }],
            })
            manager.write_json(state, {
                "schema_version": manager.STATE_SCHEMA_VERSION,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
                "machine_id": machine_id,
                "machine_label": "test-machine",
                "target_root": str(base / "target"),
            })
            output = io.StringIO()
            with redirect_stdout(output):
                code = manager.main(["machine-status", "--state-file", str(state), "--json"])
            self.assertEqual(code, 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["machine"]["label"], "test-machine")
            self.assertTrue(data["fleet"]["registered"])

    def test_legacy_state_upgrades_without_machine_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            state = base / "state.json"
            library = base / "library"
            manager.write_json(state, {
                "schema_version": 1,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
            })
            manager.save_state("OWNER/my-skills", library, state)
            saved = manager.load_state(state)
            self.assertEqual(saved["schema_version"], manager.STATE_SCHEMA_VERSION)
            self.assertNotIn("machine_id", saved)
            self.assertIsNone(manager.local_machine(saved))

    def test_existing_machine_identity_cannot_be_replaced(self):
        state = {
            "schema_version": manager.STATE_SCHEMA_VERSION,
            "machine_id": "00000000-0000-4000-8000-000000000001",
            "machine_label": "test-machine",
            "target_root": "/tmp/target",
        }
        args = manager.argparse.Namespace(
            machine_id="00000000-0000-4000-8000-000000000002",
            label="test-machine",
            target=None,
        )
        with self.assertRaises(manager.ManagerError):
            manager.requested_machine(args, state)

    def test_adopt_preview_does_not_move_or_link_existing_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "target"
            source = target / "alpha"
            library = base / "library"
            state = base / "state.json"
            write_skill(source)
            initialize_library_checkout(library)
            manager.write_json(library / "fleet.json", {
                "schema_version": manager.FLEET_SCHEMA_VERSION,
                "machines": [{
                    "id": "00000000-0000-4000-8000-000000000001",
                    "label": "test-machine",
                    "roles": [],
                    "enabled": True,
                }],
            })
            manager.write_json(state, {
                "schema_version": manager.STATE_SCHEMA_VERSION,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
                "machine_id": "00000000-0000-4000-8000-000000000001",
                "machine_label": "test-machine",
                "target_root": str(target),
            })
            code = manager.main(["adopt", str(source), "--state-file", str(state)])
            self.assertEqual(code, 0)
            self.assertTrue(source.is_dir())
            self.assertFalse(source.is_symlink())
            self.assertFalse((library / "skills" / "alpha").exists())

    def test_adopt_rejects_unregistered_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "target"
            source = target / "alpha"
            library = base / "library"
            state = base / "state.json"
            write_skill(source)
            subprocess.run(["git", "init", str(library)], check=True, capture_output=True)
            manager.write_json(state, {
                "schema_version": manager.STATE_SCHEMA_VERSION,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
                "machine_id": "00000000-0000-4000-8000-000000000001",
                "machine_label": "test-machine",
                "target_root": str(target),
            })
            code = manager.main(["adopt", str(source), "--state-file", str(state)])
            self.assertEqual(code, 2)
            self.assertTrue(source.is_dir())
            self.assertFalse(source.is_symlink())
            self.assertFalse((library / "skills" / "alpha").exists())

    def test_adopt_apply_preserves_backup_and_creates_managed_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "target"
            source = target / "alpha"
            library = base / "library"
            state = base / "state.json"
            write_skill(source, "# Managed example\n")
            initialize_library_checkout(library)
            manager.write_json(library / "fleet.json", {
                "schema_version": manager.FLEET_SCHEMA_VERSION,
                "machines": [{
                    "id": "00000000-0000-4000-8000-000000000001",
                    "label": "test-machine",
                    "roles": [],
                    "enabled": True,
                }],
            })
            manager.write_json(state, {
                "schema_version": manager.STATE_SCHEMA_VERSION,
                "private_repo": "OWNER/my-skills",
                "library_dir": str(library),
                "machine_id": "00000000-0000-4000-8000-000000000001",
                "machine_label": "test-machine",
                "target_root": str(target),
            })
            with mock.patch.object(manager, "require_gh_auth"), mock.patch.object(
                manager, "verify_private_repo", return_value={"isPrivate": True}
            ):
                code = manager.main(["adopt", str(source), "--state-file", str(state), "--apply"])
            self.assertEqual(code, 0)
            library_skill = library / "skills" / "alpha"
            backup = base / ".manage-my-skills-backups" / "alpha"
            self.assertTrue(library_skill.is_dir())
            self.assertTrue(backup.is_dir())
            self.assertTrue(source.is_symlink())
            self.assertEqual(source.resolve(), library_skill.resolve())
            self.assertEqual(manager.skill_fingerprint(backup), manager.skill_fingerprint(library_skill))


if __name__ == "__main__":
    unittest.main()
