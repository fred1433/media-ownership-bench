# media-ownership-bench

A small, measured bench on one research task: **who owns this US media outlet, as of 2026-10-04?**
Legal owner (the FCC licensee for a station), ownership chain, ultimate parent, controlling person and one
defined leader, each with a citation. Eight scored cases, three configurations, three passes each.

Page: https://theaipipe.com/demos/media-ownership-bench/

## What is compared
- **A, native search**: `gpt-6.1-sol` on the OpenAI Responses API with the `web_search` tool, reasoning effort
  `medium`, today's date in the instructions, strict JSON schema output.
- **B, bounded workflow**: same model, same effort. Search is explicit (Exa `/search`, fixed query templates),
  documents are recorded, claims are extracted with quotes, code checks the state of the evidence (missing link,
  contradiction, announced deal without a closing), runs at most one targeted extra search, escalates the final
  solve to effort `high` only on a contradiction, then stops.
- **C, same evidence, different model**: the exact dossier given to B's final solver, handed to
  `gemini-3.8-flash` with no tools. This compares two solvers on constant evidence; it says nothing about
  Gemini's own search.
- **Control, bare API**: same model and prompt, no tools, one pass.

Gemini Search grounding is not used: the Gemini API terms say Grounded Results may not be cached, syndicated,
analyzed or otherwise learned from, which rules out publishing and scoring them here.

## Results (October 4, 2026; recomputed by `bench/score.py`)
| | Native search (A) | Bounded workflow (B) | Same evidence, gemini-3.8-flash (C) |
|---|---|---|---|
| Cases complete at all 3 passes | 3/8 | 1/8 | 1/8 |
| Complete supported passes | 11/24 | 3/24 | 4/24 |
| Passes with an asserted wrong value | 2 | 0 | 3 |
| Avoidable blanks (fields) | 4 | 42 | 33 |
| Cost per attempt | $0.23 | $0.09 (incl. Exa) | $0.08 (reconstructed: B retrieval and extraction + C solve) |

Variants: B2/C2 (extraction told to follow the chain upward, same documents) left B at 3/24 and C at 5/24.
Control without search: 0/8, no wrong value, 28 of 32 fields blank. Total spend $11.16
(OpenAI $9.73, Gemini $0.97, Exa $0.46), including a $0.58 pilot on a development case.

## Layout
- `reference/cases.json` the eight scored cases and two development cases (schema, sources with four dates)
- `reference/grid.json` scoring rules and aliases, frozen before any scored run (see git history)
- `reference/verification.json` the independent second pass over the reference
- `reference/adjudications.json` decisions taken after measurement, each with its reason
- `bench/` harness (`run.py`, `arms.py`, `common.py`), scorer (`score.py`), failure location (`locate.py`), page build (`build_page.py`)
- `runs/A|B|C|bare/` every output, with usage, cost, latency, search counts; `runs/ledger.jsonl` every billed call;
  `runs/scores.json` the recomputed scores; `runs/errors.jsonl` failures

Retrieved page texts (`runs/private/`) are not published (copyright); B's dossiers keep URLs and short quotes.

## Three levels of reproduction
1. **Recompute the scores** from the published outputs: `python bench/score.py --no-fetch` (citation checks
   that need a source text not in the cache are then marked unfetched).
2. **Replay on the frozen corpus**: put the retrieved documents back in `runs/private/` and rerun B and C;
   the model calls are not deterministic, so results vary.
3. **Rerun on the live web**: `python bench/run.py` with `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `EXA_API_KEY`.
   Results are not guaranteed to match: the web, the models and the facts move.

The runner refuses any call once measured spend plus a reserve would pass `BENCH_CAP_USD` (15 by default).
OpenAI's native web search does not let the caller bound how many searches it runs inside one call, so the cap is
enforced between calls, not inside one.

## Who checked the reference
The reference was built and then re-checked by two separate AI agent passes (Claude) reading public sources,
not by a human. Disagreements and decisions are in `reference/verification.json` and `reference/adjudications.json`.
