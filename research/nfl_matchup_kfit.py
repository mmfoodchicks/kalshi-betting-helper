"""The shrinkage k the 2025 validation may use: the data foundation's own
split-half measurement (research/nfl_matchup_dataset.measure_reliability,
unchanged) run on 2018-2024 ONLY, so no k the validation reads has seen a
2025 game. RESEARCH (numpy). Production keeps the 2018-2025 table
(research/data/nfl_matchup/reliability.json); the validation frame is built
with NFLM_K=fit_only, which makes nfl_matchup read this file instead.

    export DEEP_CACHE_DIR=<store dir> VIGIL_NO_BG=1
    python3 -m research.nfl_matchup_kfit      # -> research/data/nfl_matchup_model/k_fit_only.json
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("VIGIL_NO_BG", "1")

import nfl_matchup_data as D                 # noqa: E402
import nfl_matchup_profiles as P             # noqa: E402
from research import nfl_matchup_dataset as DS   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "nfl_matchup_model", "k_fit_only.json")
SEASONS = range(2018, 2025)


def main():
    t0 = time.time()
    stores = {s: D.load_season(s) for s in SEASONS}
    stores = {s: st for s, st in stores.items() if st}
    if sorted(stores) != list(SEASONS):
        raise SystemExit(f"stores missing: have {sorted(stores)}")
    pos = P.positions_from_ids(D.load_ids())
    rel = DS.measure_reliability(stores, pos)
    out = {"seasons": f"{min(SEASONS)}-{max(SEASONS)}", "method": rel.get("method"), "pooled": rel["pooled"],
           "k": rel["k"], "source": "research/nfl_matchup_kfit.py (research.nfl_matchup_dataset.measure_reliability on "
           "2018-2024 only)", "seconds": round(time.time() - t0, 1),
           "license": {"credit": "FTN Data via nflverse (CC BY-SA 4.0); nflverse-data (CC BY 4.0)"}}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"wrote {OUT} in {out['seconds']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
