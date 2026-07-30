#!/usr/bin/env python3
"""Safely inventory, preserve, synchronize, and restore agent skills."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


SCHEMA_VERSION = 1
STATE_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
STATE_PATH = STATE_HOME / "manage-my-skills" / "state.json"
DEFAULT_MANAGER_DIR = Path(__file__).resolve().parents[3]
MANIFEST_NAME = "library.json"
ALLOWED_TOP_LEVEL = {MANIFEST_NAME, "skills"}
SOURCE_KINDS = {"github", "marketplace", "plugin", "other"}
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TEXT_SUFFIXES = {
    "", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".conf", ".py", ".sh", ".zsh", ".bash", ".js", ".ts",
}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "assigned secret": re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*['\"]?[^\s'\"${}<]{8,}"),
}


class ManagerError(RuntimeError):
    pass


@dataclass
class SkillRecord:
    name: str
    path: str
    source: str
    linked: bool


def say(message: str) -> None:
    print(message)


def fail(message: str) -> None:
    raise ManagerError(message)


def run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()
        fail(f"Command failed: {' '.join(args)}\n{detail}")
    return result


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Cannot read state file {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"State file must contain a JSON object: {path}")
    return value


def save_state(repo: str, library_dir: Path, path: Path = STATE_PATH) -> None:
    write_json(path, {
        "schema_version": SCHEMA_VERSION,
        "private_repo": repo,
        "library_dir": str(library_dir.expanduser().resolve()),
        "updated_at": utc_now(),
    })


def default_roots() -> list[Path]:
    roots = [
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills",
        Path.home() / ".agents" / "skills",
        Path.home() / ".claude" / "skills",
    ]
    return [path for path in roots if path.exists()]


def classify(path: Path) -> str:
    text = str(path).lower()
    if "/plugins/" in text or "/.system/" in text:
        return "managed"
    if "/my-skills/" in text or "/private-skills/" in text:
        return "personal"
    if "/.agents/skills/" in text:
        return "shared-local"
    return "local"


def discover(roots: Iterable[Path]) -> list[SkillRecord]:
    found: dict[str, SkillRecord] = {}
    for root in roots:
        expanded = root.expanduser()
        if not expanded.exists():
            continue
        for marker in expanded.rglob("SKILL.md"):
            skill_dir = marker.parent
            key = str(skill_dir.resolve())
            found[key] = SkillRecord(
                name=skill_dir.name,
                path=str(skill_dir),
                source=classify(skill_dir),
                linked=skill_dir.is_symlink(),
            )
    return sorted(found.values(), key=lambda item: (item.source, item.name, item.path))


def command_scan(args: argparse.Namespace) -> None:
    roots = [Path(value) for value in args.root] if args.root else default_roots()
    records = discover(roots)
    if args.json:
        print(json.dumps({"roots": [str(p) for p in roots], "skills": [asdict(x) for x in records]}, indent=2))
        return
    say(f"Found {len(records)} skills in {len(roots)} roots:")
    for item in records:
        link = " -> link" if item.linked else ""
        say(f"- {item.name} [{item.source}]{link}: {item.path}")


def require_tool(name: str) -> None:
    if not shutil.which(name):
        fail(f"Required command is not installed or not on PATH: {name}")


def add_user_local_bin_to_path(home: Path | None = None) -> None:
    user_bin = (home or Path.home()) / ".local" / "bin"
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if user_bin.is_dir() and str(user_bin) not in path_parts:
        os.environ["PATH"] = str(user_bin) + os.pathsep + os.environ.get("PATH", "")


def require_gh_auth() -> None:
    require_tool("gh")
    result = run(["gh", "auth", "status", "--hostname", "github.com"], check=False)
    if result.returncode:
        fail("GitHub CLI is not logged in. Run: gh auth login -h github.com")


def repo_info(repo: str) -> dict | None:
    result = run(
        ["gh", "repo", "view", repo, "--json", "nameWithOwner,isPrivate"],
        check=False,
    )
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("GitHub returned an unreadable repository response")


def verify_private_repo(repo: str) -> dict:
    info = repo_info(repo)
    if not info:
        fail(f"GitHub repository does not exist or is not accessible: {repo}")
    if info.get("isPrivate") is not True:
        fail(f"Refusing to use non-private repository: {repo}")
    return info


def library_manifest(repo: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": repo,
        "purpose": "Personal skill backup and portable source inventory",
        "sources": [],
        "updated_at": utc_now(),
    }


def validate_source_entry(entry: dict) -> dict:
    name = str(entry.get("name", ""))
    kind = str(entry.get("kind", ""))
    source = str(entry.get("source", ""))
    path = str(entry.get("path", ""))
    ref = str(entry.get("ref", ""))
    if not SKILL_NAME_PATTERN.fullmatch(name):
        fail(f"Source-managed skill name must use lowercase letters, digits, and hyphens: {name}")
    if kind not in SOURCE_KINDS:
        fail(f"Unsupported source kind: {kind}")
    if not source or len(source) > 500 or any(char.isspace() for char in source):
        fail("Source must be a portable identifier or URL without whitespace")
    if source.startswith(("/", "~", ".")):
        fail("Source must not be a machine-local path")
    if re.search(r"://[^/@\s]+:[^/@\s]+@", source):
        fail("Source URLs must not contain embedded credentials")
    if path:
        source_path = PurePosixPath(path)
        if source_path.is_absolute() or ".." in source_path.parts:
            fail("Source path must stay within the referenced source")
    if ref and (len(ref) > 200 or any(char.isspace() for char in ref)):
        fail("Source ref must not contain whitespace")
    serialized = json.dumps(entry, ensure_ascii=False)
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(serialized):
            fail(f"Source entry contains a possible {label}")
    normalized = {"name": name, "kind": kind, "source": source}
    if path:
        normalized["path"] = path
    if ref:
        normalized["ref"] = ref
    return normalized


def load_library_manifest(library_dir: Path) -> dict:
    manifest_path = library_dir / MANIFEST_NAME
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"Private library has no {MANIFEST_NAME}: {manifest_path}")
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Cannot read private library manifest {manifest_path}: {exc}")
    if not isinstance(value, dict):
        fail(f"Private library manifest must contain a JSON object: {manifest_path}")
    sources = value.get("sources", [])
    if not isinstance(sources, list):
        fail(f"Private library sources must be a JSON list: {manifest_path}")
    value["sources"] = [validate_source_entry(item) for item in sources if isinstance(item, dict)]
    if len(value["sources"]) != len(sources):
        fail(f"Every private library source must be a JSON object: {manifest_path}")
    return value


def source_inventory(library_dir: Path) -> list[dict]:
    if not (library_dir / MANIFEST_NAME).exists():
        return []
    return load_library_manifest(library_dir)["sources"]


def ensure_library_files(library_dir: Path, repo: str) -> None:
    library_dir.mkdir(parents=True, exist_ok=True)
    skills_dir = library_dir / "skills"
    skills_dir.mkdir(exist_ok=True)
    manifest = library_dir / MANIFEST_NAME
    if not manifest.exists():
        write_json(manifest, library_manifest(repo))
    keep = skills_dir / ".gitkeep"
    if not keep.exists():
        keep.touch()


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd, check=check)


def git_commit_if_needed(library_dir: Path, message: str) -> bool:
    git(library_dir, "add", "--", MANIFEST_NAME, "skills")
    staged = git(library_dir, "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        return False
    if staged.returncode != 1:
        fail("Cannot inspect staged changes")
    assert_allowed_staged_paths(library_dir)
    git(library_dir, "commit", "-m", message)
    return True


def assert_allowed_staged_paths(library_dir: Path) -> None:
    output = git(library_dir, "diff", "--cached", "--name-only").stdout
    bad = [line for line in output.splitlines() if line and Path(line).parts[0] not in ALLOWED_TOP_LEVEL]
    if bad:
        fail("Refusing to commit paths outside library allowlist:\n- " + "\n- ".join(bad))


def command_setup(args: argparse.Namespace) -> None:
    library_dir = Path(args.library_dir).expanduser()
    say(f"Plan: create or verify private repository {args.repo}")
    say(f"Plan: initialize private skill library at {library_dir}")
    if not args.apply:
        say("Preview only. Re-run with --apply to make these changes.")
        return
    require_gh_auth()
    info = repo_info(args.repo)
    if info is None:
        run(["gh", "repo", "create", args.repo, "--private", "--description", "Private backup of personal agent skills"])
    verify_private_repo(args.repo)
    if library_dir.exists() and any(library_dir.iterdir()):
        if not (library_dir / ".git").exists():
            fail(f"Library directory is not empty and is not a Git repository: {library_dir}")
    else:
        library_dir.parent.mkdir(parents=True, exist_ok=True)
        run(["gh", "repo", "clone", args.repo, str(library_dir)])
    ensure_library_files(library_dir, args.repo)
    if not (library_dir / ".git").exists():
        fail(f"Expected a cloned Git repository at {library_dir}")
    committed = git_commit_if_needed(library_dir, "Initialize private skill library")
    if committed:
        git(library_dir, "push", "-u", "origin", "HEAD")
    save_state(args.repo, library_dir, Path(args.state_file).expanduser())
    say("Private library is ready and its visibility was verified as Private.")


def command_connect(args: argparse.Namespace) -> None:
    library_dir = Path(args.library_dir).expanduser()
    say(f"Plan: connect existing private repository {args.repo}")
    say(f"Plan: record existing local checkout {library_dir} without changing it")
    if not args.apply:
        say("Preview only. Re-run with --apply to save the local connection.")
        return
    require_gh_auth()
    verify_private_repo(args.repo)
    if not (library_dir / ".git").exists():
        fail(f"Existing library is not a Git repository: {library_dir}")
    if not (library_dir / "skills").is_dir():
        fail(f"Existing library has no skills directory: {library_dir / 'skills'}")
    save_state(args.repo, library_dir, Path(args.state_file).expanduser())
    say("Existing private library is connected. No repository files were changed.")


def iter_text_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in TEXT_SUFFIXES:
            try:
                if path.stat().st_size <= 2_000_000:
                    yield path
            except OSError:
                continue


def scan_secrets(root: Path) -> list[str]:
    findings: list[str] = []
    for path in iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{path}: possible {label}")
    return findings


def validate_skill_links(root: Path) -> None:
    resolved_root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            path.resolve(strict=False).relative_to(resolved_root)
        except ValueError:
            fail(f"Skill contains a link outside its own directory: {path}")


def resolved_library(args: argparse.Namespace) -> tuple[str, Path, Path]:
    state_path = Path(args.state_file).expanduser()
    state = load_state(state_path)
    repo = getattr(args, "repo", None) or state.get("private_repo")
    directory = getattr(args, "library_dir", None) or state.get("library_dir")
    if not repo or not directory:
        fail("Private library is not configured. Run setup first or pass --repo and --library-dir.")
    return str(repo), Path(directory).expanduser(), state_path


def command_import(args: argparse.Namespace) -> None:
    repo, library_dir, _ = resolved_library(args)
    source = Path(args.source).expanduser().resolve()
    if not (source / "SKILL.md").is_file():
        fail(f"Source is not a skill directory (missing SKILL.md): {source}")
    validate_skill_links(source)
    findings = scan_secrets(source)
    if findings:
        fail("Sensitive-looking content found; review it before importing:\n- " + "\n- ".join(findings))
    target = library_dir / "skills" / (args.name or source.name)
    say(f"Plan: copy {source} to {target}")
    say("Plan: keep the change local until an explicit sync")
    if target.exists() or target.is_symlink():
        fail(f"Target already exists; no files were overwritten: {target}")
    if not args.apply:
        say("Preview only. Re-run with --apply to import.")
        return
    if not (library_dir / ".git").exists():
        fail(f"Private library is not a Git repository: {library_dir}")
    require_gh_auth()
    verify_private_repo(repo)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True)
    say(f"Imported {target.name}. Run sync to review and publish it to the private repository.")


def command_track_source(args: argparse.Namespace) -> None:
    repo, library_dir, _ = resolved_library(args)
    entry = validate_source_entry({
        "name": args.name,
        "kind": args.kind,
        "source": args.source,
        "path": args.path,
        "ref": args.ref,
    })
    manifest = load_library_manifest(library_dir)
    sources = manifest["sources"]
    previous = next((item for item in sources if item["name"] == entry["name"]), None)
    if previous == entry:
        say(f"Source-managed skill is already tracked: {entry['name']}")
        return
    action = "update" if previous else "add"
    say(f"Plan: {action} source-managed skill {entry['name']}")
    if previous:
        say("Current: " + json.dumps(previous, ensure_ascii=False, sort_keys=True))
    say("Planned: " + json.dumps(entry, ensure_ascii=False, sort_keys=True))
    say("Plan: keep the manifest change local until an explicit sync")
    if not args.apply:
        say("Preview only. Re-run with --apply to update the source inventory.")
        return
    if not (library_dir / ".git").exists():
        fail(f"Private library is not a Git repository: {library_dir}")
    require_gh_auth()
    verify_private_repo(repo)
    manifest["sources"] = sorted(
        [item for item in sources if item["name"] != entry["name"]] + [entry],
        key=lambda item: item["name"],
    )
    manifest["updated_at"] = utc_now()
    write_json(library_dir / MANIFEST_NAME, manifest)
    say(f"Tracked {entry['name']}. Run sync to publish the source inventory.")


def command_sources(args: argparse.Namespace) -> None:
    _, library_dir, _ = resolved_library(args)
    sources = load_library_manifest(library_dir)["sources"]
    if args.json:
        print(json.dumps({"sources": sources}, indent=2, ensure_ascii=False))
        return
    say(f"Source-managed skills: {len(sources)}")
    for item in sources:
        detail = item["source"]
        if item.get("path"):
            detail += f" path={item['path']}"
        if item.get("ref"):
            detail += f" ref={item['ref']}"
        say(f"- {item['name']} [{item['kind']}]: {detail}")


def worktree_dirty(library_dir: Path) -> bool:
    return bool(git(library_dir, "status", "--porcelain").stdout.strip())


def manager_update_status(repo_dir: Path) -> dict:
    repo_dir = repo_dir.expanduser().resolve()
    if not (repo_dir / ".git").exists():
        fail(f"Manager directory is not a Git repository: {repo_dir}")
    branch_result = git(repo_dir, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch_result.returncode:
        fail("Manager update check requires a branch checkout, not detached HEAD")
    branch = branch_result.stdout.strip()
    fetch = git(repo_dir, "fetch", "--quiet", "origin", branch, check=False)
    if fetch.returncode:
        detail = (fetch.stderr or fetch.stdout or "fetch failed").strip()
        fail(f"Cannot check manager updates from origin/{branch}: {detail}")
    local = git(repo_dir, "rev-parse", "HEAD").stdout.strip()
    remote = git(repo_dir, "rev-parse", "FETCH_HEAD").stdout.strip()
    if local == remote:
        state = "current"
    elif git(repo_dir, "merge-base", "--is-ancestor", local, remote, check=False).returncode == 0:
        state = "update-available"
    elif git(repo_dir, "merge-base", "--is-ancestor", remote, local, check=False).returncode == 0:
        state = "local-ahead"
    else:
        state = "diverged"
    return {
        "manager_dir": str(repo_dir),
        "branch": branch,
        "state": state,
        "local_commit": local,
        "remote_commit": remote,
    }


def ahead_behind(library_dir: Path) -> tuple[int, int]:
    upstream = git(library_dir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    if upstream.returncode:
        return 0, 0
    counts = git(library_dir, "rev-list", "--left-right", "--count", "HEAD...@{u}").stdout.split()
    return int(counts[0]), int(counts[1])


def command_sync(args: argparse.Namespace) -> None:
    repo, library_dir, _ = resolved_library(args)
    if not (library_dir / ".git").exists():
        fail(f"Private library is not a Git repository: {library_dir}")
    findings = scan_secrets(library_dir / "skills") if (library_dir / "skills").exists() else []
    if findings:
        fail("Sync blocked by sensitive-looking content:\n- " + "\n- ".join(findings))
    status = git(library_dir, "status", "--short").stdout.strip()
    say("Local changes:")
    say(status or "(none)")
    if not args.apply:
        say("Preview only. Re-run with --apply to fetch, commit allowlisted files, and push.")
        return
    require_gh_auth()
    verify_private_repo(repo)
    git(library_dir, "fetch", "origin")
    ahead, behind = ahead_behind(library_dir)
    if behind and (ahead or worktree_dirty(library_dir)):
        fail("Remote and local changes require review. No merge or conflict resolution was attempted.")
    if behind:
        git(library_dir, "pull", "--ff-only")
    committed = git_commit_if_needed(library_dir, args.message)
    ahead, _ = ahead_behind(library_dir)
    if committed or ahead:
        git(library_dir, "push", "origin", "HEAD")
    say("Private library is synchronized.")


def command_status(args: argparse.Namespace) -> None:
    repo, library_dir, _ = resolved_library(args)
    data = {
        "private_repo": repo,
        "library_dir": str(library_dir),
        "exists": library_dir.exists(),
        "git_repository": (library_dir / ".git").exists(),
        "skills": [],
        "sources": [],
        "changes": None,
    }
    skills_dir = library_dir / "skills"
    if skills_dir.exists():
        data["skills"] = sorted(path.name for path in skills_dir.iterdir() if (path / "SKILL.md").is_file())
    manifest = library_dir / MANIFEST_NAME
    if manifest.exists():
        data["sources"] = load_library_manifest(library_dir)["sources"]
    if data["git_repository"]:
        data["changes"] = git(library_dir, "status", "--short").stdout.splitlines()
    if args.json:
        print(json.dumps(data, indent=2))
        return
    say(f"Private repository: {repo}")
    say(f"Local library: {library_dir} ({'ready' if data['git_repository'] else 'not ready'})")
    say(f"Backed-up skills: {len(data['skills'])}")
    say(f"Source-managed skills: {len(data['sources'])}")
    say(f"Uncommitted changes: {len(data['changes'] or [])}")


def restore_actions(library_dir: Path, target_root: Path) -> tuple[list[tuple[Path, Path]], int]:
    if not (library_dir / ".git").exists():
        fail(f"Restore directory is not a Git repository: {library_dir}")
    skills_dir = library_dir / "skills"
    if not skills_dir.is_dir():
        fail(f"Restore directory has no skills directory: {skills_dir}")
    skills = [
        skill for skill in sorted(skills_dir.iterdir())
        if (skill / "SKILL.md").is_file()
    ]
    create: list[tuple[Path, Path]] = []
    already_linked = 0
    for skill in skills:
        target = target_root / skill.name
        if target.exists() or target.is_symlink():
            if target.is_symlink() and target.resolve() == skill.resolve():
                already_linked += 1
                continue
            fail(f"Restore stopped because target already exists: {target}")
        create.append((skill, target))
    return create, already_linked


def command_restore(args: argparse.Namespace) -> None:
    library_dir = Path(args.library_dir).expanduser()
    target_root = Path(args.target).expanduser()
    say(f"Plan: clone private repository {args.repo} into {library_dir} if needed")
    if library_dir.exists():
        create, already_linked = restore_actions(library_dir, target_root)
        source_count = len(source_inventory(library_dir))
        say(f"Plan: create {len(create)} skill links; {already_linked} are already correct")
        say(f"Plan: let the Agent reconcile {source_count} source-managed skills")
    else:
        say(f"Plan: link restored skills into {target_root} after cloning")
    if not args.apply:
        say("Preview only. Re-run with --apply to restore.")
        return
    require_gh_auth()
    verify_private_repo(args.repo)
    if not library_dir.exists():
        library_dir.parent.mkdir(parents=True, exist_ok=True)
        run(["gh", "repo", "clone", args.repo, str(library_dir)])
    create, already_linked = restore_actions(library_dir, target_root)
    source_count = len(source_inventory(library_dir))
    target_root.mkdir(parents=True, exist_ok=True)
    for skill, target in create:
        target.symlink_to(skill, target_is_directory=True)
    save_state(args.repo, library_dir, Path(args.state_file).expanduser())
    say(f"Restore completed: created {len(create)} links; {already_linked} were already correct.")
    say(f"Source-managed skills to reconcile through their official sources: {source_count}.")


def command_doctor(args: argparse.Namespace) -> None:
    checks: list[tuple[str, bool, str]] = []
    for tool in ("git", "gh"):
        path = shutil.which(tool)
        checks.append((tool, bool(path), path or "not found"))
    gh_ok = False
    if shutil.which("gh"):
        result = run(["gh", "auth", "status", "--hostname", "github.com"], check=False)
        gh_ok = result.returncode == 0
        checks.append(("github-login", gh_ok, "authenticated" if gh_ok else "run: gh auth login -h github.com"))
    state_path = Path(args.state_file).expanduser()
    state = load_state(state_path)
    checks.append(("state", bool(state.get("private_repo")), str(state_path)))
    if state.get("library_dir"):
        library_dir = Path(state["library_dir"])
        checks.append(("library", (library_dir / ".git").exists(), str(library_dir)))
    if args.json:
        print(json.dumps([{"check": a, "ok": b, "detail": c} for a, b, c in checks], indent=2))
    else:
        for name, ok, detail in checks:
            say(f"{'OK' if ok else 'ACTION'} {name}: {detail}")
    if not all(ok for _, ok, _ in checks):
        sys.exit(1)


def command_manager_status(args: argparse.Namespace) -> None:
    repo_dir = Path(args.manager_dir).expanduser()
    status = manager_update_status(repo_dir)
    if args.json:
        print(json.dumps(status, indent=2))
        return
    say(f"Manager status: {status['state']} ({status['branch']})")
    if status["state"] == "update-available":
        say("A fast-forward manager update is available.")
    elif status["state"] in {"local-ahead", "diverged"}:
        say("Automatic manager update is blocked until the Git history is reviewed.")


def command_update_manager(args: argparse.Namespace) -> None:
    repo_dir = Path(args.manager_dir).expanduser()
    status = manager_update_status(repo_dir)
    if status["state"] == "current":
        say("Manager is already current. Private skills were not modified.")
        return
    if status["state"] != "update-available":
        fail(f"Manager update stopped because repository state is {status['state']}")
    say(f"Plan: fast-forward manager at {repo_dir} to origin/{status['branch']}")
    say("Private skill library will not be touched.")
    if not args.apply:
        say("Preview only. Re-run with --apply to update.")
        return
    if worktree_dirty(repo_dir):
        fail("Manager has local changes. Commit or review them before updating.")
    git(repo_dir, "merge", "--ff-only", "FETCH_HEAD")
    say("Manager updated. Private skills were not modified.")


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-file", default=str(STATE_PATH), help="Path to local, credential-free state JSON")
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Discover and classify local skills")
    scan.add_argument("--root", action="append", default=[], help="Root to scan; repeatable")
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=command_scan)

    setup = sub.add_parser("setup", parents=[common], help="Create or connect a private GitHub skill library")
    setup.add_argument("--repo", required=True, help="Private GitHub repository as OWNER/NAME")
    setup.add_argument("--library-dir", default=str(Path.home() / ".local" / "share" / "my-skills"))
    setup.add_argument("--apply", action="store_true")
    setup.set_defaults(func=command_setup)

    connect = sub.add_parser("connect", parents=[common], help="Connect an existing private library without changing it")
    connect.add_argument("--repo", required=True, help="Existing private GitHub repository as OWNER/NAME")
    connect.add_argument("--library-dir", required=True, help="Existing local Git checkout")
    connect.add_argument("--apply", action="store_true")
    connect.set_defaults(func=command_connect)

    imp = sub.add_parser("import", parents=[common], help="Copy one user-owned skill into the private library")
    imp.add_argument("source")
    imp.add_argument("--name")
    imp.add_argument("--repo")
    imp.add_argument("--library-dir")
    imp.add_argument("--apply", action="store_true")
    imp.set_defaults(func=command_import)

    track = sub.add_parser("track-source", parents=[common], help="Track an open-source or provider-managed skill")
    track.add_argument("--name", required=True)
    track.add_argument("--kind", required=True, choices=sorted(SOURCE_KINDS))
    track.add_argument("--source", required=True, help="Portable source identifier or URL")
    track.add_argument("--path", default="", help="Optional skill path within the source")
    track.add_argument("--ref", default="", help="Optional branch, tag, or commit")
    track.add_argument("--repo")
    track.add_argument("--library-dir")
    track.add_argument("--apply", action="store_true")
    track.set_defaults(func=command_track_source)

    sources = sub.add_parser("sources", parents=[common], help="List source-managed skills")
    sources.add_argument("--repo")
    sources.add_argument("--library-dir")
    sources.add_argument("--json", action="store_true")
    sources.set_defaults(func=command_sources)

    sync = sub.add_parser("sync", parents=[common], help="Safely commit and push private library changes")
    sync.add_argument("--repo")
    sync.add_argument("--library-dir")
    sync.add_argument("--message", default="Update personal skills")
    sync.add_argument("--apply", action="store_true")
    sync.set_defaults(func=command_sync)

    status = sub.add_parser("status", parents=[common], help="Show private library status")
    status.add_argument("--repo")
    status.add_argument("--library-dir")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    restore = sub.add_parser("restore", parents=[common], help="Restore private skills on a new machine")
    restore.add_argument("--repo", required=True)
    restore.add_argument("--library-dir", default=str(Path.home() / ".local" / "share" / "my-skills"))
    restore.add_argument("--target", default=str(Path.home() / ".codex" / "skills"))
    restore.add_argument("--apply", action="store_true")
    restore.set_defaults(func=command_restore)

    doctor = sub.add_parser("doctor", parents=[common], help="Diagnose Git, GitHub, state, and library setup")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=command_doctor)

    manager_status = sub.add_parser("manager-status", help="Check this public manager for updates")
    manager_status.add_argument("--manager-dir", default=str(DEFAULT_MANAGER_DIR))
    manager_status.add_argument("--json", action="store_true")
    manager_status.set_defaults(func=command_manager_status)

    update = sub.add_parser("update-manager", help="Safely fast-forward this public manager checkout")
    update.add_argument("--manager-dir", default=str(DEFAULT_MANAGER_DIR))
    update.add_argument("--apply", action="store_true")
    update.set_defaults(func=command_update_manager)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    try:
        add_user_local_bin_to_path()
        args = parser().parse_args(argv)
        args.func(args)
        return 0
    except ManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
