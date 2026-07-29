# GitHub onboarding

Use this flow for a user with no GitHub knowledge.

1. Run `doctor` and explain each `ACTION` line.
2. If `gh` is missing, install GitHub CLI using the platform's normal package manager after permission.
3. If login is missing or expired, run `gh auth login -h github.com`. Tell the user exactly when to approve the browser page, device code, MFA, or organization access. Never request their password or token in chat.
4. Ask for the desired private repository name. A conventional choice is `OWNER/my-skills`.
5. Preview `setup --repo OWNER/NAME --library-dir PATH`.
6. After approval, add `--apply`. The command creates the GitHub repository with `--private` when absent, queries GitHub again, and refuses to continue unless `isPrivate` is true.
7. Run `status` and explain where the local library and remote backup live.

The local state file defaults to `~/.config/manage-my-skills/state.json`. It may contain only schema version, repository identifier, local path, and timestamps. Authentication stays with `gh`.

Do not make the user's existing skill repository public. Create a new public manager repository and preserve private history separately.
