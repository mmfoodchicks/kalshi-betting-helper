"""Tennis player serve/return ratings from the Match Charting Project.

A tennis match is, to first order, two numbers: how often each player wins a point
on their own serve, and how often they win a point returning. From those two
numbers (per surface) the whole match -- games, sets, total games, who wins -- can
be derived analytically (see tennis_sim). So this module turns Jeff Sackmann's
Match Charting Project (point-by-point charted matches, current to within weeks)
into a per-player, surface-split, recency-weighted serve/return profile.

Like the UFC and MLB engines, it is recursive and dynamic: every profile is a
recency-weighted average of that player's charted matches (recent results weigh
far more), and thin samples are Bayesian-shrunk toward the player's overall rate
and then the tour average -- so a lightly-charted player defers to the field
instead of throwing a false edge, and a rising player's number climbs as new
matches land. Re-run and the board moves with the most recent results.
"""

import datetime
import gzip
import io
import urllib.request

import racing

_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master"
_HALF_LIFE_DAYS = 730.0          # a result 2 years old counts half as much
_K_OVERALL = 160.0               # shrink player overall toward tour avg (serve pts)
_K_SURFACE = 130.0               # shrink player surface toward their overall
_SURFACES = ("Hard", "Clay", "Grass")


def _fetch_csv(name):
    """GET a Match Charting CSV as text, gzip-requested to dodge proxy truncation
    on the larger stat files."""
    req = urllib.request.Request(f"{_BASE}/{name}", headers={
        "User-Agent": "vigil/1.0", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
        if "gzip" in (resp.headers.get("Content-Encoding") or "").lower():
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def _rows(text):
    import csv
    return list(csv.DictReader(io.StringIO(text)))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def _surface_of(raw):
    s = (raw or "").strip().lower()
    if "clay" in s:
        return "Clay"
    if "grass" in s:
        return "Grass"
    return "Hard"        # hard / carpet / indoor hard / unknown -> Hard bucket


def _shrink(num, denom, prior_rate, k):
    """Bayesian point estimate: observed rate pulled toward prior_rate, weighted by
    how many points actually back it (k 'prior points')."""
    return (num + k * prior_rate) / (denom + k) if (denom + k) > 0 else prior_rate


def _build(tour):
    """Build {norm_name: profile} for a tour ('m' or 'w'). A profile is
    {name, n, lg:{...}, overall:{spw,rpw,ace,df}, surf:{Hard:{...},...}}."""
    try:
        matches = _rows(_fetch_csv(f"charting-{tour}-matches.csv"))
        overview = _rows(_fetch_csv(f"charting-{tour}-stats-Overview.csv"))
    except Exception:
        return {}
    meta = {}
    for m in matches:
        meta[m["match_id"]] = {"date": (m.get("Date") or "").strip(),
                               "surface": _surface_of(m.get("Surface"))}
    today = datetime.date.today()

    def age_w(datestr):
        try:
            d = datetime.date(int(datestr[:4]), int(datestr[4:6]), int(datestr[6:8]))
            days = max(0, (today - d).days)
        except (ValueError, TypeError):
            days = _HALF_LIFE_DAYS
        return 0.5 ** (days / _HALF_LIFE_DAYS)

    # Tour-average rates (weighted), used as the shrink prior and the sim anchor.
    lg = {"spw_n": 0.0, "sp": 0.0, "rpw_n": 0.0, "rp": 0.0, "ace_n": 0.0}
    # Per player: overall + per surface weighted accumulators.
    acc = {}

    def blank():
        return {"spw_n": 0.0, "sp": 0.0, "rpw_n": 0.0, "rp": 0.0, "ace_n": 0.0,
                "df_n": 0.0, "w": 0.0}

    names = {}
    for r in overview:
        if (r.get("set") or "").strip() != "Total":
            continue
        mid = r.get("match_id")
        info = meta.get(mid)
        if not info:
            continue
        sp = _f(r.get("serve_pts"))
        rp = _f(r.get("return_pts"))
        if sp < 10:
            continue
        w = age_w(info["date"])
        spw = (_f(r.get("first_won")) + _f(r.get("second_won")))
        rpw = _f(r.get("return_pts_won"))
        ace = _f(r.get("aces"))
        df = _f(r.get("dfs"))
        nm = _norm(r.get("player"))
        if not nm:
            continue
        names.setdefault(nm, r.get("player"))
        # league
        lg["spw_n"] += w * spw; lg["sp"] += w * sp
        lg["rpw_n"] += w * rpw; lg["rp"] += w * rp
        lg["ace_n"] += w * ace
        a = acc.setdefault(nm, {"overall": blank(), "surf": {}})
        for bucket in (a["overall"], a["surf"].setdefault(info["surface"], blank())):
            bucket["spw_n"] += w * spw; bucket["sp"] += w * sp
            bucket["rpw_n"] += w * rpw; bucket["rp"] += w * rp
            bucket["ace_n"] += w * ace; bucket["df_n"] += w * df
            bucket["w"] += w

    if lg["sp"] <= 0:
        return {}
    LG_SPW = lg["spw_n"] / lg["sp"]
    LG_RPW = lg["rpw_n"] / lg["rp"]
    LG_ACE = lg["ace_n"] / lg["sp"]
    league = {"spw": round(LG_SPW, 4), "rpw": round(LG_RPW, 4), "ace": round(LG_ACE, 4)}

    out = {}
    for nm, a in acc.items():
        ov = a["overall"]
        o_spw = _shrink(ov["spw_n"], ov["sp"], LG_SPW, _K_OVERALL)
        o_rpw = _shrink(ov["rpw_n"], ov["rp"], LG_RPW, _K_OVERALL)
        o_ace = _shrink(ov["ace_n"], ov["sp"], LG_ACE, _K_OVERALL)
        o_df = _shrink(ov["df_n"], ov["sp"], 0.04, 60.0)
        surf = {}
        for s in _SURFACES:
            b = a["surf"].get(s)
            if b:
                surf[s] = {
                    "spw": round(_shrink(b["spw_n"], b["sp"], o_spw, _K_SURFACE), 4),
                    "rpw": round(_shrink(b["rpw_n"], b["rp"], o_rpw, _K_SURFACE), 4),
                    "ace": round(_shrink(b["ace_n"], b["sp"], o_ace, 80.0), 4),
                    "df": round(_shrink(b["df_n"], b["sp"], o_df, 60.0), 4),
                    "w": round(b["w"], 1)}
            else:                                   # no data on this surface -> overall
                surf[s] = {"spw": round(o_spw, 4), "rpw": round(o_rpw, 4),
                           "ace": round(o_ace, 4), "df": round(o_df, 4), "w": 0.0}
        out[nm] = {"name": names.get(nm, nm), "n": round(ov["w"], 1), "lg": league,
                   "overall": {"spw": round(o_spw, 4), "rpw": round(o_rpw, 4),
                               "ace": round(o_ace, 4), "df": round(o_df, 4)},
                   "surf": surf}
    out["__league__"] = {"name": "__league__", "lg": league}
    return out


def profiles(tour="m"):
    """{norm_name: profile} for a tour, cached a week (the charted-match set and a
    player's recency-weighted rate move slowly)."""
    return racing._cached(("tennis_prof", tour), 7 * 86400, lambda: _build(tour)) or {}


def league(tour="m"):
    p = profiles(tour)
    base = (p.get("__league__") or {}).get("lg")
    return base or {"spw": 0.642 if tour == "m" else 0.56,
                    "rpw": 0.358 if tour == "m" else 0.44, "ace": 0.07}


def resolve(name, tour="m", fuzzy=True):
    """Profile for a player name (exact-normalized, then unambiguous last-name),
    or None. `fuzzy=False` requires an exact full-name match -- used for ITF, where
    a last-name match could wrongly attach a charted ATP player to an unrelated
    lower-tier player and invent a false edge."""
    p = profiles(tour)
    if not p:
        return None
    nm = _norm(name)
    if nm in p:
        return p[nm]
    if not fuzzy:
        return None
    last = nm.split()[-1] if nm.split() else nm
    hits = [v for k, v in p.items() if k != "__league__" and k.split()[-1:] == [last]]
    if len(hits) == 1:
        return hits[0]
    # first-initial + last-name disambiguation (J Draper)
    parts = nm.split()
    if len(parts) >= 2:
        fi = parts[0][:1]
        hits = [v for k, v in p.items() if k != "__league__"
                and k.split()[-1:] == [last] and k[:1] == fi]
        if len(hits) == 1:
            return hits[0]
    return None


def match_rates(name, surface, tour="m", fuzzy=True):
    """(spw, rpw, ace, df, profile) for a player on a surface, or None. Falls back
    to overall when the surface bucket is empty. `fuzzy=False` (ITF) requires an
    exact name match to avoid false-attaching a charted player."""
    prof = resolve(name, tour, fuzzy=fuzzy)
    if not prof:
        return None
    s = (prof.get("surf") or {}).get(surface) or prof.get("overall")
    return s["spw"], s["rpw"], s["ace"], s["df"], prof
