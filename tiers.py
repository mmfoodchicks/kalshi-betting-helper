"""Subscription tiers + feature gating (FRAMEWORK ONLY for now).

A single source of truth for what each tier could unlock, plus helpers the
endpoints use to gate features and cap simulation/leg sizes. This is wired up
but NOT enforced by default: until you flip TIERS_ENFORCED=1 in the environment,
every request resolves to the unlimited "owner" tier, so you (the owner) are
never accidentally gated. When you do start charging, set TIERS_ENFORCED=1 and
resolve the real tier from the logged-in account; set OWNER_KEY (and a matching
owner_key cookie) to keep yourself as owner/god even with enforcement on.
"""

import os

# Is paid gating active? Off by default -> everyone is treated as owner (god).
ENFORCED = os.environ.get("TIERS_ENFORCED", "").strip().lower() in ("1", "true", "yes", "on")
# Secret that grants owner/god access even when enforcement is on.
OWNER_KEY = os.environ.get("OWNER_KEY")

TIER_ORDER = ["free", "pro", "edge", "owner"]

TIERS = {
    "owner": {
        "label": "Owner (God)",
        "price": "-",
        "blurb": "Full unlimited access - that's you.",
        "max_sims": 200000,
        "max_combo_legs": 30,
    },
    "free": {
        "label": "Free",
        "price": "$0",
        "blurb": "Live odds & signals to get started.",
        "max_sims": 1000,
        "max_combo_legs": 3,
    },
    "pro": {
        "label": "Pro",
        "price": "$12/mo",
        "blurb": "Parlays, DFS, racing edges & deeper sims.",
        "max_sims": 10000,
        "max_combo_legs": 8,
    },
    "edge": {
        "label": "Edge",
        "price": "$29/mo",
        "blurb": "Everything: max sims, backtests & quant tools.",
        "max_sims": 15000,
        "max_combo_legs": 12,
    },
}

# Minimum tier required for each gated feature.
FEATURE_MIN = {
    "same_game_parlay": "pro",
    "mixed_parlay": "pro",
    "dfs": "pro",
    "racing_picks": "pro",
    "vol_edge": "pro",
    "deribit": "pro",
    "backtest": "edge",
    "recorder_backtest": "edge",
}

# Sim-run options offered in the UI; each tagged with the tier needed to use it.
SIM_RUN_OPTIONS = [100, 500, 1000, 5000, 10000, 15000]


def normalize(tier):
    return tier if tier in TIERS else "free"


def resolve(tier_cookie=None, owner_cookie=None):
    """The effective tier for a request. Until enforcement is turned on, this is
    always 'owner' (god mode) so the owner is never accidentally gated."""
    if not ENFORCED:
        return "owner"
    if owner_cookie and OWNER_KEY and owner_cookie == OWNER_KEY:
        return "owner"
    return normalize(tier_cookie or "free")


def rank(tier):
    return TIER_ORDER.index(normalize(tier))


def has_feature(tier, feature):
    """True if `tier` unlocks `feature`. Unknown features default to allowed."""
    need = FEATURE_MIN.get(feature)
    return True if need is None else rank(tier) >= rank(need)


def feature_tier(feature):
    return FEATURE_MIN.get(feature)


def cap_sims(tier, n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 1000
    return max(100, min(n, TIERS[normalize(tier)]["max_sims"]))


def cap_legs(tier, n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 2
    return max(2, min(n, TIERS[normalize(tier)]["max_combo_legs"]))


def public(current="free"):
    """JSON-serializable tier matrix for the UI."""
    current = normalize(current)
    return {
        "current": current,
        "order": TIER_ORDER,
        "tiers": {
            k: {
                "label": v["label"], "price": v["price"], "blurb": v["blurb"],
                "max_sims": v["max_sims"], "max_combo_legs": v["max_combo_legs"],
                "features": sorted(f for f, need in FEATURE_MIN.items()
                                   if rank(k) >= rank(need)),
            }
            for k, v in TIERS.items()
        },
        "feature_min": FEATURE_MIN,
        "sim_run_options": SIM_RUN_OPTIONS,
    }
