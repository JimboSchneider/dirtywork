# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub security advisories](https://github.com/JimboSchneider/dirtywork/security/advisories/new)
rather than public issues. You should receive a response within a week.

## Scope worth knowing

dirtywork's guardrails are designed to stop **accidents by a confused local
model**, not a determined adversary — the README says this plainly, and reports
that a malicious model or prompt can escape the worktree are appreciated but
expected to exist by design. The post-run human review is the actual security
boundary.

Concretely, the containment is uneven by design:

- **File tools are confined** to the worktree by real path resolution (symlink,
  `..`, and absolute-path escapes are rejected).
- **`bash` is a general shell and is NOT confined** — it is gated only by a
  best-effort regex denylist plus a `HOME` redirected into the worktree (so
  `~`/`$HOME` can't reach `~/.ssh`/`~/.aws`). A determined or prompt-injected
  model can still read absolute host paths. Do not treat `bash` as a sandbox.

True per-run isolation (OS sandbox / container rooted at the worktree) is the
real fix and is tracked as future work; until then, run dirtywork only against
models and repositories you'd trust with shell access.

Reports that DO qualify: guardrail bypasses reachable by a *well-intentioned*
model (accident-grade escapes), anything that lets a run touch the parent
checkout or leak the caller's environment secrets, and violations of the
documented machine contract that could mislead an orchestrating agent.

## Supported versions

Only the latest release is supported.
