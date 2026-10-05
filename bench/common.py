"""Shared pieces: answer schema, prompts, prices, cost ledger with a hard cap."""
import json, os, re, threading, time, unicodedata, uuid, importlib.metadata as md
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# A run directory holds its own config, ledger, outputs and cache. The published run is runs/;
# a new run goes elsewhere (BENCH_RUN_DIR or run.py --out) so it never mixes with it.
RUNS = Path(os.environ.get("BENCH_RUN_DIR", ROOT / "runs")).resolve()
PRIVATE = RUNS / "private"   # full page texts: not published (copyright)
TARGET_DATE = "2026-10-04"
CAP_USD = float(os.environ.get("BENCH_CAP_USD", "15.0"))

OPENAI_MODEL = "gpt-6.1-sol"
GEMINI_MODEL = "gemini-3.8-flash"
EFFORT = "medium"

# Public list prices read on 2026-10-04 (developers.openai.com/api/docs/pricing, ai.google.dev/gemini-api/docs/pricing)
PRICES = {
    "gpt-6.1-sol": {"in": 2.00, "cached": 0.10, "out": 10.00},
    "gemini-3.8-flash": {"in": 0.75, "cached": 0.75, "out": 3.75},
    "openai_web_search_call": 10.00 / 1000,      # reasoning models; search content tokens billed as input
    "gemini_search_query": 14.00 / 1000,          # not used in this bench (no Gemini grounding)
}

def _ver(p):
    try: return md.version(p)
    except md.PackageNotFoundError: return None

SDK = {
    "openai": _ver("openai"),
    "google-genai": _ver("google-genai"),
    "exa": "REST https://api.exa.ai/search (no SDK)",
}

def citations_schema():
    return {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["url", "quote"],
            "properties": {"url": {"type": "string"}, "quote": {"type": "string"}}}}

def entity(status_enum):
    return {"type": "object", "additionalProperties": False,
            "required": ["status", "names", "citations"],
            "properties": {"status": {"type": "string", "enum": status_enum},
                           "names": {"type": "array", "items": {"type": "string"}},
                           "citations": citations_schema()}}

ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["legal_owner", "operator", "chain", "ultimate_parent", "controlling_person", "leader",
                 "transactions", "uncertainty"],
    "properties": {
        "legal_owner": entity(["established", "not_established"]),
        "operator": entity(["established", "same_as_owner", "not_established"]),
        "chain": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                  "required": ["child", "parent", "relation", "citations"],
                  "properties": {"child": {"type": "string"}, "parent": {"type": "string"},
                                 "relation": {"type": "string"}, "citations": citations_schema()}}},
        "ultimate_parent": entity(["single", "multiple", "none", "not_established"]),
        "controlling_person": entity(["established", "none", "not_established", "not_applicable"]),
        "leader": {"type": "object", "additionalProperties": False,
                   "required": ["status", "name", "role", "entity", "citations"],
                   "properties": {"status": {"type": "string", "enum": ["established", "not_established"]},
                                  "name": {"type": ["string", "null"]}, "role": {"type": ["string", "null"]},
                                  "entity": {"type": ["string", "null"]}, "citations": citations_schema()}},
        "transactions": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                         "required": ["description", "status", "date", "citations"],
                         "properties": {"description": {"type": "string"},
                                        "status": {"type": "string", "enum": ["announced", "approved", "closed", "abandoned", "unknown"]},
                                        "date": {"type": ["string", "null"]}, "citations": citations_schema()}}},
        "uncertainty": {"type": "string"},
    },
}

SYSTEM = f"""You are a media ownership researcher. Today's date is {TARGET_DATE}; answer as of that date, not as of your training data.
Fields:
- legal_owner: the entity that legally holds the outlet (for a broadcast station, the FCC licensee).
- operator: the entity that runs it day to day if different (for example under a shared services or joint sales agreement); otherwise status same_as_owner.
- chain: each ownership link from the legal owner upward, one link per item, each with its own citation.
- ultimate_parent: the top of the chain. It may be several entities (joint venture), none (a nonprofit with no owner), or not_established.
- controlling_person: a natural person or family that controls the ultimate parent, if documented.
- leader: only the person matching the leader definition given in the question.
- transactions: deals affecting ownership; keep announced, approved and closed apart.
Rules: every established field must cite at least one source URL with a short verbatim quote copied from that source that supports it. If the evidence does not establish a field, mark it not_established rather than guessing. A name appearing in a filing is not by itself an owner or officer."""

def user_prompt(case):
    return f"""Question (as of {TARGET_DATE}): {case['question']}
Outlet: {case['media']['name']} ({case['media'].get('kind')}, {case['media'].get('market') or ''}{', call sign ' + case['media']['callsign'] if case['media'].get('callsign') else ''}).
Leader definition for this question: {case['leader_definition']}"""


class Ledger:
    """Append-only cost ledger with atomic reservations.
    reserve() commits an estimated maximum under the lock and counts it as in flight; settle() replaces it with
    the measured cost; release() frees it when the call failed before billing. A reservation is refused when
    measured spend + reservations in flight + the new reservation would pass the cap. The spend is re-read
    from the file on every reservation, so a resumed run starts from what was really spent.
    For OpenAI native search the reservation is an estimate: the caller cannot bound the searches inside one
    call, so the cap is a preventive stop based on an estimate, not a guaranteed maximum."""
    def __init__(self, path=None, cap=None):
        self.path = Path(path or RUNS / "ledger.jsonl"); self.cap = CAP_USD if cap is None else cap
        self.lock = threading.Lock(); self.inflight = {}; self.path.parent.mkdir(parents=True, exist_ok=True)

    def total(self):
        if not self.path.exists(): return 0.0
        return sum(json.loads(l)["usd"] for l in self.path.read_text().splitlines() if l.strip())

    def reserve(self, amount):
        with self.lock:
            t = self.total(); f = sum(self.inflight.values())
            if t + f + amount > self.cap:
                raise RuntimeError(f"cost cap: spent {t:.4f} + in flight {f:.2f} + reservation {amount:.2f} > {self.cap}")
            tok = uuid.uuid4().hex; self.inflight[tok] = amount
            return tok

    def release(self, tok):
        with self.lock: self.inflight.pop(tok, None)

    def settle(self, tok, **row):
        row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with self.lock:
            row["reserved_usd"] = self.inflight.pop(tok, None)
            with self.path.open("a") as f: f.write(json.dumps(row) + "\n")

LEDGER = Ledger()

def openai_cost(usage, search_calls=0):
    p = PRICES[OPENAI_MODEL]
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
    inp = usage.get("input_tokens", 0) - cached
    out = usage.get("output_tokens", 0)  # includes reasoning tokens (not counted twice)
    return inp * p["in"] / 1e6 + cached * p["cached"] / 1e6 + out * p["out"] / 1e6 + search_calls * PRICES["openai_web_search_call"]

def gemini_cost(usage):
    p = PRICES[GEMINI_MODEL]
    inp = usage.get("prompt_token_count", 0) or 0
    out = (usage.get("candidates_token_count", 0) or 0) + (usage.get("thoughts_token_count", 0) or 0)
    return inp * p["in"] / 1e6 + out * p["out"] / 1e6

def norm(s):
    s = re.sub(r"['\u2019\u2018`]", "", s or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(inc|llc|l l c|corp|corporation|co|company|the|ltd|lp|l p|holdings?|group)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def load_cases(include_dev=False):
    d = json.loads(Path(os.environ.get("BENCH_CASES", ROOT / "reference" / "cases.json")).read_text())
    return [c for c in d["cases"] if include_dev or not c.get("dev")]
