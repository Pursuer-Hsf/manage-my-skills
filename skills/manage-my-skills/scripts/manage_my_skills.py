#!/usr/bin/env python3
"""Safely inventory, preserve, synchronize, and restore agent skills."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence
import urllib.error
import urllib.request
from urllib.parse import urlparse


LIBRARY_SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 2
FLEET_SCHEMA_VERSION = 1
# Kept for callers that imported the original library schema constant.
SCHEMA_VERSION = LIBRARY_SCHEMA_VERSION
STATE_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
STATE_PATH = STATE_HOME / "manage-my-skills" / "state.json"
DEFAULT_MANAGER_DIR = Path(__file__).resolve().parents[3]
NETWORK_CHECK_TIMEOUT_SECONDS = 10
MANIFEST_NAME = "library.json"
FLEET_NAME = "fleet.json"
DEFAULT_TARGET_ROOT = Path.home() / ".codex" / "skills"
ALLOWED_TOP_LEVEL = {MANIFEST_NAME, FLEET_NAME, "skills"}
SOURCE_KINDS = {"github", "marketplace", "plugin", "other"}
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
GITHUB_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
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
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(args),
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        detail = f"command timed out after {timeout:g} seconds" if timeout is not None else "command timed out"
        if check:
            fail(f"Command timed out: {' '.join(args)}\n{detail}")
        return subprocess.CompletedProcess(list(args), 124, "", detail)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()
        fail(f"Command failed: {' '.join(args)}\n{detail}")
    return result


def github_network_check() -> tuple[bool, str]:
    proxies = urllib.request.getproxies()
    proxy_configured = any(key in proxies for key in ("http", "https", "all"))
    route = "a configured proxy" if proxy_configured else "a direct connection"
    request = urllib.request.Request("https://github.com/", method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_CHECK_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None) or response.getcode()
        return True, f"reachable via {route} (HTTP {status})"
    except urllib.error.HTTPError as exc:
        return True, f"reachable via {route} (HTTP {exc.code})"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        detail = str(reason).strip() or exc.__class__.__name__
        if proxy_configured:
            suggestion = "A proxy is configured; verify that it can reach github.com."
        else:
            suggestion = "No proxy was detected; configure HTTPS_PROXY or ALL_PROXY, or ask the Agent to request network access."
        return False, f"unreachable ({detail}). {suggestion}"


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
        return {"schema_version": STATE_SCHEMA_VERSION}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Cannot read state file {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"State file must contain a JSON object: {path}")
    schema_version = value.get("schema_version", 1)
    if not isinstance(schema_version, int) or not 1 <= schema_version <= STATE_SCHEMA_VERSION:
        fail(f"Unsupported state schema version in {path}: {schema_version}")
    return value


def normalize_machine_id(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError):
        fail(f"Machine ID must be a UUID: {value}")


def normalize_machine_label(value: object) -> str:
    label = str(value)
    if not SKILL_NAME_PATTERN.fullmatch(label):
        fail(f"Machine label must use lowercase letters, digits, and hyphens: {label}")
    return label


def normalize_roles(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        fail("Machine roles must be a list of lowercase labels")
    return sorted({normalize_machine_label(item) for item in value})


def local_machine(state: dict) -> dict | None:
    fields = ("machine_id", "machine_label", "target_root")
    present = [field in state and state[field] not in (None, "") for field in fields]
    if not any(present):
        return None
    if not all(present):
        fail("Local machine state is incomplete; re-register this machine before modifying skills")
    target_root = str(state["target_root"])
    if not target_root:
        fail("Local machine target root must not be empty")
    return {
        "id": normalize_machine_id(state["machine_id"]),
        "label": normalize_machine_label(state["machine_label"]),
        "target_root": str(Path(target_root).expanduser().resolve()),
    }


def save_state(
    repo: str,
    library_dir: Path,
    path: Path = STATE_PATH,
    *,
    machine_id: str | None = None,
    machine_label: str | None = None,
    target_root: Path | None = None,
) -> None:
    previous = load_state(path)
    previous_machine = local_machine(previous)
    selected_id = machine_id or (previous_machine or {}).get("id")
    selected_label = machine_label or (previous_machine or {}).get("label")
    selected_target = target_root or (
        Path(previous_machine["target_root"]) if previous_machine else None
    )
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "private_repo": repo,
        "library_dir": str(library_dir.expanduser().resolve()),
        "updated_at": utc_now(),
    }
    if selected_id or selected_label or selected_target:
        if not (selected_id and selected_label and selected_target):
            fail("A local machine registration needs an ID, label, and target root")
        state.update({
            "machine_id": normalize_machine_id(selected_id),
            "machine_label": normalize_machine_label(selected_label),
            "target_root": str(selected_target.expanduser().resolve()),
        })
    write_json(path, state)


def default_roots() -> list[Path]:
    roots = [
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills",
        Path.home() / ".agents" / "skills",
        Path.home() / ".claude" / "skills",
    ]
    return [path for path in roots if path.exists()]


def fleet_manifest() -> dict:
    return {
        "schema_version": FLEET_SCHEMA_VERSION,
        "machines": [],
    }


def validate_fleet_entry(entry: dict) -> dict:
    allowed = {"id", "label", "roles", "enabled"}
    unexpected = sorted(set(entry) - allowed)
    if unexpected:
        fail("Fleet entry contains unsupported fields: " + ", ".join(unexpected))
    if "id" not in entry or "label" not in entry:
        fail("Fleet entry must contain an ID and label")
    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        fail("Fleet machine enabled must be true or false")
    serialized = json.dumps(entry, ensure_ascii=False)
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(serialized):
            fail(f"Fleet entry contains a possible {label}")
    normalized = {
        "id": normalize_machine_id(entry["id"]),
        "label": normalize_machine_label(entry["label"]),
        "roles": normalize_roles(entry.get("roles", [])),
        "enabled": enabled,
    }
    return normalized


def load_fleet(library_dir: Path, *, required: bool = False) -> dict:
    path = library_dir / FLEET_NAME
    if not path.exists():
        if required:
            fail(f"Private library has no {FLEET_NAME}: {path}")
        return fleet_manifest()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Cannot read fleet inventory {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"Fleet inventory must contain a JSON object: {path}")
    unexpected = sorted(set(value) - {"schema_version", "machines"})
    if unexpected:
        fail("Fleet inventory contains unsupported fields: " + ", ".join(unexpected))
    if value.get("schema_version") != FLEET_SCHEMA_VERSION:
        fail(f"Unsupported fleet schema version in {path}: {value.get('schema_version')}")
    machines = value.get("machines")
    if not isinstance(machines, list):
        fail(f"Fleet machines must be a JSON list: {path}")
    if not all(isinstance(item, dict) for item in machines):
        fail(f"Every fleet machine must be a JSON object: {path}")
    normalized = [validate_fleet_entry(item) for item in machines]
    ids = [item["id"] for item in normalized]
    labels = [item["label"] for item in normalized]
    if len(ids) != len(set(ids)):
        fail(f"Fleet inventory has duplicate machine IDs: {path}")
    if len(labels) != len(set(labels)):
        fail(f"Fleet inventory has duplicate machine labels: {path}")
    return {
        "schema_version": FLEET_SCHEMA_VERSION,
        "machines": sorted(normalized, key=lambda item: (item["label"], item["id"])),
    }


def ensure_fleet(library_dir: Path) -> dict:
    path = library_dir / FLEET_NAME
    if not path.exists():
        write_json(path, fleet_manifest())
    return load_fleet(library_dir, required=True)


def register_fleet_machine(fleet: dict, machine: dict, roles: list[str]) -> tuple[dict, bool]:
    normalized_roles = normalize_roles(roles)
    existing = next((item for item in fleet["machines"] if item["id"] == machine["id"]), None)
    if existing:
        if existing["label"] != machine["label"]:
            fail(
                f"Machine ID {machine['id']} is already registered as {existing['label']}; "
                "do not reuse a machine identity"
            )
        if not existing["enabled"]:
            fail(f"Machine {machine['label']} is disabled in the fleet inventory")
        if normalized_roles and normalized_roles != existing["roles"]:
            fail(
                f"Machine {machine['label']} is already registered with different roles; "
                "review the fleet inventory before changing it"
            )
        return fleet, False
    if any(item["label"] == machine["label"] for item in fleet["machines"]):
        fail(f"Machine label is already registered: {machine['label']}")
    updated = {
        "schema_version": FLEET_SCHEMA_VERSION,
        "machines": sorted(
            fleet["machines"] + [{
                "id": machine["id"],
                "label": machine["label"],
                "roles": normalized_roles,
                "enabled": True,
            }],
            key=lambda item: (item["label"], item["id"]),
        ),
    }
    return updated, True


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
    say(f"Full local scan: found {len(records)} skills in {len(roots)} roots:")
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
    network_ok, network_detail = github_network_check()
    if not network_ok:
        fail(f"GitHub network is unavailable: {network_detail}")
    ok, detail = github_auth_check()
    if ok:
        return
    if detail == "not authenticated":
        fail("GitHub CLI is not logged in. Run: gh auth login -h github.com")
    fail(f"GitHub CLI authentication check unavailable: {detail}")


def github_auth_check() -> tuple[bool, str]:
    result = run(
        ["gh", "auth", "status", "--hostname", "github.com"],
        check=False,
        timeout=NETWORK_CHECK_TIMEOUT_SECONDS,
    )
    if result.returncode == 0:
        return True, "authenticated"
    output = f"{result.stdout}\n{result.stderr}".lower()
    if any(marker in output for marker in ("not logged in", "not authenticated", "no accounts")):
        return False, "not authenticated"
    return False, "authentication check unavailable; verify network access and the credential store"


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


def canonical_repository_slug(value: object) -> str:
    slug = str(value).strip()
    if not GITHUB_REPOSITORY_PATTERN.fullmatch(slug):
        fail(f"GitHub repository must use OWNER/NAME form: {value}")
    return slug.casefold()


def github_repository_from_remote(remote: str) -> str | None:
    value = remote.strip()
    ssh_match = re.fullmatch(r"(?:[^@]+@)?github\.com:([^/]+)/([^/]+)/?", value, re.IGNORECASE)
    if ssh_match:
        owner, name = ssh_match.groups()
    else:
        parsed = urlparse(value)
        if not parsed.hostname or parsed.hostname.casefold() != "github.com":
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            return None
        owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    try:
        return canonical_repository_slug(f"{owner}/{name}")
    except ManagerError:
        return None


def github_https_remote(remote: str) -> bool:
    parsed = urlparse(remote.strip())
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").casefold() == "github.com"


def assert_checkout_matches_repository(library_dir: Path, repo: str) -> None:
    expected = canonical_repository_slug(repo)
    result = git(library_dir, "remote", "get-url", "origin", check=False)
    if result.returncode:
        fail("Private library has no origin remote; clone the verified private repository before continuing")
    actual = github_repository_from_remote(result.stdout)
    if actual != expected:
        fail("Private library origin does not match the verified GitHub repository; no changes were made")


def verify_private_checkout(repo: str, library_dir: Path) -> None:
    require_gh_auth()
    verify_private_repo(repo)
    assert_checkout_matches_repository(library_dir, repo)


def library_manifest(repo: str) -> dict:
    return {
        "schema_version": LIBRARY_SCHEMA_VERSION,
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


def git(
    cwd: Path,
    *args: str,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd, check=check, timeout=timeout)


def git_commit_if_needed(library_dir: Path, message: str) -> bool:
    paths = [MANIFEST_NAME, "skills"]
    if (library_dir / FLEET_NAME).exists():
        paths.append(FLEET_NAME)
    git(library_dir, "add", "--", *paths)
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


def ensure_private_checkout(repo: str, library_dir: Path, *, create_repo: bool) -> None:
    require_gh_auth()
    info = repo_info(repo)
    if info is None:
        if not create_repo:
            fail(f"GitHub repository does not exist or is not accessible: {repo}")
        run([
            "gh", "repo", "create", repo, "--private",
            "--description", "Private backup of personal agent skills",
        ])
    verify_private_repo(repo)
    if library_dir.exists() and any(library_dir.iterdir()):
        if not (library_dir / ".git").exists():
            fail(f"Library directory is not empty and is not a Git repository: {library_dir}")
    else:
        library_dir.parent.mkdir(parents=True, exist_ok=True)
        run(["gh", "repo", "clone", repo, str(library_dir)])
    if not (library_dir / ".git").exists():
        fail(f"Expected a cloned Git repository at {library_dir}")
    assert_checkout_matches_repository(library_dir, repo)


def refresh_library_for_mutation(library_dir: Path) -> None:
    if worktree_dirty(library_dir):
        fail("Private library has local changes. Review or synchronize them before changing the fleet.")
    git(library_dir, "fetch", "origin")
    ahead, behind = ahead_behind(library_dir)
    if behind and ahead:
        fail("Private library history diverged. No merge or conflict resolution was attempted.")
    if ahead:
        fail("Private library has local commits. Synchronize or review them before changing the private library.")
    if behind:
        git(library_dir, "pull", "--ff-only")


def requested_machine(args: argparse.Namespace, state: dict) -> dict:
    current = local_machine(state)
    supplied_id = getattr(args, "machine_id", None)
    machine_id = normalize_machine_id(supplied_id) if supplied_id else (current["id"] if current else str(uuid.uuid4()))
    if current and supplied_id and machine_id != current["id"]:
        fail("This machine already has a different ID; do not replace local machine identity")
    supplied_label = getattr(args, "label", None)
    machine_label = normalize_machine_label(supplied_label or (current or {}).get("label", ""))
    if current and supplied_label and machine_label != current["label"]:
        fail("This machine already has a different label; review the fleet before renaming it")
    supplied_target = getattr(args, "target", None)
    target_root = Path(
        supplied_target or (current or {}).get("target_root") or DEFAULT_TARGET_ROOT
    ).expanduser().resolve()
    return {
        "id": machine_id,
        "label": machine_label,
        "target_root": str(target_root),
    }


def commit_and_push_library(library_dir: Path, message: str) -> bool:
    committed = git_commit_if_needed(library_dir, message)
    if committed:
        git(library_dir, "push", "-u", "origin", "HEAD")
    return committed


def command_setup(args: argparse.Namespace) -> None:
    library_dir = Path(args.library_dir).expanduser().resolve()
    say(f"Plan: create or verify private repository {args.repo}")
    say(f"Plan: initialize private skill library at {library_dir}")
    if not args.apply:
        say("Preview only. Re-run with --apply to make these changes.")
        return
    ensure_private_checkout(args.repo, library_dir, create_repo=True)
    refresh_library_for_mutation(library_dir)
    ensure_library_files(library_dir, args.repo)
    commit_and_push_library(library_dir, "Initialize private skill library")
    save_state(args.repo, library_dir, Path(args.state_file).expanduser())
    say("Private library is ready and its visibility was verified as Private.")


def command_bootstrap(args: argparse.Namespace) -> None:
    library_dir = Path(args.library_dir).expanduser().resolve()
    state_path = Path(args.state_file).expanduser()
    state = load_state(state_path)
    machine = requested_machine(args, state)
    roles = normalize_roles(args.role)
    say(f"Plan: create or verify private repository {args.repo}")
    say(f"Plan: initialize private skill library at {library_dir}")
    say(f"Plan: create or verify portable fleet inventory at {library_dir / FLEET_NAME}")
    say(f"Plan: register this machine as {machine['label']} ({machine['id']})")
    if not local_machine(state) and not args.machine_id:
        say(f"Plan: reuse reviewed machine ID {machine['id']} when applying this preview")
    say(f"Plan: record local skill target {machine['target_root']} without restoring links")
    if not args.apply:
        say("Preview only. Re-run with --apply to bootstrap this machine.")
        return
    ensure_private_checkout(args.repo, library_dir, create_repo=True)
    refresh_library_for_mutation(library_dir)
    ensure_library_files(library_dir, args.repo)
    fleet = ensure_fleet(library_dir)
    fleet, added = register_fleet_machine(fleet, machine, roles)
    if added:
        write_json(library_dir / FLEET_NAME, fleet)
    commit_and_push_library(library_dir, "Bootstrap personal skill library")
    save_state(
        args.repo,
        library_dir,
        state_path,
        machine_id=machine["id"],
        machine_label=machine["label"],
        target_root=Path(machine["target_root"]),
    )
    say(f"Bootstrapped {machine['label']}. Personal skills and source records remain unchanged.")


def command_join(args: argparse.Namespace) -> None:
    library_dir = Path(args.library_dir).expanduser().resolve()
    state_path = Path(args.state_file).expanduser()
    state = load_state(state_path)
    machine = requested_machine(args, state)
    roles = normalize_roles(args.role)
    say(f"Plan: clone or verify private repository {args.repo} at {library_dir}")
    say(f"Plan: register this machine as {machine['label']} ({machine['id']})")
    if not local_machine(state) and not args.machine_id:
        say(f"Plan: reuse reviewed machine ID {machine['id']} when applying this preview")
    say(f"Plan: record local skill target {machine['target_root']} without restoring links")
    if not args.apply:
        say("Preview only. Re-run with --apply to join this machine.")
        return
    ensure_private_checkout(args.repo, library_dir, create_repo=False)
    refresh_library_for_mutation(library_dir)
    load_library_manifest(library_dir)
    fleet = ensure_fleet(library_dir)
    fleet, added = register_fleet_machine(fleet, machine, roles)
    if added:
        write_json(library_dir / FLEET_NAME, fleet)
    commit_and_push_library(library_dir, f"Register machine {machine['label']}")
    save_state(
        args.repo,
        library_dir,
        state_path,
        machine_id=machine["id"],
        machine_label=machine["label"],
        target_root=Path(machine["target_root"]),
    )
    say(f"Joined private library as {machine['label']}. Run restore separately to preview skill links.")


def command_connect(args: argparse.Namespace) -> None:
    library_dir = Path(args.library_dir).expanduser().resolve()
    say(f"Plan: connect existing private repository {args.repo}")
    say(f"Plan: record existing local checkout {library_dir} without changing it")
    if not args.apply:
        say("Preview only. Re-run with --apply to save the local connection.")
        return
    if not (library_dir / ".git").exists():
        fail(f"Existing library is not a Git repository: {library_dir}")
    if not (library_dir / "skills").is_dir():
        fail(f"Existing library has no skills directory: {library_dir / 'skills'}")
    verify_private_checkout(args.repo, library_dir)
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
    return str(repo), Path(directory).expanduser().resolve(), state_path


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
    verify_private_checkout(repo, library_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True)
    say(f"Imported {target.name}. Run sync to review and publish it to the private repository.")


def skill_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root))):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            record = f"link\0{relative}\0{os.readlink(path)}\0".encode("utf-8")
            digest.update(record)
            continue
        if path.is_dir():
            digest.update(f"dir\0{relative}\0".encode("utf-8"))
            continue
        if path.is_file():
            digest.update(f"file\0{relative}\0".encode("utf-8"))
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
    return digest.hexdigest()


def resolved_target_root(args: argparse.Namespace, state: dict) -> Path:
    machine = local_machine(state)
    target = getattr(args, "target", None) or (machine or {}).get("target_root") or DEFAULT_TARGET_ROOT
    return Path(target).expanduser().resolve()


def require_registered_machine(state: dict, library_dir: Path, *, action: str) -> dict:
    machine = local_machine(state)
    if not machine:
        fail(f"This machine is not registered. Run bootstrap or join before {action}")
    fleet = load_fleet(library_dir, required=True)
    entry = next((item for item in fleet["machines"] if item["id"] == machine["id"]), None)
    if not entry:
        fail(f"This machine is not registered in fleet.json. Run join before {action}")
    if not entry["enabled"]:
        fail(f"This machine is disabled in fleet.json: {machine['label']}")
    return machine


def command_adopt(args: argparse.Namespace) -> None:
    repo, library_dir, state_path = resolved_library(args)
    state = load_state(state_path)
    require_registered_machine(state, library_dir, action="adopting a local skill")
    target_root = resolved_target_root(args, state)
    source_path = Path(args.source).expanduser()
    if source_path.is_symlink():
        fail(f"Adopt expects a real skill directory, not an existing link: {source_path}")
    source = source_path.resolve()
    if not (source / "SKILL.md").is_file():
        fail(f"Source is not a skill directory (missing SKILL.md): {source}")
    if source.parent != target_root:
        fail(
            "Adopt requires a skill directly inside its managed target root: "
            f"{source} is not inside {target_root}"
        )
    validate_skill_links(source)
    findings = scan_secrets(source)
    if findings:
        fail("Sensitive-looking content found; review it before adopting:\n- " + "\n- ".join(findings))
    name = args.name or source.name
    if not SKILL_NAME_PATTERN.fullmatch(name):
        fail(f"Managed skill name must use lowercase letters, digits, and hyphens: {name}")
    library_skill = library_dir / "skills" / name
    backup = target_root.parent / ".manage-my-skills-backups" / name
    say(f"Plan: copy {source} to {library_skill}")
    say(f"Plan: move the existing skill to preserved backup {backup}")
    say(f"Plan: create a link from {source} to {library_skill}")
    say("Plan: keep the private-library change local until an explicit sync")
    if library_skill.exists() or library_skill.is_symlink():
        fail(f"Private library target already exists; no files were overwritten: {library_skill}")
    if backup.exists() or backup.is_symlink():
        fail(f"Backup target already exists; no files were overwritten: {backup}")
    if not args.apply:
        say("Preview only. Re-run with --apply to adopt this skill.")
        return
    if not (library_dir / ".git").exists():
        fail(f"Private library is not a Git repository: {library_dir}")
    verify_private_checkout(repo, library_dir)
    library_skill.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, library_skill, symlinks=True)
    if skill_fingerprint(source) != skill_fingerprint(library_skill):
        fail(f"Copied skill did not verify; original was left in place: {source}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(backup))
    try:
        source.symlink_to(library_skill, target_is_directory=True)
        if not source.is_symlink() or source.resolve() != library_skill.resolve():
            raise OSError("created link does not resolve to the private library skill")
    except OSError as exc:
        try:
            shutil.move(str(backup), str(source))
        except OSError as rollback_exc:
            fail(
                f"Could not create managed link ({exc}) and could not restore original ({rollback_exc}). "
                f"Backup remains at {backup}"
            )
        fail(
            f"Could not create managed link ({exc}); original was restored and copied library skill remains at {library_skill}"
        )
    say(f"Adopted {name}. Original files remain at {backup}; run sync to publish the private copy.")


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
    verify_private_checkout(repo, library_dir)
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
    local = git(repo_dir, "rev-parse", "HEAD").stdout.strip()
    remote_ref = f"refs/remotes/origin/{branch}"
    origin = git(repo_dir, "remote", "get-url", "origin", check=False)
    if origin.returncode == 0 and github_https_remote(origin.stdout):
        network_ok, network_detail = github_network_check()
        if not network_ok:
            return {
                "manager_dir": str(repo_dir),
                "branch": branch,
                "state": "remote-unreachable",
                "local_commit": local,
                "remote_commit": None,
                "remote_ref": remote_ref,
                "error": f"GitHub network check failed: {network_detail}",
            }
    fetch = git(
        repo_dir,
        "fetch",
        "--quiet",
        "origin",
        f"refs/heads/{branch}:{remote_ref}",
        check=False,
        timeout=NETWORK_CHECK_TIMEOUT_SECONDS,
    )
    if fetch.returncode:
        probe = git(
            repo_dir,
            "ls-remote",
            "--refs",
            "origin",
            f"refs/heads/{branch}",
            check=False,
            timeout=NETWORK_CHECK_TIMEOUT_SECONDS,
        )
        if probe.returncode:
            detail = (probe.stderr or probe.stdout or fetch.stderr or fetch.stdout or "remote check failed").strip()
            return {
                "manager_dir": str(repo_dir),
                "branch": branch,
                "state": "remote-unreachable",
                "local_commit": local,
                "remote_commit": None,
                "remote_ref": remote_ref,
                "error": f"Cannot reach origin/{branch}: {detail}",
            }
        remote_lines = [line.split() for line in probe.stdout.splitlines() if line.split()]
        if not remote_lines or len(remote_lines[0]) < 1:
            return {
                "manager_dir": str(repo_dir),
                "branch": branch,
                "state": "remote-unverified",
                "local_commit": local,
                "remote_commit": None,
                "remote_ref": remote_ref,
                "error": "Remote responded without a branch commit; version is unverified.",
            }
        remote = remote_lines[0][0]
        state = "current" if local == remote else "remote-unverified"
        return {
            "manager_dir": str(repo_dir),
            "branch": branch,
            "state": state,
            "local_commit": local,
            "remote_commit": remote,
            "remote_ref": remote_ref,
            "error": None if state == "current" else "Remote is reachable, but local Git history could not be refreshed; version is unverified.",
        }
    remote_result = git(repo_dir, "rev-parse", remote_ref, check=False)
    if remote_result.returncode:
        return {
            "manager_dir": str(repo_dir),
            "branch": branch,
            "state": "remote-unverified",
            "local_commit": local,
            "remote_commit": None,
            "remote_ref": remote_ref,
            "error": "Remote fetch completed but its local tracking ref could not be read; version is unverified.",
        }
    remote = remote_result.stdout.strip()
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
        "remote_ref": remote_ref,
        "error": None,
    }


def ahead_behind(library_dir: Path) -> tuple[int, int]:
    upstream = git(library_dir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    if upstream.returncode:
        return 0, 0
    counts = git(library_dir, "rev-list", "--left-right", "--count", "HEAD...@{u}").stdout.split()
    return int(counts[0]), int(counts[1])


def changed_skill_names(changes: Iterable[str]) -> list[str]:
    names: set[str] = set()
    for raw_line in changes:
        line = raw_line.rstrip()
        if "\t" in line:
            path = line.split("\t")[-1].strip()
        else:
            path = line[3:].strip() if len(line) >= 3 else ""
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        parts = PurePosixPath(path).parts
        if len(parts) >= 2 and parts[0] == "skills":
            names.add(parts[1])
    return sorted(names)


def changed_skill_names_from_revision(library_dir: Path, revision: str) -> list[str]:
    result = git(library_dir, "diff", "--name-status", revision, check=False)
    if result.returncode:
        return []
    return changed_skill_names(result.stdout.splitlines())


def private_remote_status(library_dir: Path) -> dict:
    """Refresh and report the private checkout's upstream without changing tracked files."""
    base = {
        "state": "unconfigured",
        "ahead": 0,
        "behind": 0,
        "skill_changes": [],
        "error": None,
    }
    upstream = git(
        library_dir,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{u}",
        check=False,
    )
    if upstream.returncode:
        return base
    upstream_ref = upstream.stdout.strip()
    remote_name = upstream_ref.split("/", 1)[0]
    if not remote_name:
        return {**base, "state": "remote-unverified", "error": "Private library upstream is invalid."}
    remote_url = git(library_dir, "remote", "get-url", remote_name, check=False)
    if remote_url.returncode:
        return {
            **base,
            "state": "remote-unverified",
            "error": "Private library upstream remote could not be read.",
        }
    if github_https_remote(remote_url.stdout):
        network_ok, network_detail = github_network_check()
        if not network_ok:
            return {**base, "state": "remote-unreachable", "error": network_detail}
    fetch = git(
        library_dir,
        "fetch",
        "--quiet",
        remote_name,
        check=False,
        timeout=NETWORK_CHECK_TIMEOUT_SECONDS,
    )
    if fetch.returncode:
        detail = (fetch.stderr or fetch.stdout or "private-library remote check failed").strip()
        return {**base, "state": "remote-unreachable", "error": detail}
    counts = git(
        library_dir,
        "rev-list",
        "--left-right",
        "--count",
        "HEAD...@{u}",
        check=False,
    )
    if counts.returncode:
        return {
            **base,
            "state": "remote-unverified",
            "error": "Private library remote was fetched, but its history could not be compared.",
        }
    values = counts.stdout.split()
    if len(values) != 2:
        return {
            **base,
            "state": "remote-unverified",
            "error": "Private library remote returned an unreadable history comparison.",
        }
    ahead, behind = (int(values[0]), int(values[1]))
    if ahead == 0 and behind == 0:
        state = "current"
    elif behind and not ahead:
        state = "update-available"
    elif ahead and not behind:
        state = "local-ahead"
    else:
        state = "diverged"
    skill_changes = changed_skill_names_from_revision(library_dir, "HEAD..@{u}") if behind else []
    return {
        "state": state,
        "ahead": ahead,
        "behind": behind,
        "skill_changes": skill_changes,
        "error": None,
    }


def command_sync(args: argparse.Namespace) -> None:
    repo, library_dir, _ = resolved_library(args)
    if not (library_dir / ".git").exists():
        fail(f"Private library is not a Git repository: {library_dir}")
    if (library_dir / FLEET_NAME).exists():
        load_fleet(library_dir, required=True)
    findings = scan_secrets(library_dir / "skills") if (library_dir / "skills").exists() else []
    if findings:
        fail("Sync blocked by sensitive-looking content:\n- " + "\n- ".join(findings))
    verify_private_checkout(repo, library_dir)
    changes = git(library_dir, "status", "--short").stdout.splitlines()
    say("Local changes:")
    say("\n".join(changes) or "(none)")
    pending_skills = changed_skill_names(changes)
    if pending_skills:
        say("Private skills pending sync: " + ", ".join(pending_skills))
    remote = private_remote_status(library_dir)
    if remote["state"] == "update-available":
        say(f"Remote private-library updates: {remote['behind']} commit(s) available")
        if remote["skill_changes"]:
            say("Remote private skills pending sync: " + ", ".join(remote["skill_changes"]))
        else:
            say("Remote changes contain library metadata but no skill file changes.")
    elif remote["state"] in {"local-ahead", "diverged"}:
        say(f"Remote history state: {remote['state']} (ahead {remote['ahead']}, behind {remote['behind']})")
    elif remote["state"] in {"remote-unreachable", "remote-unverified"}:
        say(f"Remote private-library status: {remote['state']} ({remote['error']})")
    if not args.apply:
        say("Preview only. Re-run with --apply to fast-forward, commit allowlisted files, and push.")
        return
    if remote["state"] in {"remote-unreachable", "remote-unverified"}:
        fail(f"Sync stopped because remote status is {remote['state']}: {remote['error']}")
    ahead, behind = remote["ahead"], remote["behind"]
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
    repo, library_dir, state_path = resolved_library(args)
    state = load_state(state_path)
    machine = local_machine(state)
    fleet_path = library_dir / FLEET_NAME
    fleet = load_fleet(library_dir, required=True) if fleet_path.exists() else None
    fleet_machines = (fleet or {}).get("machines", [])
    fleet_entry = next(
        (item for item in fleet_machines if machine and item["id"] == machine["id"]),
        None,
    )
    data = {
        "private_repo": repo,
        "library_dir": str(library_dir),
        "exists": library_dir.exists(),
        "git_repository": (library_dir / ".git").exists(),
        "skills": [],
        "sources": [],
        "changes": None,
        "pending_skill_changes": [],
        "pending_sync": False,
        "remote": {
            "state": None,
            "ahead": 0,
            "behind": 0,
            "skill_changes": [],
            "error": None,
        },
        "machine": machine,
        "fleet": {
            "configured": fleet is not None,
            "registered": fleet_entry is not None,
            "machines": len(fleet_machines),
        },
    }
    skills_dir = library_dir / "skills"
    if skills_dir.exists():
        data["skills"] = sorted(path.name for path in skills_dir.iterdir() if (path / "SKILL.md").is_file())
    manifest = library_dir / MANIFEST_NAME
    if manifest.exists():
        data["sources"] = load_library_manifest(library_dir)["sources"]
    if data["git_repository"]:
        data["changes"] = git(library_dir, "status", "--short").stdout.splitlines()
        data["pending_skill_changes"] = changed_skill_names(data["changes"])
        data["remote"] = private_remote_status(library_dir)
        data["pending_sync"] = bool(data["changes"]) or data["remote"]["behind"] > 0
    if args.json:
        print(json.dumps(data, indent=2))
        return
    say(f"Private repository: {repo}")
    say(f"Local library: {library_dir} ({'ready' if data['git_repository'] else 'not ready'})")
    say(f"Private-library managed skills: {len(data['skills'])}")
    say(f"Source inventory records: {len(data['sources'])}")
    if machine:
        say(f"Current machine: {machine['label']} ({machine['id']})")
    else:
        say("Current machine: not registered")
    if fleet is None:
        say("Fleet inventory: not configured")
    elif fleet_entry:
        say(f"Fleet registration: ready ({len(fleet['machines'])} machines)")
    else:
        say(f"Fleet registration: current machine is not registered ({len(fleet['machines'])} machines)")
    say(f"Uncommitted changes: {len(data['changes'] or [])}")
    if data["pending_skill_changes"]:
        say(f"Pending private skill sync: {len(data['pending_skill_changes'])}")
        for name in data["pending_skill_changes"]:
            say(f"- {name}")
    elif data["changes"]:
        say(f"Pending private-library sync changes: {len(data['changes'])}")
    if data["remote"]["state"] == "update-available":
        say(f"Remote private-library updates: {data['remote']['behind']} commit(s) available")
        if data["remote"]["skill_changes"]:
            say(f"Remote private skill sync: {len(data['remote']['skill_changes'])}")
            for name in data["remote"]["skill_changes"]:
                say(f"- {name}")
        else:
            say("Remote update contains library metadata but no skill file changes.")
    elif data["remote"]["state"] in {"local-ahead", "diverged"}:
        say(
            f"Remote history state: {data['remote']['state']} "
            f"(ahead {data['remote']['ahead']}, behind {data['remote']['behind']})"
        )
    elif data["remote"]["state"] in {"remote-unreachable", "remote-unverified"}:
        say(f"Remote private-library status: {data['remote']['state']} ({data['remote']['error']})")
    if data["pending_sync"]:
        say("Sync preview: review and approve the proposed private-library fast-forward or publish before applying.")
    elif data["remote"]["state"] == "current":
        say("Pending private skill sync: 0")


def command_machine_status(args: argparse.Namespace) -> None:
    state_path = Path(args.state_file).expanduser()
    state = load_state(state_path)
    machine = local_machine(state)
    library_dir = Path(state["library_dir"]).expanduser() if state.get("library_dir") else None
    fleet_path = library_dir / FLEET_NAME if library_dir else None
    fleet = load_fleet(library_dir, required=True) if fleet_path and fleet_path.exists() else None
    fleet_machines = (fleet or {}).get("machines", [])
    fleet_entry = next(
        (item for item in fleet_machines if machine and item["id"] == machine["id"]),
        None,
    )
    data = {
        "machine": machine,
        "fleet": {
            "configured": fleet is not None,
            "registered": fleet_entry is not None,
            "machines": len(fleet_machines),
        },
    }
    if args.json:
        print(json.dumps(data, indent=2))
        return
    if not machine:
        say("Current machine is not registered. Use bootstrap for the first machine or join for another machine.")
        return
    say(f"Current machine: {machine['label']} ({machine['id']})")
    say(f"Local skill target: {machine['target_root']}")
    if fleet is None:
        say("Fleet registration: not configured")
    elif fleet_entry:
        say(f"Fleet registration: ready ({len(fleet['machines'])} machines)")
    else:
        say(f"Fleet registration: this machine is missing ({len(fleet['machines'])} machines)")


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
        validate_skill_links(skill)
        target = target_root / skill.name
        if target.exists() or target.is_symlink():
            if target.is_symlink() and target.resolve() == skill.resolve():
                already_linked += 1
                continue
            fail(f"Restore stopped because target already exists: {target}")
        create.append((skill, target))
    return create, already_linked


def command_restore(args: argparse.Namespace) -> None:
    library_dir = Path(args.library_dir).expanduser().resolve()
    state_path = Path(args.state_file).expanduser()
    state = load_state(state_path)
    machine = local_machine(state)
    target_root = resolved_target_root(args, state)
    say(f"Plan: clone private repository {args.repo} into {library_dir} if needed")
    if library_dir.exists():
        if (library_dir / FLEET_NAME).exists():
            machine = require_registered_machine(state, library_dir, action="restoring skills")
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
    assert_checkout_matches_repository(library_dir, args.repo)
    if (library_dir / FLEET_NAME).exists():
        machine = require_registered_machine(state, library_dir, action="restoring skills")
    create, already_linked = restore_actions(library_dir, target_root)
    source_count = len(source_inventory(library_dir))
    target_root.mkdir(parents=True, exist_ok=True)
    for skill, target in create:
        target.symlink_to(skill, target_is_directory=True)
    if machine:
        save_state(args.repo, library_dir, state_path, target_root=target_root)
    else:
        save_state(args.repo, library_dir, state_path)
    say(f"Restore completed: created {len(create)} links; {already_linked} were already correct.")
    say(f"Source-managed skills to reconcile through their official sources: {source_count}.")


def command_doctor(args: argparse.Namespace) -> None:
    checks: list[tuple[str, bool, str]] = []
    for tool in ("git", "gh"):
        path = shutil.which(tool)
        checks.append((tool, bool(path), path or "not found"))
    network_ok, network_detail = github_network_check()
    checks.append(("github-network", network_ok, network_detail))
    gh_ok = False
    if shutil.which("gh"):
        if network_ok:
            gh_ok, detail = github_auth_check()
            if detail == "not authenticated":
                detail = "not authenticated; run: gh auth login -h github.com"
        else:
            detail = "not checked because GitHub network is unavailable"
        checks.append(("github-login", gh_ok, detail))
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
    elif status["state"] == "remote-unverified":
        say(status["error"] or "Remote version is unverified.")
    elif status["state"] == "remote-unreachable":
        say(status["error"] or "Remote version could not be checked.")


def command_update_manager(args: argparse.Namespace) -> None:
    repo_dir = Path(args.manager_dir).expanduser()
    status = manager_update_status(repo_dir)
    if status["state"] == "current":
        say("Manager is already current. Private skills were not modified.")
        return
    if status["state"] in {"remote-unverified", "remote-unreachable"}:
        fail(f"Manager update stopped because remote status is {status['state']}: {status.get('error', 'version is unverified')}")
    if status["state"] != "update-available":
        fail(f"Manager update stopped because repository state is {status['state']}")
    say(f"Plan: fast-forward manager at {repo_dir} to origin/{status['branch']}")
    say("Private skill library will not be touched.")
    if not args.apply:
        say("Preview only. Re-run with --apply to update.")
        return
    if worktree_dirty(repo_dir):
        fail("Manager has local changes. Commit or review them before updating.")
    git(repo_dir, "merge", "--ff-only", status["remote_ref"])
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

    bootstrap = sub.add_parser("bootstrap", parents=[common], help="Create a private library and register this first machine")
    bootstrap.add_argument("--repo", required=True, help="Private GitHub repository as OWNER/NAME")
    bootstrap.add_argument("--label", required=True, help="Stable lowercase machine label")
    bootstrap.add_argument("--role", action="append", default=[], help="Optional lowercase machine role; repeatable")
    bootstrap.add_argument("--machine-id", help="Optional UUID to preserve a reviewed machine identity")
    bootstrap.add_argument("--library-dir", default=str(Path.home() / ".local" / "share" / "my-skills"))
    bootstrap.add_argument("--target", help="Local skill target root; defaults to the Codex skill root")
    bootstrap.add_argument("--apply", action="store_true")
    bootstrap.set_defaults(func=command_bootstrap)

    join = sub.add_parser("join", parents=[common], help="Join another machine to an existing private library")
    join.add_argument("--repo", required=True, help="Existing private GitHub repository as OWNER/NAME")
    join.add_argument("--label", required=True, help="Stable lowercase machine label")
    join.add_argument("--role", action="append", default=[], help="Optional lowercase machine role; repeatable")
    join.add_argument("--machine-id", help="Optional UUID to preserve a reviewed machine identity")
    join.add_argument("--library-dir", default=str(Path.home() / ".local" / "share" / "my-skills"))
    join.add_argument("--target", help="Local skill target root; defaults to the Codex skill root")
    join.add_argument("--apply", action="store_true")
    join.set_defaults(func=command_join)

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

    adopt = sub.add_parser("adopt", parents=[common], help="Move one local personal skill under managed backup and linking")
    adopt.add_argument("source")
    adopt.add_argument("--name")
    adopt.add_argument("--target", help="Managed skill root; defaults to registered local target")
    adopt.add_argument("--repo")
    adopt.add_argument("--library-dir")
    adopt.add_argument("--apply", action="store_true")
    adopt.set_defaults(func=command_adopt)

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

    machine_status = sub.add_parser("machine-status", parents=[common], help="Show this machine's local fleet registration")
    machine_status.add_argument("--json", action="store_true")
    machine_status.set_defaults(func=command_machine_status)

    restore = sub.add_parser("restore", parents=[common], help="Restore private skills on a new machine")
    restore.add_argument("--repo", required=True)
    restore.add_argument("--library-dir", default=str(Path.home() / ".local" / "share" / "my-skills"))
    restore.add_argument("--target", help="Local skill target root; defaults to registered local target")
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
