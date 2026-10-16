"""The NBA deep engine's tables, measured from possessions (nba_poss over the
nba_pbp records). RESEARCH -- the output, research/data/nba_deep_tables.json,
is what nba_deep_sim samples from. Every table is a COUNT.

  sit       the game situation a possession is played in, from the
            OFFENCE's view: "norm"; "cl+" / "cl-" / "cl0" (the last two
            minutes of the fourth or of overtime, margin within eight, the
            offence leading / trailing / level -- a leader is fouled, a
            trailer hurries); "garbage" (the fourth quarter, margin twenty or
            more -- the benches)
  start     how the possession began (nba_poss.STARTS)
  chance    0 the possession's first shot opportunity, 1 after an offensive
            rebound (a putback is a different shot)

  python3 -m research.nba_deep_fit <store dir> --seasons 2022,2023,2024,2025 --out research/data/nba_deep_tables.json
  python3 -m research.nba_deep_fit <store dir> --elasticity 2023 --tables <tables fit on 2023>
"""
import json
import os
import pickle
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import nba_poss as Q             # noqa: E402

DUR_MAX = 45          # possession durations are kept to the second up to here; longer ones pooled at the cap


def situation(per, start_clk, sd):
    if per >= 4 and start_clk <= 120 and abs(sd) <= 8:
        return "cl+" if sd > 0 else ("cl-" if sd < 0 else "cl0")
    if per == 4 and abs(sd) >= 20:
        return "garbage"
    return "norm"


def final_bucket(clk):
    return "le5" if clk <= 5 else ("6-12" if clk <= 12 else "13-24")


def start_group(start):
    """The kind of first chance a possession's start gives: transition after
    a steal, a live rebound, or a dead ball (a make, made free throws, a
    dead-ball turnover, a period's start)."""
    if start == "steal":
        return "trans"
    if start == "dreb":
        return "live"
    return "dead"


def load_stores(store_dir, seasons):
    out = []
    for s in seasons:
        p = os.path.join(store_dir, f"nba_pbp_{s}.pkl")
        if not os.path.exists(p):
            print(f"[nba-fit] missing {p}", file=sys.stderr)
            continue
        with open(p, "rb") as fh:
            st = pickle.load(fh)
        if isinstance(st, dict) and "payload" in st:
            st = st["payload"]
        out.append((int(s), st))
    return out


def _inc(d, k, v=1):
    d[k] = d.get(k, 0) + v


def fit(stores):
    T = {"dur": {}, "dur_trunc": {}, "final": {}, "opp": {}, "zone": {}, "make": {}, "and1": {}, "fts_n": {}, "oreb": {},
         "ft": [0, 0], "ft_oreb": [0, 0], "last_shot": [0, 0, 0], "home": {"pts": [0, 0], "poss": [0, 0]},
         "games": 0, "home_wins": 0, "ot": 0, "pts": 0, "seasons": []}
    for season, st in stores:
        T["seasons"].append(season)
        for rec in (st.get("games") or {}).values():
            if not Q.P.league_game(rec):
                continue
            T["games"] += 1
            T["pts"] += rec["home_score"] + rec["away_score"]
            T["home_wins"] += rec["home_score"] > rec["away_score"]
            T["ot"] += rec["periods"] > 4
            for x in Q.possessions(rec):
                sit = situation(x["per"], x["start_clk"], x["sd"])
                start = Q.STARTS[x["start"]]
                d = x["start_clk"] - x["end_clk"]
                T["home"]["pts"][x["side"]] += x["pts"]
                T["home"]["poss"][x["side"]] += 1
                # a period's FINAL possession (begun with the shot clock off,
                # ended at the buzzer): heaves and holds, 0.36 points a
                # possession begun with 5 s or less, 0.63 with 6-12, 0.54
                # with 13-24 (2024-25) against 1.14 overall
                if x["end_clk"] <= 0 and x["start_clk"] <= 24:
                    b = final_bucket(x["start_clk"])
                    _inc(T["final"], f"{b}|{min(4, x['pts'])}")
                # a possession the period's clock cut short is CENSORED: its
                # length says only "at least"; it is counted apart so the
                # duration distribution is not pulled short by the buzzer
                if x["end"] == "period":
                    _inc(T["dur_trunc"], f"{sit}|{start}|{min(DUR_MAX, d)}")
                    made_at_buzzer = 1 if any(o[1] for o in x["opps"]) else 0
                    T["last_shot"][0] += 1
                    T["last_shot"][1] += 1 if x["opps"] else 0
                    T["last_shot"][2] += made_at_buzzer
                else:
                    _inc(T["dur"], f"{sit}|{start}|{min(DUR_MAX, d)}")
                # the opportunities, read off the possession's ordered events:
                # one begins at the start and after every live offensive
                # rebound; its outcome is the first turnover, free-throw trip
                # (not an and-one or a technical) or field goal attempt
                seq = x["seq"]
                chance, want = 0, True
                for j, ev in enumerate(seq):
                    kind = ev[0]
                    nxt = seq[j + 1] if j + 1 < len(seq) else None
                    if kind == "oreb":
                        chance, want = 1, True
                        continue
                    if kind == "fts":
                        if ev[5] or ev[4] or not want:
                            if ev[4] and not ev[2]:              # a missed and-one free throw
                                T["ft_oreb"][0] += 1 if (nxt and nxt[0] == "oreb") else 0
                                T["ft_oreb"][1] += 1
                            continue
                        key = f"{sit}|{start if chance == 0 else 'oreb'}"
                        _inc(T["opp"], f"{key}|fts")
                        _inc(T["fts_n"], f"{sit}|{min(3, max(1, ev[1]))}")
                        want = False
                        if not ev[2]:
                            T["ft_oreb"][0] += 1 if (nxt and nxt[0] == "oreb") else 0
                            T["ft_oreb"][1] += 1
                        continue
                    if not want:
                        continue
                    key = f"{sit}|{start if chance == 0 else 'oreb'}"
                    if kind == "tov":
                        _inc(T["opp"], f"{key}|tov{ev[1]}")
                        want = False
                    elif kind == "fga":
                        _, z, made, blk, and1 = ev
                        _inc(T["opp"], f"{key}|fga")
                        # a first chance's shot depends on how the ball came:
                        # after a steal the league scored 1.315 a possession,
                        # after a make 1.113 (2024-25) -- the transition rim
                        # attempt against a set defence
                        sg = start_group(start) if chance == 0 else "-"
                        _inc(T["zone"], f"{sit}|{chance}|{sg}|{z}")
                        row = T["make"].setdefault(f"{sit}|{chance}|{sg}|{z}", [0, 0])
                        row[0] += made
                        row[1] += 1
                        want = False
                        if made:
                            row = T["and1"].setdefault(str(z), [0, 0])
                            row[0] += 1 if and1 else 0
                            row[1] += 1
                        else:
                            row = T["oreb"].setdefault(f"{sit}|{z}", [0, 0])
                            row[0] += 1 if (nxt and nxt[0] == "oreb") else 0
                            row[1] += 1
                # free-throw shooting, every attempt
                for made, _o in x["fts"]:
                    T["ft"][0] += made
                    T["ft"][1] += 1
    return T


def score_slopes(stores):
    """The pull back toward an even game: for each quarter (overtime with the
    fourth), the slope of a possession's points on the offence's margin
    (clamped to +/-20), NET of the two teams' season strengths -- leaders are
    mostly the better teams, so the raw slope hides it. 2024-25: -0.0012 /
    -0.0025 / -0.0010 / -0.0031 points a possession per point of lead; fed to
    the margin's random walk that is what takes an independent-possession
    game's margin SD from 16.9 to ~14 (real, net of team strength: 13.6)."""
    import collections
    num = collections.Counter()
    den = collections.Counter()
    sx = collections.Counter()
    sy = collections.Counter()
    nn = collections.Counter()
    for season, st in stores:
        games = [g for g in (st.get("games") or {}).values() if Q.P.league_game(g)]
        off = collections.defaultdict(lambda: [0, 0])
        dfn = collections.defaultdict(lambda: [0, 0])
        rows = []
        for g in games:
            teams = (g["home"], g["away"])
            for x in Q.possessions(g):
                o, d = teams[x["side"]], teams[1 - x["side"]]
                rows.append((o, d, min(4, x["per"]), max(-20, min(20, x["sd"])), x["pts"]))
                off[o][0] += x["pts"]
                off[o][1] += 1
                dfn[d][0] += x["pts"]
                dfn[d][1] += 1
        lg = sum(v[0] for v in off.values()) / max(1, sum(v[1] for v in off.values()))
        for o, d, q, m, pts in rows:
            r = pts - (lg + (off[o][0] / off[o][1] - lg) + (dfn[d][0] / dfn[d][1] - lg))
            sx[q] += m
            sy[q] += r
            num[q] += m * r
            den[q] += m * m
            nn[q] += 1
    out = {}
    for q in (1, 2, 3, 4):
        if nn[q]:
            mx, my = sx[q] / nn[q], sy[q] / nn[q]
            out[str(q)] = (num[q] / nn[q] - mx * my) / (den[q] / nn[q] - mx * mx)
    return out


def measure_elasticity(store, T, exps=(1.0, 1.33, 1.67, 2.0), n=300):
    """The engine's elasticity to team levers (nba_deep_data.LEVER_EXP). Each
    team's full-season MEASURED units, raised to exponent e, play a
    league-average side on a neutral floor n times; the exponent is right when
    the engine's mean margins line up with the teams' real ones (the slope of
    real on engine is 1). The real margins were played with the pull back
    toward an even game and so is the engine, so a team's measured units as
    levers (e = 1) come out too small, as home court did (a 1.0072 lever
    realized 1.0085 of the league's 1.0144). Measured on a TRAINING season
    only: no validation season is read. Returns {e: (slope, sd engine, sd
    real, corr)}."""
    import collections
    import random
    import statistics as stt
    import zlib
    import nba_deep_data as D
    import nba_deep_sim as E
    games = [g for g in (store.get("games") or {}).values() if Q.P.league_game(g)]
    acc, real = {}, collections.defaultdict(list)
    for g in games:
        U = D.game_units(g)
        for s_, ab in ((0, g["home"]), (1, g["away"])):
            D.add(acc.setdefault(ab, {}), U[s_])
        m = g["home_score"] - g["away_score"]
        real[g["home"]].append(m)
        real[g["away"]].append(-m)
    lg = D.league(acc)
    units = {ab: D.raw_profile(u, lg) for ab, u in acc.items()}
    L = E.league_profile()
    teams = sorted(units)
    rm = {ab: stt.mean(real[ab]) for ab in teams}
    out = {}
    for e in exps:
        em = {}
        for ab in teams:
            prof = dict(L)
            prof.update(D.levers({k: (v if v else 1.0) for k, v in units[ab].items()}, e))
            rng = random.Random(zlib.crc32(f"{ab}|{e}".encode()))
            ms = []
            for i in range(n):
                gm = E.play_game(prof, L, rng, T, neutral=True) if i % 2 == 0 else E.play_game(L, prof, rng, T, neutral=True)
                d = gm["score"][0] - gm["score"][1]
                ms.append(d if i % 2 == 0 else -d)
            em[ab] = stt.mean(ms)
        xs = [em[ab] for ab in teams]
        ys = [rm[ab] for ab in teams]
        mx, my = stt.mean(xs), stt.mean(ys)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sd_e, sd_r = stt.pstdev(xs), stt.pstdev(ys)
        out[e] = (sxy / sum((x - mx) ** 2 for x in xs), sd_e, sd_r, sxy / (len(xs) * sd_e * sd_r))
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("store")
    ap.add_argument("--seasons", default="2022,2023,2024,2025")
    ap.add_argument("--out", default=None)
    ap.add_argument("--elasticity", type=int, default=None)
    ap.add_argument("--tables", default=None)
    a = ap.parse_args(argv)
    if a.elasticity is not None:
        import nba_deep_sim as E
        st = dict(load_stores(a.store, [a.elasticity]))[a.elasticity]
        for e, (slope, sde, sdr, corr) in measure_elasticity(st, E.tables(a.tables)).items():
            print(f"exp {e:.2f}: real-on-engine slope {slope:.3f}, sd engine {sde:.2f} vs real {sdr:.2f}, corr {corr:.3f}")
        sys.exit(0)
    stores = load_stores(a.store, [int(x) for x in a.seasons.split(",")])
    T = fit(stores)
    T["dur_max"] = DUR_MAX
    T["score_slope"] = score_slopes(stores)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(T, fh, sort_keys=True)
    return T


if __name__ == "__main__":
    T = main(sys.argv[1:])
    print("games", T["games"], "pts/game", round(T["pts"] / max(1, T["games"]), 1))
