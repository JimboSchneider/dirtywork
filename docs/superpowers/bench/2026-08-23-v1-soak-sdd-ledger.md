# SDD ledger — v1 soak (#48)
Matrix: docs/superpowers/bench/2026-08-23-v1-soak-matrix.md (owner-approved decisions 17:50). Branch: v1-soak-48 off main @ 82a353e (0.10.1). Scoreboard: 2026-08-23-v1-soak-worker-scoreboard.md. Raw rows: ~/.dirtywork/bench/soak-{A,Aprime,B,C,D}.jsonl; sampler: ~/.dirtywork/bench/soak-sampler.csv (5 s).

## Pre-flight scan (2026-08-23 17:40–18:04 CDT)
| Item | Found | Action |
|---|---|---|
| LM Studio | only qwen resident, PARALLEL 1 (documented steady state: both models, PARALLEL 4) | reloaded qwen `-c 65536 --parallel 4`; loaded devstral `--parallel 4` |
| Devstral context | `lms load -c 32768` / `--context-length=32768` both ignored: `/api/v0/models` reports `loaded_context_length` 325120 (max 393216) — LM Studio's per-model config wins over the CLI flag | left as-is; harness server-window discovery will see 325k for devstral. **Deviation from CLAUDE.md's 32,768.** RSS stayed 15 GB (KV allocated lazily) |
| Ollama | `qwen3.6` resident with 262144 ctx, llama-server RSS 26.8 GB; free RAM fell to ~150 MB with all three loaded | unloaded (`keep_alive: 0`) until leg C; free RAM back to ~30 GB |
| Worker image | `:0.10` local, RepoDigest == `PINNED_DIGEST` (sha256:4fc400ca…) | — |
| `dirtywork bench` flags | no pass-through for `--verify/--max-tokens/--stuck-repeats/--stall-turns` | tools/soak_driver.py drives `dirtywork run` for legs A′/B/D |
| Existing bench tasks | none can fire F1/F2/F3/F5 | 4 provokers added (bench/repos/); review found 3 model-workarounds (generator script, stub `httpx.py`/test edit, hang announced in task text) → hardened, bypasses verified failing in the real acceptance container |
| invoicr (leg D) | image ships .NET SDK 8.0.424 + empty NuGet cache; invoicr targets net10.0; `network none` blocks restore | owner ruling: custom image `dirtywork-worker-net10:0.10` FROM the pinned digest + dotnet-install 10.0 (+231 MB, 23 s build; invoicr `dotnet test` 33/33 in 9.6 s) and `--allow-network`. Follow-up for 1.0: bump the maintained image to .NET 10 + re-pin (not filed yet — owner to confirm) |
| invoicr #94 text | stale vs repo: test project + CI `dotnet test` already exist; migrations now timestamp-prefixed | task file carries a "current repo state" note; code wins over plan where they conflict |
| Scripts | `/opt/homebrew/bin/python3` (3.14) lacks pytest + editable dirtywork; `/usr/bin/python3` has both | all soak commands use `/usr/bin/python3` |

## Legs
Leg A: `dirtywork bench` 3 tasks × {qwen, devstral} × 2 — started 18:04:23; net10 image build (18:05–18:06) overlapped runs 1–3 (download-bound; noted for sampler reads). Result 18:07: 12/12 completed, acceptance 12/12, gamed 0; qwen mean 9.8 s / 17.9k tok, devstral mean 26.3 s / 28.8k tok.
Leg A′: driver, `--verify <acceptance> --verify-rounds 2` — started 18:07 inside a 10-min tool window (mistake: leg B would have been cut off mid-run); stopped after 3 rows, no orphan process/container, relaunched detached 18:11 (resumable-by-label skipped the 3 done rows).
Leg B: queued behind A′ in the same detached chain (18 rows).
