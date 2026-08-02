"""Build our OWN UFC fighter ratings from primary fight history (ESPN core API).

No accessible MMA API hands us striking/grappling rates, so we assemble them
ourselves. For each fighter we pull their fight log and, bout by bout, read the
per-fight box score (significant strikes landed + absorbed, takedowns, knockdowns,
control time) together with the result (win/loss, method, round). Aggregated over
recent fights that becomes a rating: offence per minute, striking defence, the
takedown game, finish power and durability -- the inputs the fight simulator and
the DFS optimizer need.

Heavy on I/O (every fight is a few API calls), so fighter ratings are cached for
two weeks -- a fighter's history only changes when they next compete.
"""

import concurrent.futures as _cf
import clock
import re

import racing

CORE = "http://sports.core.api.espn.com/v2/sports/mma/leagues/ufc"
ATH = "http://sports.core.api.espn.com/v2/sports/mma/athletes"
_MAX_FIGHTS = 8            # recent fights to aggregate per fighter
_LG = {"ss_pm": 4.0, "ss_absorbed_pm": 4.0, "td_p15": 1.3, "kd_p15": 0.25,
       "str_def": 0.55, "td_def": 0.65, "finish_rate": 0.45, "durability": 0.75}
# Strength of schedule: opponents' average career win rate, and how hard it swings
# the box-score OUTPUT rates. Beating up on winning fighters is real; padding stats
# on cans is not, so numbers built vs a weak schedule regress toward league.
_LG_OPP_WR = 0.50
_SOS_K = 0.7


def _get(u):
    return racing._get_json(u)


def _stat_map(stat_obj):
    out = {}
    for c in ((stat_obj or {}).get("splits", {}) or {}).get("categories", []):
        for x in c.get("stats", []):
            ab = (x.get("abbreviation") or "").strip()
            if ab:
                try:
                    out[ab] = float(x.get("value", 0.0))
                except (TypeError, ValueError):
                    pass
    return out


def _minutes(period, display_clock, fmt_periods):
    """Fight length in minutes from the round it ended in + time elapsed in that
    round (ESPN displayClock is elapsed, M:SS); decisions go the distance."""
    per = int(period or fmt_periods or 3)
    elapsed = 300.0
    if display_clock and ":" in str(display_clock):
        try:
            m, s = str(display_clock).split(":")
            elapsed = int(m) * 60 + int(s)
        except ValueError:
            pass
    return max(1.0, (per - 1) * 5 + elapsed / 60.0)


def _method(result):
    txt = ((result or {}).get("type") or {}).get("name") or \
        (result or {}).get("displayName") or (result or {}).get("name") or ""
    t = txt.lower()
    if "sub" in t:
        return "sub"
    if "ko" in t or "tko" in t or "knockout" in t:
        return "ko"
    return "dec"


def fighter_rating(fid, name=None, as_of=None):
    """Aggregated rating dict for one fighter, cached two weeks.

    `as_of` (YYYY-MM-DD) makes the rating POINT-IN-TIME: only fights that had
    already happened before that date are used. That's what lets us replay a past
    card honestly — rating a fighter for a 2025 bout must not see the 2025 bout
    itself, or anything after it, or a backtest just measures hindsight. Default
    (None) = today, i.e. the normal live rating."""
    def build():
        try:
            el = _get(f"{ATH}/{fid}/eventlog?lang=en")
        except Exception:
            return None
        # ESPN's eventlog is NOT reliably chronological — the newest bout can sit
        # at index 0 with the rest descending, so slicing the tail (the old
        # items[-14:]) silently rated fighters on their OLDEST fights and ignored
        # the last decade. Take a generous window and let the date sort below pick
        # the genuinely most recent fights. Replaying the past needs the wider
        # window anyway, since recent bouts are filtered out by the cutoff.
        items = (el.get("events") or {}).get("items", [])[-60:]
        today = as_of or clock.today_et().isoformat()

        def fetch_comp(it):
            ref = (it.get("competition") or {}).get("$ref") or it.get("$ref")
            try:
                return _get(ref)
            except Exception:
                return None
        with _cf.ThreadPoolExecutor(max_workers=6) as ex:
            comps = [c for c in ex.map(fetch_comp, items) if c]
        # completed fights only, newest first
        done = [c for c in comps if (c.get("date") or "")[:10] < today
                and len(c.get("competitors") or []) == 2]
        done.sort(key=lambda c: c.get("date") or "", reverse=True)
        done = done[:_MAX_FIGHTS]

        def detail(comp):
            cs = comp["competitors"]
            me = next((c for c in cs if f"/{fid}" in ((c.get("athlete") or {}).get("$ref") or "")), None)
            opp = next((c for c in cs if c is not me), None)
            if not me or not opp:
                return None
            fmt = ((comp.get("format") or {}).get("regulation") or {}).get("periods", 3)
            try:
                ms = _stat_map(_get(me["statistics"]["$ref"]))
                os_ = _stat_map(_get(opp["statistics"]["$ref"]))
                stt = _get(comp["status"]["$ref"])
            except Exception:
                return None
            mins = _minutes(stt.get("period"), stt.get("displayClock"), fmt)
            om = re.search(r"/athletes/(\d+)", (opp.get("athlete") or {}).get("$ref") or "")
            return {"win": bool(me.get("winner")), "mins": mins,
                    "method": _method(stt.get("result")),
                    "ssl": ms.get("SSL", 0), "ssa": ms.get("SSA", 0),
                    "opp_ssl": os_.get("SSL", 0), "opp_ssa": os_.get("SSA", 0),
                    "tdl": ms.get("TDL", 0), "tda": ms.get("TDA", 0),
                    "opp_tdl": os_.get("TDL", 0), "opp_tda": os_.get("TDA", 0),
                    "kd": ms.get("KD", 0), "opp_id": om.group(1) if om else None}
        with _cf.ThreadPoolExecutor(max_workers=6) as ex:
            fights = [f for f in ex.map(detail, done) if f]
        if not fights:
            return _default(fid, name, 0)

        tot_min = sum(f["mins"] for f in fights) or 1.0
        ssl = sum(f["ssl"] for f in fights); ssa = sum(f["ssa"] for f in fights)
        opp_ssl = sum(f["opp_ssl"] for f in fights); opp_ssa = sum(f["opp_ssa"] for f in fights)
        tdl = sum(f["tdl"] for f in fights)
        opp_tda = sum(f["opp_tda"] for f in fights); opp_tdl = sum(f["opp_tdl"] for f in fights)
        kd = sum(f["kd"] for f in fights)
        wins = sum(1 for f in fights if f["win"])
        fin_for = sum(1 for f in fights if f["win"] and f["method"] in ("ko", "sub"))
        fin_against = sum(1 for f in fights if not f["win"] and f["method"] in ("ko", "sub"))
        nf = len(fights)
        # Strength of schedule: each recent opponent's career win rate (cached), a
        # level-of-competition proxy. A tough schedule earns the output rates trust
        # (and a small boost); a weak one regresses them toward league.
        def _opp_wr(f):
            oid = f.get("opp_id")
            if not oid:
                return None
            try:
                p = career_profile(oid)
                return p.get("win_rate") if p else None
            except Exception:
                return None
        try:
            with _cf.ThreadPoolExecutor(max_workers=6) as ex:
                oqs = [q for q in ex.map(_opp_wr, fights) if q is not None]
        except Exception:
            oqs = []
        sos = sum(oqs) / len(oqs) if oqs else _LG_OPP_WR
        sos_mult = max(0.88, min(1.12, 1 + _SOS_K * (sos - _LG_OPP_WR)))
        prof = career_profile(fid)
        # Finish rate & durability: the career record (incl. pre-UFC fights) is a far
        # bigger, more stable sample than the few UFC bouts we box-score, so prefer it
        # when present (it subsumes the UFC fights anyway), shrunk by its own size.
        if prof:
            fr_obs, fr_n = prof["finish_rate"], prof["fights"]
            du_obs, du_n = prof["durability"], prof["fights"]
        else:
            fr_obs, fr_n = (fin_for / nf), nf
            du_obs, du_n = (1 - fin_against / nf), nf
        return {
            "id": fid, "name": name, "fights": nf, "record_w": wins, "record_l": nf - wins,
            # Output rates get the strength-of-schedule multiplier; defense and the
            # career-based finish/durability are left on their own (cleaner signal).
            "ss_pm": round(_shrink(ssl / tot_min, _LG["ss_pm"], nf) * sos_mult, 2),
            "ss_absorbed_pm": round(_shrink(opp_ssl / tot_min, _LG["ss_absorbed_pm"], nf), 2),
            "str_def": round(_shrink(1 - (opp_ssl / opp_ssa) if opp_ssa else _LG["str_def"], _LG["str_def"], nf), 3),
            "td_p15": round(_shrink(tdl / tot_min * 15, _LG["td_p15"], nf) * sos_mult, 2),
            "td_def": round(_shrink(1 - (opp_tdl / opp_tda) if opp_tda else _LG["td_def"], _LG["td_def"], nf), 3),
            "kd_p15": round(_shrink(kd / tot_min * 15, _LG["kd_p15"], nf) * sos_mult, 3),
            "finish_rate": round(_shrink(fr_obs, _LG["finish_rate"], fr_n), 3),
            "durability": round(_shrink(du_obs, _LG["durability"], du_n), 3),
            "sos": round(sos, 3),
            "career": prof,
        }
    # as_of is part of the key: a point-in-time rating must never be served from
    # (or overwrite) the live one.
    return racing._cached(("ufc_fighter", fid, as_of or ""), 14 * 86400, build) \
        or _default(fid, name, 0)


_K = 4.5   # pseudo-fights of shrinkage toward the prior (a thin sample regresses hard)


def _shrink(obs, prior, n):
    return (n * obs + _K * prior) / (n + _K)


def career_profile(fid):
    """Full PRO MMA record (including pre-UFC / regional fights) from ESPN's records
    endpoint -- this exists even for UFC debutants, so it's our only read on a
    fighter who has never set foot in the Octagon. Returns the record, the
    finish/decision breakdown, a finish rate and a durability proxy, or None if ESPN
    has no record for them. Cached two weeks (a record only changes when they fight).

    Caveat worth keeping in mind: regional/other-promotion competition is softer than
    the UFC, so a glossy pre-UFC record is a weaker signal than the same line in the
    UFC -- like reading college stats for an NFL rookie. We use it, but shrink it."""
    def build():
        try:
            rec = _get(f"{ATH}/{fid}/records?lang=en&region=us")
        except Exception:
            return None
        it = next((x for x in (rec.get("items") or [])
                   if x and x.get("type") == "total"), None)
        if not it:
            return None
        st = {s.get("type"): (s.get("value") or 0) for s in it.get("stats", [])}
        w = int(st.get("wins", 0)); l = int(st.get("losses", 0))
        n = w + l
        if n <= 0:
            return None
        ko = int(st.get("tkos", 0)); sub = int(st.get("submissions", 0))
        kol = int(st.get("tkolosses", 0)); subl = int(st.get("submissionlosses", 0))
        fin_for, fin_against = ko + sub, kol + subl
        return {
            "record": it.get("summary") or f"{w}-{l}",
            "w": w, "l": l, "fights": n,
            "ko_wins": ko, "sub_wins": sub, "dec_wins": max(0, w - fin_for),
            "finish_rate": round(fin_for / n, 3),
            "durability": round(1 - fin_against / n, 3),
            "win_rate": round(w / n, 3),
            "title_fights": int(st.get("titlewins", 0)) + int(st.get("titlelosses", 0)) + int(st.get("titledraws", 0)),
        }
    return racing._cached(("ufc_career", fid), 14 * 86400, build)


def _default(fid, name, n):
    """Best estimate for a fighter with no UFC box-score history. We have no UFC
    striking/grappling rates for them, but we DO pull their pro career record (incl.
    pre-UFC fights) and seed finish rate + durability from it, so a 19-3 regional
    finisher no longer reads identically to a winless unknown."""
    d = {"id": fid, "name": name, "fights": n, "record_w": 0, "record_l": 0,
         "ss_pm": _LG["ss_pm"], "ss_absorbed_pm": _LG["ss_absorbed_pm"],
         "str_def": _LG["str_def"], "td_p15": _LG["td_p15"], "td_def": _LG["td_def"],
         "kd_p15": _LG["kd_p15"], "finish_rate": _LG["finish_rate"],
         "durability": _LG["durability"], "career": None}
    try:
        prof = career_profile(fid)
    except Exception:
        prof = None
    if prof:
        d["career"] = prof
        d["finish_rate"] = round(_shrink(prof["finish_rate"], _LG["finish_rate"], prof["fights"]), 3)
        d["durability"] = round(_shrink(prof["durability"], _LG["durability"], prof["fights"]), 3)
    return d


# How each rate component feeds the single 0-100 power rating (sums to 1).
_RW = {"ss_pm": 0.22, "str_def": 0.18, "kd_p15": 0.16, "finish_rate": 0.12,
       "td_p15": 0.12, "td_def": 0.10, "durability": 0.10}


def power_rating(r):
    """A single 0-100 fighter rating (league average == 50). NOT an Elo -- we have
    no head-to-head result graph for UFC -- but a normalized blend of striking,
    grappling, finishing and durability versus the league baseline. A fighter with
    no usable history sits at exactly 50 (we genuinely don't know them yet)."""
    score = 50.0
    for k, w in _RW.items():
        base = _LG[k] or 1.0
        ratio = (r.get(k, base) or 0.0) / base
        score += w * 50.0 * (min(2.0, max(0.0, ratio)) - 1.0)   # ratio 1->+0, 2->+50w
    # Career nudge: a proven winner with real experience gets credit beyond the rate
    # components (which, for a debut, are mostly league-average placeholders). Kept
    # small and capped -- regional wins are softer than UFC wins, so it complements
    # the rate model rather than overwhelming it.
    c = r.get("career")
    if c and c.get("fights", 0) >= 3:
        score += max(-6.0, min(8.0, (c["win_rate"] - 0.5) * 16))     # 100% W -> +8, 50% -> 0
        score += min(4.0, c["fights"] / 25.0 * 4.0)                  # deep experience -> +4
    return int(round(max(1, min(99, score))))


def rating_components(r):
    """Per-area 0-100 sub-ratings (league average 50) for display."""
    def sub(k):
        base = _LG[k] or 1.0
        ratio = (r.get(k, base) or 0.0) / base
        return int(round(max(1, min(99, 50 + 50 * (min(2.0, max(0.0, ratio)) - 1.0)))))
    return {"striking": sub("ss_pm"), "str_def": sub("str_def"),
            "takedowns": sub("td_p15"), "td_def": sub("td_def"),
            "power": sub("kd_p15"), "finishing": sub("finish_rate"),
            "durability": sub("durability")}


def _athlete_name(ref):
    try:
        return _get(ref).get("displayName")
    except Exception:
        return None


def card():
    """The upcoming UFC card as [{a_id,a_name,b_id,b_name,weight,rounds}]."""
    def build():
        d = _get(f"{CORE}/events?limit=3")
        items = d.get("items", [])
        if not items:
            return None
        ev = _get(items[0]["$ref"])
        bouts = []
        for comp in ev.get("competitions", []):
            cs = comp.get("competitors", [])
            if len(cs) != 2:
                continue
            ids, names = [], []
            for c in cs:
                ref = (c.get("athlete") or {}).get("$ref") or ""
                fid = ref.split("/athletes/")[-1].split("?")[0] if ref else None
                ids.append(fid)
                names.append(_athlete_name(ref))
            if not all(ids):
                continue
            rounds = ((comp.get("format") or {}).get("regulation") or {}).get("periods", 3)
            bouts.append({"a_id": ids[0], "a_name": names[0], "b_id": ids[1],
                          "b_name": names[1], "weight": (comp.get("type") or {}).get("text"),
                          "rounds": rounds})
        return {"event": ev.get("name"), "date": (ev.get("date") or "")[:10], "bouts": bouts}
    return racing._cached(("ufc_card",), 6 * 3600, build)


def fighter_bio(fid):
    """Age / reach / stance from the ESPN athlete record, cached a week. Age is
    the most reliable decline signal in MMA (the chin goes first, then output);
    reach shapes the striking exchanges."""
    def build():
        try:
            d = _get(f"{ATH}/{fid}?lang=en")
        except Exception:
            return None
        return {"age": d.get("age"), "reach": d.get("reach"),
                "height": d.get("height"),
                "stance": ((d.get("stance") or {}).get("text") or "").lower()}
    return racing._cached(("ufc_bio", fid), 7 * 86400, build)
