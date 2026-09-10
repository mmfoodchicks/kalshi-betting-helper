"""Reproducible research randomness. Every research run derives its child
seeds from one base seed plus the coordinates of what is being simulated
(season, week, game, simulator mode), so two runs with the same base seed
draw the same worlds and a result can be repeated over several base seeds
to measure Monte Carlo uncertainty. Production's legacy simulator stays
unseeded (its stamp says so); this is for research artifacts, which stamp
every seed they used."""
import hashlib


def child_seed(base_seed, season=None, week=None, game=None, mode=None, extra=None):
    """A deterministic 63-bit seed from the base seed and the coordinates."""
    key = "|".join(str(x) for x in (int(base_seed), season, week, game, mode, extra))
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") & ((1 << 63) - 1)


def stamp(base_seed, **coords):
    """What to write into an artifact: the base seed, the coordinates and
    the derived child seed, so the draw can be reproduced from the record."""
    return {"base_seed": int(base_seed), "coords": {k: v for k, v in coords.items()},
            "child_seed": child_seed(base_seed, **coords)}
