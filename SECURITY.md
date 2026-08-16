# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub security advisories](https://github.com/JimboSchneider/dirtywork/security/advisories/new)
rather than public issues. You should receive a response within a week.

## Scope worth knowing

As of 0.3, **Docker mode is the default** and is a real containment
boundary: `--network none`, `--read-only` root filesystem,
`--cap-drop ALL`, kernel-enforced memory/CPU/process-count/per-file-size
limits, no host path mounted in except a read-only copy of the parent
repository's git object store, and a validated tar export as the only path
from the worker's tree back to the host. Escapes from docker mode — a
container breakout, a way to write outside the run's worktree or
`~/.dirtywork/runs/<slug>/`, a way to reach the network, a way for the
export validator to write through a symlink or a `.git`-named path — are
in scope and taken seriously.

**Known, accepted exposures in docker mode** (see README's Security &
trust section for the full list): the worker can read the *entire* parent
git object store (all branches, unreachable objects — not a
confidentiality boundary); total disk is a best-effort sampled bound, not
a kernel quota; escaping symlinks are created (not followed) inside the
worktree and reported; host git commands the *operator* runs afterward on
the exported tree use the operator's own config and can trigger a
worker-authored `.gitattributes`' configured filter.

**`--sandbox none`** keeps 0.2's guardrail-only behavior and its caveats
unchanged: file tools are path-confined (symlink-safe), but `bash` is a
general shell gated only by a best-effort regex denylist plus a `HOME`
redirected into the worktree — not confined. A determined or
prompt-injected model can still read absolute host paths. Do not treat
`--sandbox none` as a sandbox; it exists for operators who cannot or do
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
