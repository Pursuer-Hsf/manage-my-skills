---
name: manage-my-skills
description: Safely discover, classify, back up, synchronize, and restore user-owned agent skills with a separate private GitHub repository. Use when a user asks an Agent to organize local skills, set up private skill backup, import or sync personal skills, restore them on a new machine, diagnose GitHub access, or update this public manager without changing the managed skills.
---

# Manage My Skills

Manage the public manager and the user's private skill library as two independent repositories. Never place private skills, credentials, machine configuration, or personal inventory in this public repository.

## Start Every Workflow

1. Locate this skill's `scripts/manage_my_skills.py`.
2. Run `python3 <script> doctor` and `python3 <script> scan` before proposing changes.
3. Explain the result in plain language. Do not assume the user knows Git or GitHub.
4. Preview mutating commands first. Use `--apply` only after the user approves the stated files, repository, and effects.

## Choose The Workflow

- Inventory or classification: run `scan`; consult [classification.md](references/classification.md).
- First private backup: follow [github-onboarding.md](references/github-onboarding.md), then run `setup`.
- Add a user-owned skill: run `import` without `--apply`, show the destination, then apply. Run `sync` separately.
- Check backup state: run `status`; translate Git output into ordinary language.
- Publish private changes: run `sync` without `--apply`, review the file list, then apply.
- Restore a machine: run `restore` without `--apply`, verify every target, then apply.
- Diagnose failure: run `doctor`; ask the user only for browser login, MFA, or permission approval that the Agent cannot complete.
- Update this manager: run `update-manager`; do not invoke private-library synchronization as part of the update.

## Non-Negotiable Safety Rules

Read [safety.md](references/safety.md) before any setup, import, sync, or restore operation.

- Keep preview mode as the default and require explicit `--apply` for mutation.
- Verify the backup repository is private through GitHub before copying or pushing skills.
- Let `gh` or the operating system credential manager own authentication. Never store tokens in project files or state JSON.
- Treat only user-authored skills as private-backup candidates. Record third-party, bundled, marketplace, and plugin skills as references unless their license and update model justify copying.
- Stop on secret-scan findings, existing restore targets, non-fast-forward history, conflicts, or staged paths outside `library.json` and `skills/`.
- Never run `git add -A`, force-push, rewrite history, silently resolve conflicts, or delete/overwrite a skill.
- Do not use ignored directories as private storage. Ignored files do not synchronize and can be removed by cleanup commands such as `git clean -xfd`.

## Support Boundary

Automatic discovery is Codex-first and also checks common shared skill locations. Other Agents can use this repository by reading this `SKILL.md` and running the Python CLI if they have filesystem, Git, GitHub, and network access. Do not promise automatic invocation on every Agent platform.
