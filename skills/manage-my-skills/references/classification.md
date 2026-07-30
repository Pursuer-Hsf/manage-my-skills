# Skill classification

Classify by ownership and update authority, not merely by filesystem location.

| Class | Meaning | Private backup default |
| --- | --- | --- |
| Personal | Authored or intentionally maintained by the user | Yes |
| Shared local | User-controlled skill shared across Agents | Ask once, then yes if owned |
| Managed | Open-source, bundled, plugin, marketplace, or package-managed | No; track the canonical source and official update path |
| Unknown | Ownership or license cannot be established | No until reviewed |

Symlinks describe installation, not ownership. Resolve the link and classify the source. When duplicates exist, compare resolved paths and content before selecting one canonical copy.

The built-in scanner uses path-based hints and can be wrong. The Agent must confirm ownership before import.

For source-managed skills, record only portable source metadata: name, source kind, canonical source, optional path, and optional branch, tag, or commit. Do not store executable installation commands or machine-local paths.
