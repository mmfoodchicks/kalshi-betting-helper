"""Subscription tiers + feature gating.

A single source of truth for what each tier unlocks, plus tiny helpers the
endpoints use to gate features and cap simulation sizes. Tiers are resolved per
request (cookie today; wire to real accounts/billing later) and ENFORCED on the
server, so the gating is real rather than cosmetic -- the UI selector just picks
which tier the server should apply.
"""

TIER_ORDER = ["free", "pro", "edge"]

TIERS = {
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
