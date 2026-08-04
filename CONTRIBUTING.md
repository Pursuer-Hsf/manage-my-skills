# Contributing

`manage-my-skills` helps people keep their own Agent Skills private, reviewable, and consistent across machines. Small, focused contributions are welcome.

## Before You Start

- Search existing issues and discussions before opening a new one.
- Never include personal skills, credentials, internal hostnames, private repositories, or machine paths in issues, fixtures, screenshots, or pull requests.
- Discuss changes to safety behavior before implementing them. Preview-by-default, private-repository verification, allowlisted staging, adoption backup, fleet privacy, and no-overwrite restore behavior are project invariants.

## Good Contributions

- A reproducible bug report with sanitized inputs.
- A narrowly scoped compatibility improvement.
- Documentation that makes first-time setup or recovery clearer.
- Tests for a safety boundary or regression.

## Development

Run the project checks before opening a pull request:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile skills/manage-my-skills/scripts/manage_my_skills.py
```

Keep pull requests small, explain the user-facing effect, and include tests when behavior changes.

## Security

Report potential exposure of private skill content or credentials through the process in [SECURITY.md](SECURITY.md), not in a public issue.
