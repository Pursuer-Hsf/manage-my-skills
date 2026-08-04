# GitHub onboarding

Use this flow for a user with no GitHub knowledge. The public manager must already be available to the Agent; it remains separate from the user's private library.

## First managed machine

1. Run `doctor` and `scan`, then explain each `ACTION` line and the initial skill classification.
2. If `gh` is missing, install GitHub CLI using the platform's normal package manager after permission.
3. If login is missing or expired, run `gh auth login -h github.com`. Tell the user exactly when to approve the browser page, device code, MFA, or organization access. Never request their password or token in chat.
4. Ask for the desired private repository name and a simple label for the current machine. A conventional repository choice is `OWNER/my-skills`; labels should be generic, lowercase, and non-sensitive.
5. Preview `bootstrap` with the repository, local checkout, local skill target, label, and optional roles. Preserve the previewed random machine UUID for the matching apply command. Explain that it creates the private library and registers only this machine; it does not import or replace skills yet.
6. After approval, add `--apply`. The command creates the GitHub repository with `--private` when absent, queries GitHub again, and refuses to continue unless `isPrivate` is true.
7. Run `status` and `machine-status`. Explain the private library, the local state file, and that the fleet inventory contains only safe machine labels and roles.
8. Review the scanned skills one by one. Use `adopt` for an owned skill already inside the managed target root; it preserves a backup before replacing that directory with a link. Use `import` only when the user wants a private copy without changing the existing location. Track public skills by source only.

## Another machine

1. Install or load the public manager on that machine, then run `doctor` and `scan` there.
2. Complete GitHub authentication on that machine. Credentials are not shared from another machine.
3. Ask for the existing private repository, a new safe machine label, optional roles, and the local skill target.
4. Preview `join`; it clones or verifies the private library, creates a new random local machine UUID, and adds only safe identity fields to `fleet.json`. Preserve the previewed UUID for the matching apply command.
5. After approval, run `join --apply`, then preview `restore` separately. Restore must stop if any target already exists.
6. Reconcile public skills from their recorded official sources after a separate preview and approval.

Machines do not need SSH access to one another. Each one connects independently to GitHub and reconciles its own state. Do not promise central live status unless a direct connection or separate reporting channel is explicitly configured.

## Existing private libraries

If the user already has a private repository and local checkout, use `connect` only when they want to save that local connection without changing repository files. Use `join` after inspection when they want to register the current machine in a fleet inventory. Never make an existing private repository public.

The local state file defaults to `~/.config/manage-my-skills/state.json`. It may contain only schema version, repository identifier, local checkout path, machine UUID, machine label, local target path, and timestamps. Authentication stays with `gh`.
