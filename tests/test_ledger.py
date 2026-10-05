"""python tests/test_ledger.py: reservations are committed atomically (review point: two workers could both pass a check)."""
import sys, tempfile, pathlib, threading
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "bench"))
from common import Ledger

d = pathlib.Path(tempfile.mkdtemp())
L = Ledger(d / "ledger.jsonl", cap=1.00)
t1 = L.reserve(0.60)
try:
    L.reserve(0.60); raise SystemExit("FAIL: second 0.60 reservation accepted while the first is in flight")
except RuntimeError:
    pass
L.settle(t1, provider="test", usd=0.10)          # measured cost replaces the reservation
t2 = L.reserve(0.60)                               # 0.10 spent + 0.60 <= 1.00: accepted
L.release(t2)
# concurrency: 20 threads try 0.30 each with 0.10 already spent: at most 3 may hold a reservation at once
ok = []; lock = threading.Lock()
def w():
    try:
        t = L.reserve(0.30)
        with lock: ok.append(t)
    except RuntimeError:
        pass
ts = [threading.Thread(target=w) for _ in range(20)]; [t.start() for t in ts]; [t.join() for t in ts]
assert len(ok) == 3, len(ok)
print("ledger reservation tests: OK")
