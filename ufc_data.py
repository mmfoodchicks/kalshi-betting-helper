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
import datetime

import racing

CORE = "http://sports.core.api.espn.com/v2/sports/mma/leagues/ufc"
ATH = "http://sports.core.api.espn.com/v2/sports/mma/athletes"
_MAX_FIGHTS = 8            # recent fights to aggregate per fighter
_LG = {"ss_pm": 4.0, "ss_absorbed_pm": 4.0, "td_p15": 1.3, "kd_p15": 0.25,
       "str_def": 0.55, "td_def": 0.65, "finish_rate": 0.45, "durability": 0.75}


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


def fighter_rating(fid, name=None):
    """Aggregated rating dict for one fighter, cached two weeks."""
    def build():
        try:
            el = _get(f"{ATH}/{fid}/eventlog?lang=en")
        except Exception:
            return None
        items = (el.get("events") or {}).get("items", [])[-14:]  # recent slice
        today = datetime.date.today().isoformat()

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
            return {"win": bool(me.get("winner")), "mins": mins,
                    "method": _method(stt.get("result")),
                    "ssl": ms.get("SSL", 0), "ssa": ms.get("SSA", 0),
                    "opp_ssl": os_.get("SSL", 0), "opp_ssa": os_.get("SSA", 0),
                    "tdl": ms.get("TDL", 0), "tda": ms.get("TDA", 0),
                    "opp_tdl": os_.get("TDL", 0), "opp_tda": os_.get("TDA", 0),
                    "kd": ms.get("KD", 0)}
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
        # Shrink rates toward league average by sample size (k pseudo-fights).
        # Generous so a 1-2 fight sample (a debut or short ESPN history) can't read
        # as elite — a prospect regresses hard toward the field.
        k = 4.5

        def sh(obs, prior, n=nf):
            return (n * obs + k * prior) / (n + k)
        return {
            "id": fid, "name": name, "fights": nf, "record_w": wins, "record_l": nf - wins,
            "ss_pm": round(sh(ssl / tot_min, _LG["ss_pm"]), 2),
            "ss_absorbed_pm": round(sh(opp_ssl / tot_min, _LG["ss_absorbed_pm"]), 2),
            "str_def": round(sh(1 - (opp_ssl / opp_ssa) if opp_ssa else _LG["str_def"], _LG["str_def"]), 3),
            "td_p15": round(sh(tdl / tot_min * 15, _LG["td_p15"]), 2),
            "td_def": round(sh(1 - (opp_tdl / opp_tda) if opp_tda else _LG["td_def"], _LG["td_def"]), 3),
            "kd_p15": round(sh(kd / tot_min * 15, _LG["kd_p15"]), 3),
            "finish_rate": round(sh(fin_for / nf, _LG["finish_rate"]), 3),
            "durability": round(sh(1 - fin_against / nf, _LG["durability"]), 3),
        }
    return racing._cached(("ufc_fighter", fid), 14 * 86400, build) or _default(fid, name, 0)


def _default(fid, name, n):
    """League-average rating for a fighter with no usable history (debut)."""
    return {"id": fid, "name": name, "fights": n, "record_w": 0, "record_l": 0,
            "ss_pm": _LG["ss_pm"], "ss_absorbed_pm": _LG["ss_absorbed_pm"],
            "str_def": _LG["str_def"], "td_p15": _LG["td_p15"], "td_def": _LG["td_def"],
            "kd_p15": _LG["kd_p15"], "finish_rate": _LG["finish_rate"],
            "durability": _LG["durability"]}


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
