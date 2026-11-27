"""Guards for college conference membership (2026-11-27; cfb.teams). The C3 replay found the 2026 CFP sim playing
nine realigned schools in their 2025 leagues and missing the two new FBS schools: cfb.teams took membership from
LAST season's standings tree, which is the prior's source, not this season's leagues.

    python3 tests/guards_auto_cfb_membership.py      # exits 1 on any failed check
"""
import os
import sys

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)


def _tree(confs):
    """An ESPN standings tree: {conference: [(id, name, wins, losses, point differential)]}."""
    return {"children": [{"name": c, "standings": {"entries": [
        {"team": {"id": tid, "displayName": nm, "abbreviation": nm[:4].upper(), "location": nm, "name": "X"},
         "stats": [{"name": "pointDifferential", "value": pd, "displayValue": str(pd)},
                   {"name": "overall", "value": None, "displayValue": f"{w}-{l}"},
                   {"name": "gamesPlayed", "value": float(w + l), "displayValue": str(w + l)}]}
        for tid, nm, w, l, pd in rows]}} for c, rows in confs.items()]}


def run(ck):
    import cfb
    import racing
    S = 2097
    trees = {
        # last season: Boise in the Mountain West, Utah State there too, "Gone U" still FBS; Sac State is FCS
        (S - 1, "level=3"): _tree({"Mountain West Conference": [("68", "Boise State", 9, 5, 70),
                                                                ("328", "Utah State", 6, 7, -10),
                                                                ("999", "Gone U", 1, 11, -200)],
                                   "Pac-12 Conference": [("38", "Colorado", 9, 4, 90)]}),
        (S - 1, "group=81"): _tree({"Big Sky Conference": [("16", "Sacramento State", 7, 5, 60),
                                                           ("17", "Montana", 10, 3, 120)]}),
        # this season: Boise and Utah State in the Pac-12, Sac State in the MAC, Gone U no longer FBS
        (S, "level=3"): _tree({"Pac-12 Conference": [("68", "Boise State", 3, 1, 20), ("328", "Utah State", 2, 2, 0)],
                               "Mid-American Conference": [("16", "Sacramento State", 2, 2, -5)],
                               "Big 12 Conference": [("38", "Colorado", 3, 1, 30)]}),
        (S, "group=81"): _tree({"Big Sky Conference": [("17", "Montana", 3, 1, 40), ("999", "Gone U", 0, 4, -50)]}),
    }
    calls = []

    def fake(url, timeout=25):
        calls.append(url)
        for (season, q), d in trees.items():
            if f"season={season}&" in url and q in url:
                return d
        raise OSError("no tree")
    saved = racing._get_json
    keys = [("cfb_teams", s, 3, d) for s in (S, S - 1) for d in ("fbs", "fcs")]
    try:
        racing._get_json = fake
        for k in keys:
            racing._form_cache.pop(k, None)
        fbs = cfb.teams(S, "fbs")
        R = cfb.ratings(S, "fbs")
        Rf = cfb.ratings(S, "fcs")
        for k in keys:
            racing._form_cache.pop(k, None)
        del trees[(S, "level=3")]                    # this season's tree unreachable: last season's leagues
        fallback = cfb.teams(S, "fbs")
    finally:
        racing._get_json = saved
        for k in keys:
            racing._form_cache.pop(k, None)
    # the FCS prior is centred on THIS season's FCS schools (Montana; Sac State has left, Gone U has arrived from
    # FBS and is itself a mover), as ratings(div="fcs") centres it
    fcs_mean = 120 / 13
    ck("CFB1 a college season's leagues are THIS season's (Boise State and Utah State in the Pac-12, Colorado in the "
       "Big 12), its prior last season's record; a school new to the division keeps last season's record from the "
       "other division's tree and is rated on that division's scale less the FBS-FCS gap; a school that left the "
       "division leaves the table; no tree for this season keeps last season's leagues (the old behaviour)",
       fbs["68"]["conf"] == "Pac-12 Conference" and fbs["328"]["conf"] == "Pac-12 Conference"
       and fbs["38"]["conf"] == "Big 12 Conference" and (fbs["68"]["w"], fbs["68"]["l"]) == (9, 5)
       and fbs["16"]["conf"] == "Mid-American Conference" and fbs["16"]["prior_div"] == "fcs"
       and (fbs["16"]["w"], fbs["16"]["l"]) == (7, 5) and "999" not in fbs
       and abs(R["16"] - ((60 / 12 - fcs_mean) * (1 - cfb._REG) - cfb._DIV_GAP)) < 1e-9
       and "16" not in Rf and fallback["68"]["conf"] == "Mountain West Conference" and "16" not in fallback,
       f"{ {t: (r['conf'], r.get('prior_div')) for t, r in fbs.items()} } {R.get('16')}")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
