"""UFC fight simulator: two fighter ratings -> win probability, method/round and
each fighter's DraftKings-point distribution.

We model the striking battle (volume scaled by the opponent's defence), the
takedown game, and finish threat (knockdown power vs the opponent's durability)
into a single dominance score -> win probability. Then we Monte-Carlo the bout:
who wins, whether it's a finish (and the round + method), and each fighter's
significant strikes / takedowns / knockdowns, which become DK points. Out of that
fall both the futures/props numbers (win %, KO/sub/dec, round) and the DFS
projections (proj / floor / median / ceiling per fighter).
"""

import math
import random

import ufc_data

_LG_DEF = 0.55             # league-average striking defence (1 - opp accuracy)
_SCALE = 3.6               # dominance -> win-prob logistic scale
# DraftKings MMA scoring (Classic): sig strikes +0.2, control time +0.03/sec,
# takedown +5, reversal/sweep +5, knockdown +10, advance +3. Fight conclusion:
# R1 win +90, R2 +70, R3 +45, R4/R5 +40, decision +30, quick win (<60s) +25.
_SS_PTS = 0.2
_CTRL_PTS = 0.03           # per second of control
_ADV_PTS = 3
_WIN_PTS = {1: 90, 2: 70, 3: 45, 4: 40, 5: 40}
_DEC_PTS = 30
_QUICK_PTS = 25


def _landed_pm(att, opp):
    """Sig strikes `att` lands per minute, scaled by `opp`'s striking defence."""
    return att["ss_pm"] * (1 - opp["str_def"]) / max(0.2, 1 - _LG_DEF)


def _td_pm(att, opp):
    return att["td_p15"] / 15.0 * (1 - opp["td_def"]) / 0.35


def win_prob(a, b):
    """P(a beats b) from the striking + grappling + finish-threat differential."""
    strike_edge = _landed_pm(a, b) - _landed_pm(b, a)
    grap_edge = (_td_pm(a, b) - _td_pm(b, a)) * 15 * 0.45
    finish_edge = (a["kd_p15"] * (1 - b["durability"]) -
                   b["kd_p15"] * (1 - a["durability"])) * 4.0
    dom = strike_edge + grap_edge + finish_edge
    return 1.0 / (1.0 + math.exp(-dom / _SCALE))


def _pois(lam, rng):
    if lam <= 0:
        return 0
    L = math.exp(-min(30, lam)); k = 0; p = 1.0
    while True:
        k += 1; p *= rng.random()
        if p <= L:
            return k - 1


def _dist(arr):
    arr = sorted(arr)
    n = len(arr)
    q = lambda f: arr[min(n - 1, int(f * n))]
    return {"proj": round(sum(arr) / n, 1), "floor": round(q(0.1), 1),
            "median": round(q(0.5), 1), "ceil": round(q(0.9), 1)}


def simulate_bout(a, b, rounds=3, n=20000, seed=None):
    """Monte-Carlo a bout between rated fighters a and b. Returns per-fighter win%,
    method/round odds and DK point distribution."""
    rng = random.Random(seed)
    pa = win_prob(a, b)
    # Fight-level finish propensity: each fighter's finish threat vs the other's
    # durability, capped to a realistic ~UFC finish rate.
    ft_a = (a["finish_rate"] + a["kd_p15"] * 0.4) * (1.3 - b["durability"])
    ft_b = (b["finish_rate"] + b["kd_p15"] * 0.4) * (1.3 - a["durability"])
    p_finish = max(0.15, min(0.8, (ft_a + ft_b) * 0.7))
    # Share of finishes that are KOs vs subs, from the winner's striking-vs-grappling.
    dk = {a["id"]: [], b["id"]: []}
    wins = {a["id"]: 0, b["id"]: 0}
    methods = {"ko": 0, "sub": 0, "dec": 0}
    rounds_hist = {}
    for _ in range(n):
        win = a if rng.random() < pa else b
        finish = rng.random() < p_finish
        # Finish round: heavy hitters end it EARLY — the round distribution
        # tightens toward R1 as the winner's threat rises (~55% of real UFC
        # finishes land in round 1). That's where the +90 DK bonus lives.
        if finish:
            ft_w = ft_a if win is a else ft_b
            lam = max(0.45, 0.90 - 0.55 * min(1.2, ft_w))
            rd = 1 + _pois(lam, rng)
        else:
            rd = rounds
        if finish and rd > rounds:
            finish = False              # the finish would land after the final bell
        if finish:
            ko_share = win["kd_p15"] * 4 + win["ss_pm"] * 0.05
            sub_share = win["td_p15"] * 0.6 + 0.3
            method = "ko" if rng.random() < ko_share / (ko_share + sub_share) else "sub"
            end_min = (rd - 1) * 5 + rng.uniform(0.5, 5.0)
        else:
            method = "dec"
            rd = rounds
            end_min = rounds * 5.0
        methods[method] += 1
        if method != "dec":             # round distribution among FINISHES only
            rounds_hist[rd] = rounds_hist.get(rd, 0) + 1
        wins[win["id"]] += 1
        for f, o in ((a, b), (b, a)):
            ss = _pois(_landed_pm(f, o) * end_min, rng)
            td = _pois(_td_pm(f, o) * end_min, rng)
            kd = 0
            if finish and f is win and method == "ko":
                kd = 1 + _pois(0.3, rng)
            else:
                kd = _pois(f["kd_p15"] / 15.0 * end_min, rng)
            # Ground game: each takedown buys control time (+0.03/sec) and a
            # chance of positional advances (+3 each) — the wrestler's DK floor.
            ctrl = sum(rng.uniform(30, 110) for _ in range(td))
            ctrl = min(ctrl, end_min * 60 * 0.7)
            adv = _pois(0.6 * td, rng)
            pts = (_SS_PTS * ss + 5 * td + 10 * kd
                   + _CTRL_PTS * ctrl + _ADV_PTS * adv)
            if f is win:
                pts += _WIN_PTS[rd] if (finish and method != "dec") else _DEC_PTS
                if finish and end_min <= 1.0:
                    pts += _QUICK_PTS        # sub-60-second win
            dk[f["id"]].append(pts)

    # Index-aligned per-fighter DK samples: entry i of both arrays is the SAME
    # simulated fight, so lineup/contest scoring sees the true joint outcome
    # (exactly one of the two cashes the win bonus in any given sim).
    step = max(1, n // 1000)
    keep = list(range(0, n, step))

    def fighter_out(f):
        d = _dist(dk[f["id"]])
        d["dk_arr"] = [round(dk[f["id"]][i], 1) for i in keep if i < len(dk[f["id"]])]
        nf = f.get("fights", 0)
        car = f.get("career")
        return {"id": f["id"], "name": f["name"], "fights": nf,
                "record": f"{f['record_w']}-{f['record_l']}",
                "rating": ufc_data.power_rating(f),
                "components": ufc_data.rating_components(f),
                "career": car,
                "career_record": car.get("record") if car else None,
                # "defaulted" = we know nothing (no UFC fights AND no pro record).
                # A UFC debutant with a pro career record is NOT a blind placeholder.
                "thin": nf < 3, "defaulted": nf == 0 and not car,
                "debut": nf == 0 and bool(car),
                "win_pct": round(100 * wins[f["id"]] / n, 1), **d}
    return {"rounds": rounds, "n_sims": n,
            "a": fighter_out(a), "b": fighter_out(b),
            "method": {k: round(100 * v / n, 1) for k, v in methods.items()},
            "finish_round": {str(r): round(100 * c / n, 1)
                             for r, c in sorted(rounds_hist.items())}}


def _compute_board(n):
    c = ufc_data.card()
    if not c or not c.get("bouts"):
        return None
    bouts = []
    for bt in c["bouts"]:
        ra = ufc_data.fighter_rating(bt["a_id"], bt["a_name"])
        rb = ufc_data.fighter_rating(bt["b_id"], bt["b_name"])
        res = simulate_bout(ra, rb, rounds=bt.get("rounds", 3), n=n)
        res["weight"] = bt.get("weight")
        bouts.append(res)
    return {"sport": "ufc", "event": c.get("event"), "date": c.get("date"),
            "n_sims": n, "bouts": bouts}


import threading as _threading
import time as _time
_board_inflight = [False]


def board(n=15000):
    """Whole-card simulator board. NON-BLOCKING: returns the cached board if fresh,
    else kicks a background compute (rating every fighter from history is slow the
    first time) and returns None until it's ready. Cached 6h."""
    import racing
    key = ("ufc_board",)
    hit = racing._form_cache.get(key)
    if hit and (_time.time() - hit[0]) < 6 * 3600 and hit[1] is not None:
        return hit[1]
    if not _board_inflight[0]:
        _board_inflight[0] = True

        def _bg():
            try:
                racing._cached(key, 6 * 3600, lambda: _compute_board(n))
            finally:
                _board_inflight[0] = False
        _threading.Thread(target=_bg, daemon=True).start()
    return None
