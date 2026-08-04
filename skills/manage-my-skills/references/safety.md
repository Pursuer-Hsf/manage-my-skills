# Safety model

## Trust boundaries

- Public manager: code, generic documentation, tests, and sanitized examples only.
- Private library: user-owned skills, a portable source inventory in `library.json`, and an optional fleet inventory in `fleet.json`.
- Fleet inventory: only random machine UUIDs, safe labels, roles, and enabled state. It must not contain hostnames, IP addresses, SSH aliases, usernames, paths, credentials, or live status.
- Local state: repository identifier, local checkout path, random machine UUID, safe label, and local skill target; no credentials.
- GitHub authentication: owned by GitHub CLI and the operating system credential store.

## Before copying or pushing

1. Show the exact source, destination, backup, repository, and mode.
2. Confirm the skill is user-owned and appropriate to copy.
3. Scan text files for private keys, access tokens, access keys, and assigned secrets.
4. Query GitHub and require `isPrivate: true`, then confirm the local checkout's `origin` matches that repository.
5. Validate `fleet.json` when present and restrict staged paths to `library.json`, `fleet.json`, and `skills/`.

Open-source and provider-managed skills stay under their original update authority. Store only portable source metadata in `library.json`; reject credentials, embedded installation commands, and machine-local paths.

For `adopt`, copy and verify the private skill before moving the original directory to a local backup. Do not delete the backup and do not replace an existing link target.

Secret scanning is a guardrail, not proof of safety. The Agent must also review internal hosts, personal identifiers, proprietary procedures, licenses, and confidential data before upload.

## Stop conditions

Stop without mutation when any of these occurs:

- Existing destination or backup would be overwritten.
- Repository visibility is not private or cannot be verified.
- The local checkout has no `origin` or its `origin` is not the verified private repository.
- Secret-like content is detected.
- Fleet identity is duplicate, incomplete, or conflicts with an existing safe label.
- Local and remote Git histories diverge.
- A merge, rebase, force push, deletion, or conflict resolution would be required.
- Staged content escapes the private-library allowlist.

Create a separate backup or quarantine copy before any later workflow that intentionally replaces or removes data.
