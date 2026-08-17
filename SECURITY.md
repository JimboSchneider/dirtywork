# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub security advisories](https://github.com/JimboSchneider/dirtywork/security/advisories/new)
rather than public issues. You should receive a response within a week.

## Scope worth knowing

As of 0.4, **Docker mode is the default** and is a real containment
boundary — see README's [Security & trust](README.md#security--trust)
section for the full containment description (network, filesystem,
capabilities, resource limits, the object-store mount, the validated
export). Escapes from docker mode — a container breakout, a way to write
outside the run's worktree or `~/.dirtywork/runs/<slug>/`, a way to reach
the network, a way for the export validator to write through a symlink or
a `.git`-named path — are in scope and taken seriously. Inside the
container, `bash` enforces only the mode-independent policy rules (no
`git push`/`sudo`/pipe-to-shell/system-control commands); the
host-filesystem and shared-repo rules from `--sandbox none` below are not
applied there, since the container is the boundary for everything else.

**Known, accepted exposures in docker mode** (see README's Security &
trust section for the full list): the worker can read the *entire* parent
git object store (all branches, unreachable objects — not a
confidentiality boundary); total disk is a best-effort sampled bound, not
a kernel quota; escaping symlinks are created (not followed) inside the
worktree and reported; host git commands the *operator* runs afterward on
the exported tree use the operator's own config and can trigger a
worker-authored `.gitattributes`' configured filter.

**`--sandbox none`** keeps 0.2's guardrail-only behavior and its caveats —
see README's [Safety model](README.md#safety-model) section for the full
description. It is not a sandbox; it exists for operators who cannot or do
not want to run Docker.

Reports that DO qualify: any docker-mode escape as described above,
guardrail bypasses reachable by a *well-intentioned* model in
`--sandbox none` mode (accident-grade escapes), anything that lets a run
touch the parent checkout's git state beyond dirtywork's own bookkeeping,
anything that lets a run reach the network in the default (`--network
none`) configuration, and violations of the documented machine contract
that could mislead an orchestrating agent.

## Supported versions

Only the latest release is supported.
