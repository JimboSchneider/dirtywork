# dirtywork — working rules for Claude

## Dogfood rule: dirtywork builds dirtywork

Every code change to this repo (roadmap items, features, fixes) is implemented
by running the **latest released** dirtywork from PyPI against this checkout,
with a **local worker model**. Claude's job is planner, brief-writer and
reviewer — not implementer. Do not have Claude or Claude subagents edit the
code directly by default.

Why: the product's proof is its receipts — scoreboard rows, ledger entries,
blog posts, metrics — and those only exist when dirtywork does the work.
Claude-implemented changes produce none of that.

### Invocation

Run the release, not the checkout (`pipx run --spec`, not a stale pipx
install; check the current PyPI version first):

```
pipx run --spec 'dirtywork==<latest>' dirtywork run "<task>" \
  --repo /Users/jimschneider/repos/dirtywork \
  --model qwen/qwen3-coder-next --sandbox docker \
  --image dirtywork-worker-pytest:<X.Y> \
  --verify "python3 -m pytest -q -p no:cacheprovider" --verify-rounds 2 \
  --max-turns 60 --timeout 1800
```

- Worker: `qwen/qwen3-coder-next` via LM Studio (`http://localhost:1234/v1`);
  `mistralai/devstral-small-2-2512` as the second worker for fan-out.
- Image: the maintained worker image ships without pytest. Build a derived
  image (`FROM ghcr.io/jimboschneider/dirtywork-worker:<X.Y>` +
  `python3-pytest`) per the "Derived images" section of `docker/README.md`,
  and point `--image` at it.
- Task text: short and explicit — name the files, strings and tests; weave
  in prior rulings. The worker writes code + tests; Claude writes docs after.

### Process around a run

1. Spec → plan (`docs/superpowers/specs/`, `docs/superpowers/plans/`),
   worker briefs written verbatim into the plan.
2. Metrics sampler on for every run (`tools/soak_sampler.sh`); a per-run row
   (status, turns, wall, tokens, tok/s, nudges, verdict) goes into the ledger
   under `docs/superpowers/bench/`.
3. Claude reviews the produced `dirtywork/<slug>` branch and runs the full
   suite on the host before opening the PR.
4. Fall back to a Claude implementer only after the worker fails a
   resume-with-feedback — and say so explicitly in the PR.

### Approvals

Never merge a PR or cut a release without an explicit per-action go-ahead.
After any `docs/` merge, verify the Pages build succeeded and the sitemap
URLs return 200 before calling a post live (Pages is not a PR check).
