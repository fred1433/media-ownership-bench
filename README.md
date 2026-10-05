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

## Results (October 4, 2026), first scorer then scorer v2 after the external review
| | Complete passes | Cases complete every pass | Passes with a wrong value | Avoidable blanks (v2) |
|---|---|---|---|---|
| A | 11/24 then 3/24 | 3/8 then 1/8 | 2 then 0 | 7 |
| B | 3/24 then 0/24 | 1/8 then 0/8 | 0 then 0 | 48 |
| C | 4/24 then 1/24 | 1/8 then 0/8 | 3 then 3 | 39 |

Scorer v2 (`bench/score.py`) applies `reference/conventions_v2.json` (three notions: media group, top of the control
chain, controller; one parent convention for all cases), the explicit alias table `reference/entities.json`, and a
mechanical per-field passage check (found in the document, names the entity, states the relation, not cut before a
qualifier, one passage per owner branch). Currency of a source at the target date is not checked mechanically, so a
complete field reads "matches the record, passage located", not "supported". For the workflow arms it also records the
solver's decision on its own dossier (coverage vs decision). The first scores are reproduced by `bench/score_v1.py`
against `reference/cases_v1.json` (`runs/scores_v1.json`).

Total spend $11.16 (OpenAI $9.73, Gemini $0.97, Exa $0.46), including a $0.58 pilot. The review corrections made no
paid call. The point "measure one targeted correction" was NOT run (no new paid call in this edition); the corrected
missing-link choice was evaluated offline only (`bench/detector_v2.py`, `runs/detector_v2_offline.json`).

## Layout
- `reference/cases.json` the eight scored cases and two development cases (schema, sources with four dates)
- `reference/grid.json` first scoring rules, frozen before any scored run; `reference/conventions_v2.json`, `reference/entities.json`, `reference/manual_checks.json` the v2 rules
- `reference/verification.json` the independent second pass over the reference
- `reference/adjudications.json` decisions taken after the freeze from the verification pass, before any output was scored, each with its reason (plus the KPFA leader correction recorded in `cases.json`)
- `bench/` harness (`run.py`, `arms.py`, `common.py`), scorer (`score.py`), failure location (`locate.py`), page build (`build_page.py`)
- `runs/A|B|C|bare/` every output, with usage, cost, latency, search counts; `runs/ledger.jsonl` every billed call;
  `runs/scores.json` the recomputed scores; `runs/citation_checks.json` every citation check (url, passage, fetched, found);
  `runs/locate.json` the stage where each workflow blank or error first shows. No call failed, so there is no `runs/errors.jsonl`.

Retrieved page texts (`runs/private/`) are not published (copyright); B's dossiers keep URLs and short quotes.

## Changed after the first scores were seen (disclosed)
Scorer fixes, no change to cases or reference: curly apostrophes no longer split names (D'Onofrio); aliases of
four letters or fewer match as whole words (NFL); the check that a cited passage names the entity ignores generic
words and accepts a reference alias; a citation whose every page refused automated fetching is reported as
"unverifiable" instead of "unsupported". The B2/C2 variant was designed after reading the first results.

## Three levels of reproduction
1. **Re-aggregate the scores, reusing the published citation checks** (no network): `python bench/score.py`
   (v2) and `python bench/score_v1.py` (first scorer). They read `runs/citation_checks.json` and
   `reference/manual_checks.json`; `--fetch-free` re-fetches missing pages with a plain HTTP GET (no paid API).
2. **Replay**: (a) replay the solvers on the published claim dossiers: each `runs/B/*.json` holds the exact
   `dossier` text given to B's solver and to C; (b) replay on full documents, when available: the retrieved page
   texts are not published (copyright), so this needs your own collection. Model calls are not deterministic.
3. **Run anew on the live web**: `python bench/run.py --out runs_new` with `OPENAI_API_KEY`, `GOOGLE_API_KEY`,
   `EXA_API_KEY`. A new run directory has its own config, ledger, outputs and cache; the runner refuses to write
   into the published `runs/`. `--dry-run` writes the config and stops before any call. Results will differ.

Budget: `bench/common.py` Ledger reserves an estimated maximum per call under a lock, counts calls in flight, and
settles each reservation with the measured cost (`python tests/test_ledger.py`). The published run used an earlier
check that did not reserve money for calls in flight (nothing overran). OpenAI native search cannot be bounded inside
one call, so the cap is a preventive stop based on estimates. `BENCH_MAX_OUTPUT_TOKENS` bounds outputs on a new run
(unset in the published run).

## Changed after the first scores were seen (disclosed)
- First scorer fixes: curly apostrophes, short aliases (NFL), entity check on generic words, "unverifiable" label.
- External review (scorer v2): conventions and entity table committed before the rescoring; the relation vocabulary
  of the passage check was set while reading these outputs. Reference: KPFA leader and Mission president sources,
  controllers of Nexstar and Disney evaluated from their 2026 proxies (`reference/verification_v2.json`).
- B2/C2 variant designed after reading the first results.

## Who checked the reference
The reference was built and then re-checked by two separate AI agent passes (Claude) reading public sources,
not by a human. Disagreements and decisions are in `reference/verification.json` and `reference/adjudications.json`.
