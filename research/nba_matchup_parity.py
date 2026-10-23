"""Production against research on one held-out date (RESEARCH ONLY): replay the
deep store's games into the book exactly as nba_matchup.rebuild does (season
before + season), price every player of the date's games with
nba_matchup.predict -- the absent regulars read off the box, as the research
rows did -- and compare P(stat >= k) with the research pipeline's shipped
predictions for the same rows (research/nba_matchup_fit.py dump_preds).

  python3 research/nba_matchup_parity.py <store dir> <work dir> 2026-03-10

The one known difference is the comparables' history: research rows saw every
earlier season back to 2022-23 (rows.pkl holds 2022-23 .. 2025-26; 2021-22 was
history for the profiles only), production logs hold the season before and the
current one (rung 5 ships only for rebounds, coefficient 0.085)."""
import os
import pickle
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import nba_matchup as NM                 # noqa: E402
import nba_matchup_data as D             # noqa: E402
import nba_matchup_profiles as M         # noqa: E402
from research import nba_matchup_walk as W   # noqa: E402


def main(store, work, day):
    P = NM.params()
    season = D.season_of(day)
    ents = [e for e in W.load_entries(store, (season - 1, season)) if e["day"] < day]
    today = [e for e in W.load_entries(store, (season,)) if e["day"] == day]
    book, logs = NM.replay(ents, P)
    book.roll(season)
    with open(os.path.join(work, "test_preds.pkl"), "rb") as fh:
        preds = pickle.load(fh)
    ship, mm = NM._shipped(P)
    diffs = {m: [] for m in ("pts", "reb", "ast", "tpm", "pra")}
    n = 0
    for g in today:
        played = {s: {pid for pid, r in g["players"].items() if r[D.PI["played"]] and r[D.PI["side"]] == s}
                  for s in (0, 1)}
        for s, team, opp in ((0, g["home"], g["away"]), (1, g["away"], g["home"])):
            own_t, opp_t = book.team(team), book.team(opp)
            ctx = NM.team_context(book, team, day, lambda pid, s=s: pid not in played[s], P, own_t)
            nets = (NM.net_rating(own_t, P), NM.net_rating(opp_t, P))
            for pid in played[s]:
                key = (g["id"], pid)
                if key not in preds:
                    continue
                sn = book.player(pid, day)
                if sn is None:
                    continue
                pos = sn["pos"] if sn["pos"] >= 0 else g["players"][pid][D.PI["pos_g"]]
                mrow = NM.minutes_row(sn, team, book.teams[team].tg, pos, ctx, nets[0], nets[1])
                pr = NM.predict(sn, own_t, opp_t, P, s == 0, ctx, logs.get(pid) or [], M._day(day), mrow)
                n += 1
                for m in diffs:
                    sf_p = pr["markets"][m]["sf"]
                    sf_r = preds[key][m]
                    for k in NM.LINES[m]:
                        diffs[m].append(abs(sf_p[k] - sf_r[k]))
    print(f"{day}: {n} players priced by both")
    for m, d in diffs.items():
        d.sort()
        if d:
            print(f"  {m}: |production - research| P(>=k) mean {sum(d) / len(d):.5f}, median {d[len(d) // 2]:.5f}, "
                  f"95th {d[int(0.95 * len(d))]:.5f}, max {d[-1]:.5f} over {len(d)} lines")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
