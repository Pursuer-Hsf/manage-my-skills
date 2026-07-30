# Safety model

## Trust boundaries

- Public manager: code, generic documentation, tests, and sanitized examples only.
- Private library: user-owned skills plus a portable source inventory in `library.json`.
- Local state: machine-specific paths and repository identifier; no credentials.
- GitHub authentication: owned by GitHub CLI and the operating system credential store.

## Before copying or pushing

1. Show the exact source, destination, repository, and mode.
2. Confirm the skill is user-owned and appropriate to copy.
3. Scan text files for private keys, access tokens, access keys, and assigned secrets.
4. Query GitHub and require `isPrivate: true`.
5. Restrict staged paths to `library.json` and `skills/`.

Open-source and provider-managed skills stay under their original update authority. Store only portable source metadata in `library.json`; reject credentials, embedded installation commands, and machine-local paths.

Secret scanning is a guardrail, not proof of safety. The Agent must also review hostnames, IP addresses, usernames, internal URLs, proprietary procedures, and personal identifiers before upload.

## Stop conditions

Stop without mutation when any of these occurs:

- Existing destination would be overwritten.
- Repository visibility is not private or cannot be verified.
- Secret-like content is detected.
- Local and remote Git histories diverge.
- A merge, rebase, force push, deletion, or conflict resolution would be required.
- Staged content escapes the private-library allowlist.

Create a separate backup or quarantine copy before any later workflow that intentionally replaces or removes data.
