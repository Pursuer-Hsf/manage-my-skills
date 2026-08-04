# Security policy

## Reporting

Open a GitHub security advisory for vulnerabilities that could expose private skill content, credentials, or repository data. Do not include real secrets or private skills in a public issue.

## Guarantees and limits

The manager blocks common credential patterns, verifies GitHub repository privacy, and confirms an existing checkout's `origin` matches that repository before setup, bootstrap, join, import, adoption, restore, or sync. Pattern scanning cannot prove that content is safe. Users and Agents must still review personal identifiers, internal infrastructure, proprietary procedures, and licensing before upload.

The optional private fleet inventory is limited to random machine IDs, safe labels, roles, and enabled state. It must not contain hostnames, IP addresses, SSH aliases, usernames, local paths, credentials, or live machine reports. Local machine state and authentication remain local to each machine.

The project intentionally does not store GitHub credentials, run background synchronization, resolve Git conflicts, overwrite restored skills, force-push, make an existing repository public, or require machines to reach each other by SSH. Adoption preserves a local backup before linking a personal skill to its private-library copy.

The public manager checks its configured Git remote only when invoked. Manager updates require approval, use fast-forward only, and stop for local changes, detached HEAD, local-ahead history, or divergence. Updating the manager does not synchronize the private skill library.
