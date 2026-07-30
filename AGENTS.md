# Agent instructions

This repository contains a public skill manager, never the user's private skills.

- Read `skills/manage-my-skills/SKILL.md` before changing behavior.
- Preserve preview-by-default semantics and require `--apply` for mutation.
- Keep GitHub credentials out of files, logs, fixtures, and examples.
- Never weaken private-repository verification, secret scanning, staged-path allowlisting, or no-overwrite restore behavior.
- Keep manager self-updates explicit, previewed, and fast-forward-only. Never couple them to private-library synchronization.
- Use only sanitized fixtures. Apart from this public repository's owner and URL, do not add real usernames, home paths, private repository owners, hosts, IP addresses, internal URLs, or tokens.
- Keep the CLI standard-library-only unless a dependency removes substantial risk and is justified.
- Keep `README.md`, `docs/README.zh-CN.md`, and `docs/README.ja.md` aligned when user-facing workflows, commands, requirements, or safety guarantees change.
- Run unit tests, skill validation, and the repository sensitive-string audit before publishing.
