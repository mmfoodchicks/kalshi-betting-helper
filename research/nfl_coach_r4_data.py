"""The extra nflverse fields the coaching engine's fourth revision needs beside R2's
rows (research/reports/nfl_coach_r4.md): air yards a pass, the players a play's
description says were injured, and the passers' names. RESEARCH. nflverse
play-by-play (CC BY 4.0), never committed; the row cache is gitignored.

THE SEAL is R1's (`nfl_coach_data.sealed`): a sealed row is skipped on its season,
week and season-type fields before any other field is read, unless `unseal` is
R4's read token (research/nfl_coach_r4_read.py, after its own checks); a sealed
read never writes the cache.
"""
import csv
import gzip
import os
import pickle
import re

from research import nfl_coach_data as CD

_UNSEAL_TOKEN = "nfl_coach_r4_read"
CACHE_VERSION = 1
# GSIS descriptions name an injured player as "J.Burrow was injured during the play."
_INJ = re.compile(r"([A-Z][A-Za-z'\.\- ]{1,30}?) was injured")


def cache_path(dirname, season):
    return os.path.join(dirname, f"nfl_coach_r4_extra_{int(season)}.pkl")


def load_extra(dirname, season, unseal=None, use_cache=True):
    """{"air": {(game id, play id): air yards}, "inj": {game id: [(play id, name)]},
    "names": {gsis id: "J.Burrow"}} for one season (unsealed rows only unless the
    read's token is passed)."""
    cp = cache_path(dirname, season)
    cacheable = unseal is None
    if cacheable and use_cache and os.path.exists(cp):
        with open(cp, "rb") as fh:
            d = pickle.load(fh)
        if d.get("v") == CACHE_VERSION:
            return d
    air, inj, names = {}, {}, {}
    with gzip.open(CD.path(dirname, season), "rt", newline="") as fh:
        rd = csv.reader(fh)
        hdr = next(rd)
        ix = {h: j for j, h in enumerate(hdr)}
        js, jw, jt = ix["season"], ix["week"], ix["season_type"]
        jg, jp, ja, jd = ix["game_id"], ix["play_id"], ix["air_yards"], ix["desc"]
        nm = [(ix["passer_player_id"], ix["passer_player_name"]), (ix["rusher_player_id"], ix["rusher_player_name"])]
        for raw in rd:
            if CD.sealed(raw[js], raw[jw], raw[jt]) and unseal != _UNSEAL_TOKEN:
                continue
            gid = raw[jg]
            try:
                pid = int(float(raw[jp]))
            except ValueError:
                continue
            a = raw[ja]
            if a not in ("", "NA"):
                try:
                    air[(gid, pid)] = int(float(a))
                except ValueError:
                    pass
            desc = raw[jd]
            if "injured" in desc:
                for m in _INJ.findall(desc):
                    inj.setdefault(gid, []).append((pid, m.strip()))
            for ji, jn in nm:
                if raw[ji] not in ("", "NA") and raw[jn] not in ("", "NA"):
                    names[raw[ji]] = raw[jn]
    d = {"v": CACHE_VERSION, "air": air, "inj": inj, "names": names}
    if cacheable and use_cache:
        with open(cp + ".tmp", "wb") as fh:
            pickle.dump(d, fh, protocol=4)
        os.replace(cp + ".tmp", cp)
    return d
