---
name: manage-my-skills
description: Safely discover, classify, back up, synchronize, restore, and preserve user-owned agent skills in a separate private GitHub repository. Use when a user asks to organize local skills; create, connect, or diagnose a private skill library; import, synchronize, or restore personal skills; update this manager; or preserve a reusable user-owned workflow from the current task.
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
- Existing private backup: preview and run `connect`; it must verify privacy and write only local state, never repository files.
- Add a user-owned skill: run `import` without `--apply`, show the destination, then apply. Run `sync` separately.
- Check backup state: run `status`; translate Git output into ordinary language.
- Publish private changes: run `sync` without `--apply`, review the file list, then apply.
- Restore a machine: run `restore` without `--apply`, verify every target, then apply.
- Diagnose failure: run `doctor`; ask the user only for browser login, MFA, or permission approval that the Agent cannot complete.
- Update this manager: run `update-manager`; do not invoke private-library synchronization as part of the update.

## Suggest Reusable Skill Capture

When the current task produces a clearly reusable, user-owned workflow that is not already a skill:

1. Offer once to turn it into a personal skill.
2. Keep the prompt short: name the reusable value and ask whether to prepare a sanitized preview.
3. Do not create, import, or synchronize anything until the user agrees.
4. After agreement, use an appropriate skill-authoring workflow, remove machine-specific or sensitive details, and show the proposed content.
5. Import and synchronize only after separate approval.

Do not suggest capture for trivial one-off work, third-party instructions, or content whose main value depends on credentials, private identifiers, or confidential data.

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
