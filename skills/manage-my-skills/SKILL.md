---
name: manage-my-skills
description: Automatically detect when reusable workflows should become new or updated personal skills, safely manage personal and source-installed open-source agent skills across multiple machines, and keep this manager current. Use when organizing, installing, updating, backing up, synchronizing, restoring, or diagnosing skills; managing a private skill library; or preserving a reusable workflow from the current task.
---

# Manage My Skills

Manage the public manager and the user's private skill library as two independent repositories. Never place private skills, credentials, machine configuration, or personal inventory in this public repository.

## Start Every Workflow

1. Locate this skill's `scripts/manage_my_skills.py`.
2. Run `manager-status` once. If a fast-forward update is available, report it and offer to update the public manager before changing managed skills. After an update, re-read this `SKILL.md` before continuing.
3. Run `doctor` and `scan` before proposing managed-skill changes.
4. Explain the result in plain language. Do not assume the user knows Git or GitHub.
5. Preview mutating commands first. Use `--apply` only after the user approves the stated files, repository, and effects.

For a capture-only suggestion, do not interrupt the current task with a network check. Run the manager check after the user accepts the suggestion and before changing skill files.
If the update check cannot reach the remote, report that the version is unverified and continue only with work that does not depend on a manager update. Never describe a failed check as current.

## Choose The Workflow

- Inventory or classification: run `scan`; consult [classification.md](references/classification.md).
- First private backup: follow [github-onboarding.md](references/github-onboarding.md), then run `setup`.
- Existing private backup: preview and run `connect`; it must verify privacy and write only local state, never repository files.
- Add a user-owned skill: run `import` without `--apply`, show the destination, then apply. Run `sync` separately.
- Track an open-source, marketplace, or plugin skill: confirm its canonical public source, then preview and run `track-source`. Never copy it into the private `skills/` directory.
- Reconcile source-managed skills: run `sources`; compare the desired source, path, and ref with the current machine, then use the source's official installer or updater after preview and approval.
- Check backup state: run `status`; translate Git output into ordinary language.
- Publish private changes: run `sync` without `--apply`, review the file list, then apply.
- Restore a machine: run `restore` without `--apply`, verify every target, then apply. Reconcile every source-managed skill separately through its official source.
- Diagnose failure: run `doctor`; ask the user only for browser login, MFA, or permission approval that the Agent cannot complete.
- Update this manager: run `manager-status`, then preview and run `update-manager` only when the state is `update-available`. Do not invoke private-library synchronization as part of the update.
- Manage several machines: check and update the public manager independently on each machine before reconciling personal and source-managed skills.

## Suggest Reusable Skill Capture Or Update

When the current task produces a clearly reusable, user-owned workflow:

1. Compare it with existing personal skills.
2. If it adds durable value to an existing skill, offer once to update that skill. Otherwise, offer once to create a new skill.
3. Keep the prompt short: name the reusable value and ask whether to prepare a sanitized preview.
4. Do not create, update, import, or synchronize anything until the user agrees.
5. After agreement, use an appropriate skill-authoring workflow, remove machine-specific or sensitive details, and show the proposed content.
6. Import and synchronize only after separate approval.

Do not suggest capture for trivial one-off work, third-party instructions, or content whose main value depends on credentials, private identifiers, or confidential data.

## Non-Negotiable Safety Rules

Read [safety.md](references/safety.md) before any setup, import, sync, or restore operation.

- Keep preview mode as the default and require explicit `--apply` for mutation.
- Verify the backup repository is private through GitHub before copying or pushing skills.
- Let `gh` or the operating system credential manager own authentication. Never store tokens in project files or state JSON.
- Treat only user-authored skills as private-backup candidates. Track third-party, bundled, marketplace, and plugin skills by canonical source and reinstall or update them through their official mechanism.
- Never store executable installation commands, credentials, or machine-local paths in the portable source inventory.
- Stop on secret-scan findings, existing restore targets, non-fast-forward history, conflicts, or staged paths outside `library.json` and `skills/`.
- Never run `git add -A`, force-push, rewrite history, silently resolve conflicts, or delete/overwrite a skill.
- Update this public manager only by fast-forward. Stop on a dirty worktree, local-ahead history, divergence, or detached HEAD.
- Do not use ignored directories as private storage. Ignored files do not synchronize and can be removed by cleanup commands such as `git clean -xfd`.

## Support Boundary

Automatic discovery is Codex-first and also checks common shared skill locations. Manager update checks run when this management workflow is invoked, not as a background daemon. Personal skills synchronize as private content; open-source skills synchronize as a portable desired-source inventory and are reconciled on each machine through their official installer. Other Agents can use this repository with filesystem, Git, GitHub, and network access, but automatic invocation differs by platform.
