# Agent instructions

This repository contains a public skill manager, never the user's private skills.

- Read `skills/manage-my-skills/SKILL.md` before changing behavior.
- Preserve preview-by-default semantics and require `--apply` for mutation.
- Keep GitHub credentials out of files, logs, fixtures, and examples.
- Never weaken private-repository verification, secret scanning, staged-path allowlisting, or no-overwrite restore behavior.
- Use only sanitized fixtures. Do not add real usernames, home paths, repository owners, hosts, IP addresses, internal URLs, or tokens.
- Keep the CLI standard-library-only unless a dependency removes substantial risk and is justified.
- Run unit tests, skill validation, and the repository sensitive-string audit before publishing.
