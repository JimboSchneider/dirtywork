# localagent

Runs one coding task against a local LM Studio model in an agentic tool-use
loop, inside an isolated git worktree. Built to be driven by Claude Code;
humans watch with `tail -f`.

## Install

    chmod +x bin/localagent
    ln -sf ~/repos/localagent/bin/localagent ~/.local/bin/localagent

## Use

    localagent run --repo ~/repos/someproject "Add a unit test for X"

Watch a run: `tail -f` the transcript path printed on stderr.
Review a run: `git -C <worktree> diff`, then commit or discard.
Discard a run: `git -C <repo> worktree remove --force <worktree> &&
git -C <repo> branch -D localagent/<slug>`

Design: docs/superpowers/specs/2026-08-13-localagent-design.md
