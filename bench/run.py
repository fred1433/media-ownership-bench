"""Runner. Usage: python bench/run.py [--dev] [--cases a,b] [--runs 3] [--arms A,B,C,bare]
Writes runs/<arm>/<case>_r<n>.json. Skips outputs that already exist. Stops at the cost cap (ledger)."""
import argparse, json, os, sys, time, traceback
# --out must be known before common is imported: it decides where config, ledger, outputs and cache go.
if "--out" in sys.argv: os.environ["BENCH_RUN_DIR"] = sys.argv[sys.argv.index("--out") + 1]
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import RUNS, load_cases, LEDGER, SDK, OPENAI_MODEL, GEMINI_MODEL, EFFORT, SYSTEM, ANSWER_SCHEMA, PRICES, CAP_USD
import arms

def save(arm, case, run, out):
    p = RUNS / arm / f"{case['id']}_r{run}.json"; p.parent.mkdir(parents=True, exist_ok=True)
    out = dict(out); out.pop("raw", None) if arm != "A" else None
    out.update({"arm": arm, "case": case["id"], "run": run, "saved": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    p.write_text(json.dumps(out, indent=1, default=str))

def done(arm, case, run): return (RUNS / arm / f"{case['id']}_r{run}.json").exists()

def work(case, runs, armset, start=1):
    log = []
    for run in range(start, runs + 1):
        try:
            if "A" in armset and not done("A", case, run): save("A", case, run, arms.arm_a(case, run))
            if ("B" in armset or "C" in armset):
                if not done("B", case, run): save("B", case, run, arms.arm_b(case, run))
                if "C" in armset and not done("C", case, run):
                    b = json.loads((RUNS / "B" / f"{case['id']}_r{run}.json").read_text())
                    save("C", case, run, arms.arm_c(case, run, b["dossier"]))
            if "B2" in armset or "C2" in armset:
                if not done("B2", case, run): save("B2", case, run, arms.arm_b(case, run, sys_prompt=arms.EXTRACT_SYS_V2, arm="B2"))
                if "C2" in armset and not done("C2", case, run):
                    b = json.loads((RUNS / "B2" / f"{case['id']}_r{run}.json").read_text())
                    save("C2", case, run, arms.arm_c(case, run, b["dossier"], arm="C2"))
            if "bare" in armset and run == 1 and not done("bare", case, run): save("bare", case, run, arms.arm_bare(case, run))
        except Exception as e:
            err = {"case": case["id"], "run": run, "error": repr(e), "trace": traceback.format_exc()[-1500:]}
            (RUNS / "errors.jsonl").open("a").write(json.dumps(err) + "\n"); log.append(err)
            if "cost cap" in repr(e): break
    return log

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--dev", action="store_true"); ap.add_argument("--cases")
    ap.add_argument("--runs", type=int, default=3); ap.add_argument("--arms", default="A,B,C,bare")
    ap.add_argument("--workers", type=int, default=5); ap.add_argument("--out", help="new run directory (own ledger, config, outputs, cache)")
    ap.add_argument("--dry-run", action="store_true", help="write the run config and stop before any API call"); a = ap.parse_args()
    if RUNS == (Path(__file__).resolve().parent.parent / "runs") and not a.dry_run and os.environ.get("BENCH_ALLOW_PUBLISHED_DIR") != "1":
        sys.exit("refusing to write into the published runs/: pass --out <new dir>")
    cases = load_cases(include_dev=True)
    cases = [c for c in cases if c.get("dev")] if a.dev else [c for c in cases if not c.get("dev")]
    if a.cases: cases = [c for c in load_cases(True) if c["id"] in a.cases.split(",")]
    if not set(a.arms.split(",")) <= {"B2", "C2"}: (RUNS / "config.json").write_text(json.dumps({"openai_model_requested": OPENAI_MODEL, "gemini_model_requested": GEMINI_MODEL,
        "endpoints": {"A,B,bare": "OpenAI Responses API", "C": "Gemini Developer API generateContent", "B search": "Exa /search"},
        "sdk": SDK, "reasoning_effort": EFFORT, "B_escalation_effort": "high", "gemini_thinking": "model default",
        "tools": {"A": "web_search (OpenAI native)", "B": "none in-model; Exa search by fixed templates", "C": "none", "bare": "none"},
        "limits": {"B_extra_queries": 2, "B_docs_chars": 3500, "retries": 1, "timeout_s": 600},
        "cap_usd": CAP_USD, "prices": PRICES, "system_prompt": SYSTEM, "schema": ANSWER_SCHEMA}, indent=1))
    print(f"run directory: {RUNS} | ledger: {LEDGER.path} | spent there so far: {LEDGER.total():.4f} | cap {CAP_USD}")
    if a.dry_run: sys.exit(0)
    logs = []
    for r in range(1, a.runs + 1):   # complete passes first, so a cap stop leaves whole passes
        with ThreadPoolExecutor(a.workers) as ex:
            logs += list(ex.map(lambda c: work(c, r, set(a.arms.split(",")), start=r), cases))
        print(f"pass {r} done | spent so far: {LEDGER.total():.4f}", flush=True)
    print("errors:", sum(len(l) for l in logs), "| spent so far:", round(LEDGER.total(), 4))
