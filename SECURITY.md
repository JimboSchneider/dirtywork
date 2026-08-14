# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub security advisories](https://github.com/JimboSchneider/localagent/security/advisories/new)
rather than public issues. You should receive a response within a week.

## Scope worth knowing

localagent's guardrails (path confinement, bash denylist, environment
scrubbing) are designed to stop **accidents by a confused local model**, not a
determined adversary — the README says this plainly, and reports that a
malicious model or prompt can escape the worktree are appreciated but expected
to exist by design. The post-run human review is the actual security boundary.

Reports that DO qualify: guardrail bypasses reachable by a *well-intentioned*
model (accident-grade escapes), anything that lets a run touch the parent
checkout or leak the caller's environment secrets, and violations of the
documented machine contract that could mislead an orchestrating agent.

## Supported versions

Only the latest release is supported.
