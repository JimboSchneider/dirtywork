# Locally-Runnable LLMs for Agentic Coding — Research Report
Machine: Apple M5 Max, 40 cores, 128GB unified memory, macOS, LM Studio (OpenAI-compatible API, MLX/GGUF, native tool calling required).
Budget: model weights ≤~75GB (leave room for 64k+ ctx KV cache + a second smaller model). Prefer 4-6 bit quants.
Baseline: `qwen/qwen3-coder-next` (Qwen3-Coder, 80B total/3B active MoE, ~45-85GB by quant, 262k ctx, works well).
Research date: 2026-08-16. Compiled from 4 parallel web-research passes (WebSearch/WebFetch, primary sources preferred).

---

## Candidate details

### 1. Qwen3.6-35B-A3B (Qwen team)
- Params: 35B total / 3B active, MoE. Release: 2026-04-16. License: Apache 2.0.
  Source: [GitHub QwenLM/Qwen3.6](https://github.com/QwenLM/Qwen3.6) (accessed 2026-08-16).
- Benchmarks (self-reported, HF model card, accessed 2026-08-16): SWE-bench Verified 73.4%, SWE-bench Pro 49.5%. [huggingface.co/Qwen/Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B). No independent verification found.
- Tool calling: native, but KNOWN LM Studio bugs — tool calls emitted inside `<think>` tags, malformed streamed args, "Failed to parse tool call". Sources: [lmstudio-bug-tracker #2045](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/2045), [#1868](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1868), [HF discussion "Tool use failure [Fix Found]"](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/discussions/51) (all accessed 2026-08-16). Reported fixed via `preserve_thinking` setting + LM Studio ≥0.4.5.
- Context: 262,144 native (YaRN to ~1.01M).
- Memory (MLX, mlx-community, accessed 2026-08-16): 4-bit = 20.4GB ([mlx-community/Qwen3.6-35B-A3B-4bit](https://huggingface.co/mlx-community/Qwen3.6-35B-A3B-4bit)); 6-bit = 29.1GB ([mlx-community/Qwen3.6-35B-A3B-6bit](https://huggingface.co/mlx-community/Qwen3.6-35B-A3B-6bit)). Comfortably fits budget.
- Tok/s on Apple Silicon: 44 tok/s (M4 Max) / 52 tok/s (M5 Max) claimed by aggregator [llmcheck.net](https://llmcheck.net/blog/qwen-36-35b-a3b-mac-new-number-one/) — **UNCONFIRMED, low-confidence, not a primary source**.
- LM Studio ID: `qwen/qwen3.6-35b-a3b` ([lmstudio.ai/models/qwen/qwen3.6-35b-a3b](https://lmstudio.ai/models/qwen/qwen3.6-35b-a3b)).
- Weakness: young release (Apr 2026) with active bug-tracker churn on tool-calling/streaming; verify LM Studio ≥0.4.5 before production use.

### 2. qwen/qwen3-coder-next (baseline, re-verified)
- Params: 80B total / 3B active MoE, hybrid Gated-DeltaNet/Gated-Attention. Release: ~2026-02-03/04. License: Apache 2.0.
  Sources: [MarkTechPost](https://www.marktechpost.com/2026/02/03/qwen-team-releases-qwen3-coder-next-an-open-weight-language-model-designed-specifically-for-coding-agents-and-local-development/) (2026-02-03), [HF model card](https://huggingface.co/Qwen/Qwen3-Coder-Next) (accessed 2026-08-16).
- Benchmarks (self-reported, HF card): SWE-bench Verified 70.6%, SWE-bench Pro 44.3%, Terminal-Bench 2.0 36.2%. No independent verification found.
- Tool calling: native, no known LM Studio bug reports found (in contrast to the newer 3.6 line).
- Context: 262,144.
- Memory: GGUF Q4_K_M 48.4GB / Q5_K_M 56.7GB / Q6_K 65.5GB / Q8_0 84.8GB (HF); MLX 4-bit ~46GB per LM Studio catalog ([lmstudio.ai/models/qwen/qwen3-coder-next](https://lmstudio.ai/models/qwen/qwen3-coder-next)).
- Tok/s on Apple Silicon: no source found.
- Weakness: none specifically documented; current known-good baseline.

### 3. GLM-4.7-Flash (zai-org)
- Params: 31B total / 3B active MoE. Release: ~2026-01-20. License: MIT (assumed per zai-org convention; not independently re-confirmed this pass).
  Source: [MarkTechPost](https://www.marktechpost.com/2026/01/20/) (title-level, accessed 2026-08-16), HF card zai-org/GLM-4.7-Flash (accessed 2026-08-16).
- Benchmarks (self-reported, HF card): SWE-bench Verified 59.2%, LiveCodeBench v6 64.0%, GPQA-Diamond 75.2%, τ²-Bench 79.5%. No independent verification found.
- Tool calling: "trained for tool use" per LM Studio catalog ([lmstudio.ai/models/zai-org/glm-4.7-flash](https://lmstudio.ai/models/zai-org/glm-4.7-flash), accessed 2026-08-16); native GGUF/MLX-6bit/8bit listed. Some community-reported friction in IDEs/OpenHands resolved by removing custom sampling params (no exact date/source).
- Context: 131,072.
- Memory (MLX, mlx-community): 6-bit ≈15GB, 8-bit ≈21GB. Very light — can run alongside qwen3-coder-next simultaneously.
- Tok/s: "43 tok/s on Apple M5 laptop" claimed by an aggregator (curateclick.com-style site) — **UNCONFIRMED, no primary source verified**.
- Weakness: lower self-reported SWE-bench than qwen3-coder-next/Qwen3.6; less battle-tested tool-calling track record.

### 4. Seed-OSS-36B-Instruct (ByteDance)
- Params: 36B dense. Release date: not pinned down this pass (HF card accessed 2026-08-16). License: Apache 2.0.
  Source: [huggingface.co/ByteDance-Seed/Seed-OSS-36B-Instruct](https://huggingface.co/ByteDance-Seed/Seed-OSS-36B-Instruct).
- Benchmarks (self-reported, HF card): LiveCodeBench 67.4%, RULER (long-context) 94.6%. Card claims SOTA "on multiple benchmarks including SWE-Bench and issue resolution" but **no exact SWE-bench Verified number found** — flag as unverified/incomplete evidence.
- Tool calling: native support per LM Studio catalog ([lmstudio.ai/models/bytedance/seed-oss-36b](https://lmstudio.ai/models/bytedance/seed-oss-36b)), GGUF/MLX 4/5/6/8-bit. No LM Studio tool-calling bug reports found (also no confirmation of clean operation beyond the catalog listing itself — "no source found" either way on real-world reliability).
- Context: 131,072 (a 512K claim appears only in a secondary blog — **UNCONFIRMED**, not on the primary card).
- Memory: MLX 6-bit (lmstudio-community/Seed-OSS-36B-Instruct-MLX-6bit) = 29.4GB.
- Tok/s on Apple Silicon: no source found.
- Weakness: weakest benchmark evidence of the shortlist (no confirmed SWE-bench Verified number); "thinking budget" reasoning-length control could add latency in long tool-call loops if misconfigured.

### 5. MiniMax M2 (MiniMax AI) — thin fit
- Params: 230B total (229.9B) / 10B active (9.8B) MoE. Release: 2025-10-26 (M2); M2.1/M2.5/M2.7 followed through 2026. License: Modified MIT (attribution required above 100M MAU / $30M ARR).
  Source: [github.com/MiniMax-AI/MiniMax-M2](https://github.com/MiniMax-AI/MiniMax-M2).
- Benchmarks (self-reported): M2.1 74% SWE-bench Verified ([digitalapplied.com](https://www.digitalapplied.com/blog/minimax-m2-1-digital-employee-coding-guide)); M2.5 80.2% SWE-bench Verified; M2.7 78% SWE-bench Verified, 57.0% Terminal-Bench 2 ([marktechpost.com, 2026-04-12](https://www.marktechpost.com/2026/04/12/minimax-just-open-sourced-minimax-m2-7-a-self-evolving-agent-model-that-scores-56-22-on-swe-pro-and-57-0-on-terminal-bench-2/)). Independent (Artificial Analysis, accessed 2026-08-16): LiveCodeBench ~83%, competitive coding index. [artificialanalysis.ai](https://artificialanalysis.ai/models/comparisons/minimax-m2-vs-kimi-k2)
- Tool calling: native OpenAI-format; **explicitly supported in LM Studio as of v0.3.31** ([lmstudio.ai/blog/lmstudio-v0.3.31](https://lmstudio.ai/blog/lmstudio-v0.3.31)) — the best-documented LM Studio compatibility signal of any non-Qwen candidate.
- Context: 128,000.
- Memory: smallest practical quant (unsloth UD-IQ2_XXS, 2-bit) = **74GB** — clears the ≤75GB bar with essentially **zero headroom** for KV cache or a second model. Next step up (UD-IQ2_M) is 78.1GB, already over budget. [huggingface.co/unsloth/MiniMax-M2-GGUF](https://huggingface.co/unsloth/MiniMax-M2-GGUF)
- Tok/s: ~15-26 tok/s on 128GB+ machines at heavy quant per [rentamac.io](https://rentamac.io/minimax-mac-mini) (quality of source uncertain); no M5 Max-specific figure.
- Weakness: the 74-80% SWE-bench numbers are for M2.1/M2.5/M2.7, not necessarily the exact 2-bit-quantized original M2 build that fits the budget — real-world quality at 2-bit is unverified and quantization at this level is a genuine risk. MiniMax's own blog admits sliding-window-attention variants degraded on agentic/long-context tasks during development (hence the full-attention design). [huggingface.co/blog/MiniMax-AI/why-did-m2-end-up-as-a-full-attention-model](https://huggingface.co/blog/MiniMax-AI/why-did-m2-end-up-as-a-full-attention-model)

---

## Honorable mentions (fail a constraint)

### GLM-4.5-Air (zai-org) — fits budget, but broken tool calling in LM Studio
- Params: 106B total / 12B active MoE. Release: ~2025-08-08 (arXiv 2508.06471). License: MIT.
  [huggingface.co/zai-org/GLM-4.5-Air](https://huggingface.co/zai-org/GLM-4.5-Air) (accessed 2026-08-16).
- Benchmarks (self-reported, arXiv 2508.06471, 2025-08): SWE-bench Verified 57.6% (full GLM-4.5: 64.2%). No independent verification found.
- Context: 128,000. [docs.z.ai/guides/llm/glm-4.5](https://docs.z.ai/guides/llm/glm-4.5) (accessed 2026-08-16).
- Memory: MLX 4-bit = 60.1GB ([mlx-community/GLM-4.5-Air-4bit](https://huggingface.co/mlx-community/GLM-4.5-Air-4bit), accessed 2026-08-16). Fits budget.
- Tool calling: **OPEN, unresolved LM Studio bug** — [lmstudio-bug-tracker #829](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/829), filed 2025-08-02, still open (last activity 2026-02-01, verified directly via `gh issue view`). Default Jinja template silently fails/garbles tool calls and web search. Workaround (community-reported): switch model's LM Studio prompt template from Jinja to ChatML.
- Excluded from top 5 because: lower benchmark score than qwen3-coder-next AND an unresolved native tool-calling bug in the exact harness this report targets.

### gpt-oss-120b (OpenAI) — fits budget, but known LM Studio tool-call parser issues
- Params: 117B total / 5.1B active MoE. Release: 2025-08-05. License: Apache 2.0. [arxiv.org/pdf/2508.10925](https://arxiv.org/pdf/2508.10925).
- Benchmarks (self-reported, model card): SWE-bench Verified 62.4%.
- Context: 128,000.
- Memory: native MXFP4 checkpoint ≈63GB (~4-bit equivalent) fits budget; 6-bit MLX builds (~95GB) do not.
- Tool calling: multiple **open** GitHub issues — [lmstudio-bug-tracker #876](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/876) "fails tool calls", [#873](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/873) "cannot produce a valid tool call by default", [#1105](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1105) "structured output doesn't work" (filed 2025-10-13), [lmstudio-js #493](https://github.com/lmstudio-ai/lmstudio-js/issues/493) "tool calling broken after recent update". Root cause: mismatch between gpt-oss's native "Harmony" response format and LM Studio's tool-call parser.
- Excluded from top 5 because: real, recurring, multi-issue tool-calling breakage in the exact harness this report targets, plus lower benchmark score than qwen3-coder-next.

### Mistral Small 4 (Mistral AI) — fits budget, but no disclosed agentic-coding benchmark
- Params: 119B total / 6B active (8B incl. embed/output) MoE. Release: 2026-03-16. License: Apache 2.0. [mistral.ai/news/mistral-small-4/](https://mistral.ai/news/mistral-small-4/) (2026-03-16).
- Context: 256,000.
- Mistral states it "unifies... Devstral for agentic coding... into a single, versatile model" and claims it "outperforms GPT-OSS 120B" on LiveCodeBench, but **discloses no SWE-bench Verified / Terminal-Bench / Aider score** — no source found for a hard agentic-coding number.
- Excluded from top 5 because: no benchmark evidence for the specific capability this report is ranking (agentic coding quality).

### Kimi K2 / DeepSeek V3.2+ / GLM-4.6+ / MiMo-V2.5-Pro — fail the memory-budget constraint outright
- Kimi K2 (1T total/32B active): smallest usable dynamic quant ~230-245GB (Unsloth 1.8-bit). Does not fit, not close. [x.com/UnslothAI/status/1944780685409165589](https://x.com/UnslothAI/status/1944780685409165589)
- DeepSeek V3.2 (671B/37B active): smallest quant (unsloth UD-TQ1_0, 1-bit) = 161GB. Independent SGLang reproduction of its SWE-bench claim scored only ~28% vs. 73.1% self-reported — large self-reported-vs-independent gap. [github.com/sgl-project/sglang/issues/17348](https://github.com/sgl-project/sglang/issues/17348)
- GLM-4.6 (357B/32B active) and GLM-5.x (744B-1T class): all ≥180GB even at 4-bit. Does not fit.
- Xiaomi MiMo-V2.5-Pro (1.02T/42B active): even 2-bit GGUF = 297GB. Strong self-reported/aggregator-sourced benchmarks (SWE-bench Verified 78.9%) but completely inactionable on this hardware.

---

## Ranked top 5 table

| Rank | Name | Size @ quant | Ctx | Tool-calling OK? | Key benchmark (source, date) | One-line why |
|---|---|---|---|---|---|---|
| 1 | Qwen3.6-35B-A3B | 20.4GB (MLX 4-bit) / 29.1GB (6-bit) | 262k | Yes, with caveat — known LM Studio parsing bugs, reportedly fixed in LM Studio ≥0.4.5 | SWE-bench Verified 73.4% self-reported (HF card, accessed 2026-08-16) | Newer, higher self-reported score, and less than half qwen3-coder-next's footprint — leaves huge headroom for a second model |
| 2 | qwen/qwen3-coder-next (current) | ~46GB (MLX 4-bit) – 65.5GB (Q6_K) | 262k | Yes, no known LM Studio issues found | SWE-bench Verified 70.6% self-reported (HF card, accessed 2026-08-16) | Proven, already working baseline — keep as the stable fallback |
| 3 | GLM-4.7-Flash | 15GB (6-bit) / 21GB (8-bit) MLX | 131k | Claimed ("trained for tool use"), no confirmed bug reports found | SWE-bench Verified 59.2% self-reported (HF card, accessed 2026-08-16) | Tiny footprint, cheap to run resident alongside a bigger model, though weaker benchmark |
| 4 | MiniMax M2 (2-bit quant) | 74GB (unsloth UD-IQ2_XXS) | 128k | Best-documented — LM Studio explicit support since v0.3.31 | SWE-bench Verified 74-80% self-reported for M2.1/M2.5/M2.7 variants (marktechpost.com 2026-04-12; digitalapplied.com) — not confirmed at the specific 2-bit quant that fits | Highest claimed benchmarks of the fitting set, but razor-thin memory margin and unverified 2-bit quality are real risks |
| 5 | Seed-OSS-36B-Instruct | 29.4GB (MLX 6-bit) | 131k | Claimed native, no bug reports found either way | No confirmed SWE-bench number — LiveCodeBench 67.4% self-reported (HF card) | Light and clean-looking on paper, but weakest benchmark evidence of the shortlist — speculative pick |

**Download commands:**
1. `lms get qwen/qwen3.6-35b-a3b`
2. `lms get qwen/qwen3-coder-next` (already installed)
3. HF: `mlx-community/GLM-4.7-Flash` (verify exact LM Studio catalog id at lmstudio.ai/models/zai-org/glm-4.7-flash)
4. HF: `unsloth/MiniMax-M2-GGUF` (UD-IQ2_XXS build)
5. HF: `lmstudio-community/Seed-OSS-36B-Instruct-MLX-6bit`

---

## Explicit answers

**(a) Does GLM-4.5-Air support tool calling in LM Studio, and what is its context length?**
Context length is 128K ([docs.z.ai/guides/llm/glm-4.5](https://docs.z.ai/guides/llm/glm-4.5), accessed 2026-08-16). The model itself supports tool calls, but LM Studio's integration has an **open, unresolved bug** — [lmstudio-bug-tracker #829](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/829) (filed 2025-08-02, still open as of last activity 2026-02-01): the default Jinja template silently fails or garbles tool calls/web search. Community workaround: switch the model's LM Studio prompt template from Jinja to ChatML.

**(b) Is there a newer Qwen coder model than qwen3-coder-next as of Aug 2026?**
Yes — **Qwen3.6-35B-A3B**, released 2026-04-16 (vs. qwen3-coder-next's ~2026-02-03/04), self-reports higher SWE-bench Verified (73.4% vs 70.6%) at less than half the memory footprint (20-29GB vs 46-85GB), same 262k context, same Apache 2.0 license. Caveat: both benchmark sets are vendor self-reported (no independent verification found for either), and Qwen3.6-35B-A3B had (reportedly now-patched) LM Studio tool-calling bugs that qwen3-coder-next's issue history doesn't show. Sources: [Qwen/Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) vs [Qwen/Qwen3-Coder-Next](https://huggingface.co/Qwen/Qwen3-Coder-Next) (both accessed 2026-08-16). A "Qwen4" is circulating as **RUMOR/UNCONFIRMED** (aggregator claims of a Sept 2026 Apsara Conference launch) — no official source found.

**(c) Any Devstral/Mistral update newer than devstral-small-2-2512?**
No model branded "Devstral" is newer — devstral-small-2-2512 and its sibling Devstral 2 (123B) both released 2025-12-09 ([mistral.ai/news/devstral-2-vibe-cli/](https://mistral.ai/news/devstral-2-vibe-cli/)), and Devstral 2's model card now shows a **deprecation notice** (deprecation date 2026-05-22) recommending Mistral Medium 3.5 instead ([docs.mistral.ai/models/model-cards/devstral-2-25-12](https://docs.mistral.ai/models/model-cards/devstral-2-25-12), accessed 2026-08-16). The closest actual successor is **Mistral Small 4** (2026-03-16, 119B/6B active MoE, Apache 2.0, 256k ctx), which Mistral says "unifies... Devstral for agentic coding" into one model — but discloses no SWE-bench/Terminal-Bench score. [mistral.ai/news/mistral-small-4/](https://mistral.ai/news/mistral-small-4/)
