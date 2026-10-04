"""Recompute every score from runs/ and reference/. Usage: python bench/score.py [--no-fetch]
Citation check (automated part): the cited passage exists in the source document, and it names the claimed entity.
Source texts are fetched once through Exa /contents and cached in runs/private (not published)."""
import json, os, re, statistics, sys, difflib
from pathlib import Path
import httpx, jsonschema
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import RUNS, PRIVATE, ROOT, load_cases, norm, ANSWER_SCHEMA, LEDGER

FIELDS = ["legal_owner", "ultimate_parent", "controlling_person", "leader"]
GRID = json.loads((ROOT / "reference" / "grid.json").read_text())
ADJ = {k: {"extra_aliases": v} for k, v in GRID["pre_registered_aliases"].items()}
_post = ROOT / "reference" / "adjudications.json"   # post-measurement decisions, each with its reason
if _post.exists():
    for k, v in json.loads(_post.read_text()).items():
        if k.startswith("_"): continue
        ADJ.setdefault(k, {"extra_aliases": []}); ADJ[k]["extra_aliases"] = ADJ[k].get("extra_aliases", []) + v.get("extra_aliases", [])
        if "override" in v: ADJ[k]["override"] = v["override"]
        if "accept_also" in v: ADJ[k]["accept_also"] = v["accept_also"]
CACHE = PRIVATE / "source_cache.json"
cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
FETCH = "--no-fetch" not in sys.argv


def names_of(e):
    return [e["name"]] + e.get("aliases", []) if isinstance(e, dict) and e.get("name") else []


def match(model_name, admissible):
    m = norm(model_name)
    if not m: return False
    for a in admissible:
        a = norm(a)
        if a and (a == m or (len(a) > 4 and (a in m or m in a))): return True
    return False


SUFFIX = {"jr", "sr", "ii", "iii", "iv"}
GENERIC = {"broadcasting", "media", "licensing", "license", "licenses", "family", "trust", "foundation", "network", "networks", "communications", "television", "radio", "stations", "national", "capital", "company", "operations", "system", "fund", "policy", "reform", "spanish"}
def person_match(model_name, admissible):
    m = [x for x in norm(model_name).split() if x not in SUFFIX]
    for a in admissible:
        t = [x for x in norm(a).split() if x not in SUFFIX]
        if t and m and t[-1] == m[-1] and (t[0][0] == m[0][0]): return True
    return False


def source_text(url):
    if url in cache: return cache[url]
    if not FETCH: return None
    txt = None
    try:  # direct fetch first (HTML only), then Exa /contents
        r = httpx.get(url, timeout=40, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36"})
        if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
            t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
            t = re.sub(r"<[^>]+>", " ", t); t = re.sub(r"&nbsp;|&#160;", " ", t); t = re.sub(r"&amp;", "&", t)
            if len(t.strip()) > 500: txt = t
    except Exception:
        pass
    if txt: cache[url] = txt; return txt
    try:
        LEDGER.guard(0.01)
        r = httpx.post("https://api.exa.ai/contents", timeout=90, headers={"x-api-key": os.environ["EXA_API_KEY"]},
                       json={"urls": [url], "text": {"maxCharacters": 200000}, "livecrawl": "fallback"})
        d = r.json(); usd = (d.get("costDollars") or {}).get("total", 0.0)
        LEDGER.add(provider="exa", arm="score", case="-", run=0, step="contents", url=url, usd=usd)
        res = d.get("results") or []
        txt = res[0].get("text") if res else None
    except Exception:
        txt = None
    cache[url] = txt
    return txt


def located(quote, text):
    if not text or not quote: return False
    t = norm(text); parts = [norm(p) for p in re.split(r"\.\.\.|…", quote) if len(norm(p)) > 3]
    if not parts: return False
    ok = 0
    for p in parts:
        if p in t: ok += 1; continue
        # tolerate small transcription differences: best matching window
        sm = difflib.SequenceMatcher(None, t, p, autojunk=False)
        blk = sm.find_longest_match(0, len(t), 0, len(p))
        if blk.size >= 0.8 * len(p): ok += 1
    return ok == len(parts)


def cite_ok(citations, entity_names):
    """At least one citation whose passage is found in the source and names the entity."""
    detail = []
    for c in citations:
        txt = source_text(c["url"])
        found = located(c["quote"], txt)
        q = norm(c["quote"]); qt = set(q.split())
        names = (not entity_names) or any(norm(n) and (norm(n) in q or any(t in qt for t in norm(n).split() if len(t) >= 4 and t not in GENERIC)) for n in entity_names)
        detail.append({"url": c["url"], "fetched": txt is not None, "passage_found": found, "names_entity": names})
    return any(d["passage_found"] and d["names_entity"] for d in detail), detail


def field_verdict(case, field, ans):
    ref = case["reference"]; adj = ADJ.get(f"{case['id']}:{field}", {})
    if field == "legal_owner":
        rs = "established" if ref["legal_owner"].get("name") else "not_established"
        adm = names_of(ref["legal_owner"]) + adj.get("extra_aliases", [])
        ms = ans["legal_owner"]["status"]; names = ans["legal_owner"]["names"]
        ok = ms == "established" and any(match(n, adm) for n in names)
        cites = ans["legal_owner"]["citations"]
    elif field == "ultimate_parent":
        up = ref["ultimate_parent"]; rs = up["status"]; ms = ans["ultimate_parent"]["status"]; names = ans["ultimate_parent"]["names"]
        ents = [names_of(e) for e in up.get("entities", [])]
        adm = [n for e in ents for n in e] + adj.get("extra_aliases", [])
        if rs == "single" and all(match(n, names_of(ref["legal_owner"])) for e in ents for n in e[:1]):
            ok = ms == "none" or (ms in ("single", "multiple") and bool(names) and any(match(n, adm) for n in names))
        elif rs in ("single", "multiple"):
            ok = ms in ("single", "multiple") and all(any(match(n, e + adj.get("extra_aliases", [])) for n in names) for e in ents) and bool(names)
        elif rs == "none":
            ok = ms == "none" or (ms == "single" and all(match(n, names_of(ref["legal_owner"])) for n in names) and names)
            adm = names_of(ref["legal_owner"])
        else:
            ok = None
        cites = ans["ultimate_parent"]["citations"]
    elif field == "controlling_person":
        cp = ref["controlling_person"]; rs = cp["status"]; ms = ans["controlling_person"]["status"]; names = ans["controlling_person"]["names"]
        adm = cp.get("names", []) + adj.get("extra_aliases", [])
        if rs == "established":
            ok = ms == "established" and any(person_match(n, adm) or match(n, adm) for n in names)
        elif rs in ("none", "not_applicable"):
            ok = ms in ("none", "not_applicable")
        else:
            ok = None
        cites = ans["controlling_person"]["citations"]
    else:
        ld = ref["leader"]; rs = ld["status"]; ms = ans["leader"]["status"]; names = [ans["leader"]["name"]] if ans["leader"]["name"] else []
        adm = ([ld["name"]] if ld.get("name") else []) + ld.get("aliases", []) + adj.get("extra_aliases", [])
        ok = (ms == "established" and any(person_match(n, adm) for n in names)) if rs == "established" else None
        cites = ans["leader"]["citations"]
    model_asserts = ms not in ("not_established",)
    if rs == "not_established":
        if not model_asserts: return {"verdict": "abstain_justified", "status": ms, "names": names}
        if adj.get("accept_also") and any(person_match(n, adj["accept_also"]) or match(n, adj["accept_also"]) for n in names):
            good, detail = cite_ok(cites, names)
            return {"verdict": "correct_supported" if good else "correct_unsupported", "status": ms, "names": names, "citations": detail, "note": "accepted by adjudication"}
        return {"verdict": "unsupported", "status": ms, "names": names, "note": "reference not established"}
    if not model_asserts:
        return {"verdict": "abstain_avoidable", "status": ms, "names": names}
    if ok is False and adj.get("override") == "correct":
        ok = True
    if not ok:
        return {"verdict": "error", "status": ms, "names": names}
    if ms in ("none", "not_applicable") and rs in ("none", "not_applicable"):
        return {"verdict": "correct_supported", "status": ms, "names": names, "cite": "not required"}
    good, detail = cite_ok(cites, ([] if ms == "none" and not names else names + adm))
    if not good and detail and not any(d["fetched"] for d in detail):
        return {"verdict": "correct_unverifiable", "status": ms, "names": names, "citations": detail,
                "note": "every cited source refused automated fetching"}
    return {"verdict": "correct_supported" if good else "correct_unsupported", "status": ms, "names": names, "citations": detail}


def score_output(case, path):
    d = json.loads(path.read_text())
    out = {"case": case["id"], "arm": d["arm"], "run": d["run"], "usd": d.get("usd_total", d.get("usd")),
           "latency_s": d.get("latency_total_s", d.get("latency_s")), "web_search": d.get("web_search"),
           "escalated": d.get("escalated"), "flags": d.get("flags")}
    try:
        ans = json.loads(d["text"]); jsonschema.validate(ans, ANSWER_SCHEMA); out["format_valid"] = True
    except Exception as e:
        out.update(format_valid=False, fields={}, complete_supported=False, asserted_error=False); return out
    f = {k: field_verdict(case, k, ans) for k in FIELDS}
    out["fields"] = f
    v = [x["verdict"] for x in f.values()]
    out["complete_supported"] = all(x in ("correct_supported", "abstain_justified") for x in v)
    out["asserted_error"] = "error" in v
    out["unsupported"] = sum(x in ("unsupported", "correct_unsupported") for x in v)
    out["unverifiable"] = v.count("correct_unverifiable")
    out["abstain_justified"] = v.count("abstain_justified"); out["abstain_avoidable"] = v.count("abstain_avoidable")
    out["answer"] = {k: ans[k] for k in ("legal_owner", "operator", "ultimate_parent", "controlling_person", "leader", "transactions", "uncertainty")}
    return out


def main():
    cases = {c["id"]: c for c in load_cases()}
    rows = []
    for arm in ["A", "B", "C", "B2", "C2", "bare"]:
        for p in sorted((RUNS / arm).glob("*.json")):
            cid = p.stem.rsplit("_r", 1)[0]
            if cid in cases: rows.append(score_output(cases[cid], p))
    CACHE.write_text(json.dumps(cache))
    agg = {}
    for arm in ["A", "B", "C", "B2", "C2", "bare"]:
        r = [x for x in rows if x["arm"] == arm]
        if not r: continue
        per_case = {}
        for x in r: per_case.setdefault(x["case"], []).append(x)
        n_runs = max(len(v) for v in per_case.values())
        complete = sum(x["complete_supported"] for x in r)
        usd = [x["usd"] for x in r]; lat = [x["latency_s"] for x in r]
        agg[arm] = {
            "outputs": len(r), "runs_per_case": n_runs,
            "cases_all_runs_complete": sum(all(x["complete_supported"] for x in v) and len(v) == n_runs for v in per_case.values()),
            "cases": len(per_case),
            "complete_supported_outputs": complete,
            "outputs_with_asserted_error": sum(x["asserted_error"] for x in r),
            "unsupported_fields": sum(x.get("unsupported", 0) for x in r),
            "unverifiable_fields": sum(x.get("unverifiable", 0) for x in r),
            "abstain_justified_fields": sum(x.get("abstain_justified", 0) for x in r),
            "abstain_avoidable_fields": sum(x.get("abstain_avoidable", 0) for x in r),
            "format_valid": sum(x["format_valid"] for x in r),
            "per_case": {k: {"complete": f"{sum(x['complete_supported'] for x in v)}/{len(v)}",
                             "errors": sum(x["asserted_error"] for x in v),
                             "usd": round(sum(x["usd"] for x in v), 4)} for k, v in per_case.items()},
            "usd_total": round(sum(usd), 4), "usd_per_attempt": round(sum(usd) / len(usd), 4),
            "usd_per_complete_supported": round(sum(usd) / complete, 4) if complete else None,
            "latency_median_s": statistics.median(lat), "latency_range_s": [min(lat), max(lat)],
            "searches_per_answer": [x["web_search"].get("calls_executed") for x in r] if arm == "A" else None,
        }
    (RUNS / "scores.json").write_text(json.dumps({"aggregate": agg, "outputs": rows}, indent=1))
    for arm, a in agg.items():
        print(arm, {k: v for k, v in a.items() if k not in ("per_case", "searches_per_answer")})

if __name__ == "__main__":
    main()
