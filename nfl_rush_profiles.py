"""As-of RUSHING profiles for the NFL matchup engine: running backs (rushing
and receiving), quarterback rushing decomposed, the defense against both, the
offensive line, and the team's run context -- what each had SHOWN before a
given week, from the agreed compact play store (nfl_matchup_data) and the
auxiliary stores (nfl_rush_data), nothing from that week or later in them.

The as-of machinery is the agreed one (nfl_matchup_profiles): a Snapshot as of
(season, week) holds that season's games with week < cutoff at weight 1 and
whole prior seasons at DECAY ** (seasons back), no more than BACK seasons
back; the tallies record every (season, week) they hold and the constructor
refuses any at or past the cutoff. The ONE thing a snapshot reads from the
cutoff week itself is that week's injury report (`status`, the OL and RB-room
absences), which the league publishes before kickoff.

Every profile field is a dict
    {"value", "unit", "kind", "n", "basis", "metric", "raw", "k", "prior", "reliability"}
- value: the empirical-Bayes figure (sum + k x prior) / (n + k) when the field
  is shrunk, else the raw ratio. kind "modelled" when shrunk, "proxy" when
  the quantity stands in for something the data does not chart, else
  "observed".
- unit: "share" (a probability or a share, drawn as a percent) or a named
  unit ("yds_per_carry", "per_game", "epa_per_play", "ratio", ...).
- k: the measured split-half k of THAT quantity (research/data/nfl_rush/
  reliability.json, `_K_DEFAULT` below until it exists): k = n (1 - r) / r,
  r the odd-vs-even-week correlation, n the mean denominator of a half. A
  quantity whose r <= 0.02 has k None: its value IS the prior and its
  reliability reads 0 (the data does not support it). reliability = n / (n + k).
- Splits (direction x gap, box, formation, personnel faced, scramble
  conditions) shrink toward a prior built from the unit's OWN overall figure
  moved by the league's split offset (additive for yards / EPA / success /
  stuffs / explosives, multiplicative for scramble rates) -- "base" in the
  field -- with the k measured on exactly that departure.

Scopes (the agreed F1 lesson): a usage share (carry share, snap share, red-zone
share, situational shares, targets per on-field dropback) is TEAM-SCOPED when
a team is given -- numerator and denominator from the same games for that
team, and for the on-field rates from the same plays. Efficiency (YPC,
success, YBC / YAC, receiving efficiency, QB rushing efficiency) pools every
team the player played for: it is his.

What the data supports and what it does not (every proxy is labelled where it
is used):
- Run DIRECTION is nflfastR's run_location x run_gap (left / right x end /
  tackle / guard, and middle, whose gap is blank by convention): 0.01-0.05%
  of RB designed runs a season have no location (report §4.4). "Edge" = the end gap, "interior" = guard
  and middle. Run CONCEPTS (zone, gap, power, counter, duo), fronts as a
  scheme (4-3 / 3-4, odd / even, over / under), alignments, gap integrity,
  spies and read-option identification are in NO free source.
- The front PROXY is the DL count of participation's defense personnel (3 or
  fewer / 4 / 5+); it counts bodies, not alignment, and it ends at 2025.
- Box, defense personnel (base / nickel / dime) and offense personnel come
  from participation (2016-2025) -- 2026 reads them from prior seasons only;
  the box falls back to FTN's count (the same charting from 2023, measured).
- Pressure on a scramble and the coverage it beat are charted from 2023 only
  (2021-22 scrambles carry neither), so every scramble split by pressure,
  man / zone or shell reads 2023+ dropbacks only (SCR_CHART_ERA); the blitz
  split reads FTN's rusher count, 2022+. 2026 has no pressure or coverage at
  all; PFR's per-game pressure counts stand in, labelled proxy.
- "Containment" is a PROXY: scramble yards per pressured dropback allowed,
  observed over what the QBs faced would have been expected to gain.
- QB designed runs exclude scrambles, kneels, spikes, aborted snaps and
  sneaks; sneaks are FTN's is_qb_sneak where FTN charted the play (2022+),
  else a written rule (sneak_rule); RPO keeps are FTN's is_rpo on a QB
  designed run -- an RPO that becomes a pass is a pass. Read-option keeps
  are NOT identifiable. Sacks are never rushing; kneels are (NFL scoring).
- PFR's yards before / after contact and broken tackles are per player-game;
  NGS's RYOE exists only for a back's games with 10+ rushes (labelled).
"""
import json
import os

import nfl_matchup_data as D
import nfl_matchup_profiles as P
import nfl_rush_data as R

# A season back counts 0.75, two back 0.5625. Chosen by the agreed rule, written
# before the run (research/nfl_rush_dataset.py): the pooled year-over-year r of
# ten rushing tendencies read 0.602 (0.600 without the 2024->2025 pair) -- on
# the 0.6 boundary; the team tendencies alone read 0.535 (they would take 0.5),
# the QB / RB traits 0.701 (research/reports/nfl_rush_data.md §5.3). The agreed
# passing profiles keep their own 0.5.
DECAY = 0.75
BACK = 2
RECENT = 4
SCR_CHART_ERA = 2023  # participation charts pressure / coverage on scrambles from 2023 (0 of 910 in 2021, 0 of 905 in 2022)
FTN_ERA = 2022
LEAD = 8             # "leading" / "trailing": by more than one score
RELIABILITY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", "data", "nfl_rush",
                                "reliability.json")
_k_path = RELIABILITY_PATH

# ---- codes ------------------------------------------------------------------------
DIRS = ("", "left_end", "left_tackle", "left_guard", "middle", "right_guard", "right_tackle", "right_end")
SIDES = ("edge", "tackle", "interior")
SIDE_OF = {1: "edge", 7: "edge", 2: "tackle", 6: "tackle", 3: "interior", 4: "interior", 5: "interior"}
BOX = ("", "light", "neutral", "heavy")
DPERS = ("", "base", "nickel", "dime")
OPERS = ("", "11", "12", "21", "13", "22", "10", "other")
RPOS = ("RB", "QB", "QBS", "WR", "TE", "UNK")     # QBS = a QB sneak, kept apart from QB designed runs
SHELL = {"single_high": "single_high", "two_high": "two_high"}


def dir_ix(rloc, rgap):
    """run_location x run_gap -> DIRS index (0 = not charted)."""
    if rloc == 2:
        return 4
    if rloc == 1:
        return {1: 1, 2: 2, 3: 3}.get(rgap, 0)
    if rloc == 3:
        return {3: 5, 2: 6, 1: 7}.get(rgap, 0)
    return 0


def box_ix(box):
    b = P.box_bucket(box if box is not None and box > 0 else None)
    return {"light": 1, "neutral": 2, "heavy": 3}.get(b, 0)


def dpers_ix(ndb):
    if ndb is None or ndb < 0:
        return 0
    return 1 if ndb <= 4 else 2 if ndb == 5 else 3


def opers_ix(code):
    if code == D.PERS_UNKNOWN:
        return 0
    return {11: 1, 12: 2, 21: 3, 13: 4, 22: 5, 10: 6}.get(code, 7)


def line_yards(y):
    """Line yards for one carry, by this module's stated CONVENTION: a loss
    counts 120%, yards 0-4 100%, yards 5-10 50%, nothing past 10 -- so it caps at
    7.0 (4 yards -> 4.0; 7 -> 5.5; 10 -> 7.0; -2 -> -2.4). It is the commonly
    quoted Football Outsiders schedule, but the page that defines it could not
    be reached (research/reports/rush_data_sources.md §4: HTTP 404, and a
    secondary source quotes a different schedule), so it is NOT FO's official
    (adjusted) line yards: no opponent or situation adjustment. Computed on
    RB/FB designed runs only (never QB runs, scrambles, kneels or aborted snaps)."""
    if y < 0:
        return 1.2 * y
    if y <= 4:
        return float(y)
    if y <= 10:
        return 4.0 + 0.5 * (y - 4)
    return 7.0


def sneak_rule(sg, ytg, rloc, rgap):
    """The rule-coded QB sneak, used ONLY where FTN has no row for the play (FTN
    starts in 2022, so this reaches 2021 -- a 2023 cutoff's two-seasons-back
    prior -- and the ~0.3% of 2022 plays FTN missed). FTN's is_qb_sneak is the
    only charted sneak flag (no pbp field or play description says "sneak",
    rush_data_sources.md §8.1). The rule: a QB designed run from under center
    with 1-2 yards to go, up the middle or through a guard gap. Its agreement
    with FTN on 2022-2025 is in research/reports/nfl_rush_data.md; every sneak
    count says how many came from the rule (`counts`)."""
    return sg == 0 and 0 < ytg <= 2 and (rloc == 2 or rgap == 3)


# ---- per-game line layouts ---------------------------------------------------------------
SITS = ("early", "third", "sy", "rz", "gl", "2min", "neutral", "lead", "trail")


def _layout(names):
    return {n: i for i, n in enumerate(names)}, len(names)


# player line: one per player per game (team-scoped by construction: one record per offence)
PL_NAMES = (
    "rush", "rush_yds", "rush_td",                       # official rushes: designed + aborted + scramble + kneel
    "des", "des_yds", "des_td", "des_succ", "des_fd", "des_x10", "des_x20", "des_stuff", "des_epa",  # designed (incl. sneaks)
    "sneak", "sneak_yds", "rpo", "rpo_yds",
    "scr", "scr_yds", "kneel", "kneel_yds", "abort", "abort_yds",
    *[f"des_{s}" for s in SITS], "des_gl_td", "des_sy_conv",
    "tgt", "rec", "rec_yds", "rec_td", *[f"tgt_{s}" for s in SITS],
    "air_sum", "air_n", "yac_sum", "ftn_tgt", "screen_tgt", "chk_tgt", "tgt_man", "yds_man", "tgt_zone", "yds_zone",
    "fuml",
    "of_snaps", "of_db", "of_run", "of_man", "of_zone", "of_tgt", "of_tgt_man", "of_tgt_zone",
    *[f"sit_{s}" for s in R.SIT],
    "pfr_snaps", "pfr_team_snaps", "pfr_st_snaps",
    "pfr_car", "pfr_ybc", "pfr_yac", "pfr_btk", "pfr_rbtk",
    "ngs_games", "ngs_att", "ngs_yds", "ngs_exp", "ngs_pct8_att", "ngs_ttlos_att",
    "pos_rb",                                            # 1 when the player is an RB/FB (players.csv), per game
    "app",                                               # 1 when pbp or participation shows him in the game (see _close_game)
)
PL, PL_W = _layout(PL_NAMES)
_ZERO_PL = [0.0] * PL_W
# team line: the offence's game
TL_NAMES = (
    "snaps", "db", "des", "des_yds", "rb_des", "rb_des_yds", "qb_des", "qb_sneak",
    "abort", "tgt", "rb_tgt", "rb_rec", "rb_recy", "rb_yac", "rb_air_sum", "rb_air_n",
    *[f"des_{s}" for s in SITS], *[f"db_{s}" for s in SITS], *[f"tgt_{s}" for s in SITS],
    "ftn_plays", "rpo_plays", "rpo_runs", "rpo_qb_keeps", "rpo_passes",
    *[f"sit_{s}" for s in R.SIT],
    "pfr_team_snaps", "rb_line_yds", "rb_stuff", "rb_succ", "rb_pfr_car", "rb_pfr_ybc", "rb_pfr_yac", "rb_pfr_btk",
)
TL, TL_W = _layout(TL_NAMES)
# QB line: one per QB per game -- his dropbacks (as passer or scrambler) and every rush
QL_NAMES = (
    "db", "att", "sack", "scr", "scr_yds", "scr_td", "scr_x10", "scr_fd",
    "dsg", "dsg_yds", "dsg_td", "dsg_x10", "dsg_succ", "dsg_epa",          # designed runs, NOT sneaks
    "sneak", "sneak_yds", "sneak_td", "sneak_sy", "sneak_sy_conv", "rpo", "rpo_yds",
    "kneel", "kneel_yds", "abort", "abort_yds", "rush_td", "rz_rush", "gl_rush", "x10_rush",
    "press_n", "press", "scr_press", "scr_nopress", "scr_yds_press", "blitz_n", "blitz", "scr_blitz", "scr_noblitz",
    "mz_n", "man", "scr_man", "scr_zone", "fam_n", "single", "scr_single", "two", "scr_two",
    "expo", "pfr_pressured", "pfr_hurried", "pfr_hit", "pfr_blitzed", "pfr_sacked", "pfr_rows",
    "hs",                                                # sacks + QB hits that were not sacks (pbp qb_hit): the non-PFR pressure proxy
)
QL, QL_W = _layout(QL_NAMES)
# designed-run value vector (tallies keyed by direction / box / personnel / formation / rusher)
RV_NAMES = ("n", "yds", "succ", "epa", "stuff", "x10", "x20", "td", "fd", "ly", "sy", "sy_conv", "rz", "gl")
RV, RV_W = _layout(RV_NAMES)
# what the defense lines up in, every snap (keyed by offence personnel / shotgun / designed run)
BV_NAMES = ("n", "box_n", "box_sum", "light", "neutral", "heavy", "pers_n", "base", "nickel", "dime",
            "dl_n", "dl3", "dl4", "dl5")
BV, BV_W = _layout(BV_NAMES)
# the defense's game extras: PFR defender sums (nfl_rush_data.PFR_DEF_FIELDS order)
DP_NAMES = ("defenders", "tackles", "missed", "pressures", "hurries", "hits", "sacks", "blitzes")


# ---- the k table -------------------------------------------------------------------------
# Measured 2026-10-23 on 2018-2025 (research/data/nfl_rush/reliability.json:
# odd vs even weeks, 256 team-seasons, 284-528 RB-seasons by gate -- RB/FB
# only since the review's B1, which also took 2024's 92 WR and 14 TE carriers
# out --, 282 QB-seasons; research/nfl_rush_dataset.py measure). The profile reads the json
# and falls back to these, copied from it (a guard holds the two equal). None =
# the split-half r was <= 0.02 or fewer than 6 units: no signal; the profile
# keeps the prior. Every metric the profiles stamp is in the table (guarded).
# What they say, in one line each: a defense's run-DIRECTION, side and box
# splits of what it allows are noise (r -0.05 to 0.04); so are a back's
# direction / box / personnel / formation efficiency splits (r < 0.1); a
# back's direction SHARES are a real tendency (r 0.43, k 107 carries); usage
# shares are known within a few games (carry share r 0.91, k 18 -- 0.77 / k 54
# on weeks 1-9 vs 10-18: odd / even flatters usage); his catch rate is
# noise (r 0.03, k 877 targets) where his aDOT is not (r 0.54); a QB's
# designed-run and scramble rates are stable traits (r 0.92 / 0.76, k 36
# exposure plays / 80 dropbacks); a defense's scramble OE is close to noise
# (r 0.04, k 359 expected scrambles; 145-200 on the other two splits), its
# designed-QB-run OE weak (r 0.16); the pbp hit + sack proxy is a real trait on
# both sides (QB r 0.61, k 159 dropbacks; defense OE r 0.40, k 67).
_K_DEFAULT = {
    "def_base_share": 113.87, "def_blitz": 92.2, "def_box_epa_dev": None, "def_box_expl10_dev": 1331.22,
    "def_box_expl20_dev": None, "def_box_mean": 212.87, "def_box_op_heavy_dev": 382.61,
    "def_box_op_light_dev": 1404.79, "def_box_sg_dev": 203.16, "def_box_stuff_dev": None,
    "def_box_success_dev": 1838.04, "def_box_ypc_dev": None, "def_containment_proxy_oe": 333.18,
    "def_designed_runs_oe": 40.26, "def_designed_yds_oe": 527.99, "def_dime_share": 29.15, "def_dir_epa_dev": None,
    "def_dir_expl10_dev": None, "def_dir_expl20_dev": None, "def_dir_stuff_dev": None, "def_dir_success_dev": None,
    "def_dir_ypc_dev": None, "def_dl3_share": 3.72, "def_dl4_share": 4.39, "def_explosive_runs_oe": 51.24,
    "def_heavy_share": 167.43, "def_hit_sack_rate_oe": 66.97, "def_light_share": 163.24, "def_mtkl_pg": 12.46,
    "def_mtkl_rate": 656.66, "def_nickel_share": 66.88, "def_pressure": 363.87, "def_pressure_pfr": 685.47,
    "def_rb_catch": 650.23, "def_rb_epa": 507.76, "def_rb_expl10": 305.3, "def_rb_expl20": 619.15,
    "def_rb_fd": 458.75, "def_rb_line_yds": 221.79, "def_rb_stuff": 230.19, "def_rb_success": 376.8,
    "def_rb_sy_conv": None, "def_rb_tgt_db": 595.18, "def_rb_yac_rec": 712.22, "def_rb_ypc": 333.44,
    "def_rb_ypt": 776.74, "def_red_zone_rushes_oe": 47.54, "def_rush_tds_oe": None, "def_scramble_yds_oe": 1381.2,
    "def_scrambles_oe": 359.45, "def_side_epa_dev": None, "def_side_expl10_dev": 1449.97, "def_side_expl20_dev": None,
    "def_side_stuff_dev": None, "def_side_success_dev": 2445.06, "def_side_ypc_dev": None, "def_yac": 513.26,
    "def_ybc": 413.73, "ol_gap_success_dev": 815.75, "ol_line_yds": 344.34, "ol_stuff": 448.25, "ol_success": 337.51,
    "ol_ybc": 310.73, "qb_dsg_expl10": None, "qb_dsg_rate": 36.34, "qb_dsg_success": 144.29, "qb_dsg_yds_rate": 63.08,
    "qb_dsg_ypc": 82.6, "qb_gl_rate": 728.97, "qb_hit_sack_rate": 158.7, "qb_kneel_pg": 31.43, "qb_rpo_rate": 235.22,
    "qb_rz_rate": 229.4, "qb_scr_blitz_dev": None, "qb_scr_expl10": 275.21, "qb_scr_man_dev": 493.86,
    "qb_scr_noblitz_dev": None, "qb_scr_nopress_dev": 997.0, "qb_scr_press_dev": 239.65, "qb_scr_rate": 79.94,
    "qb_scr_single_dev": 670.53, "qb_scr_two_dev": 599.08, "qb_scr_yds_db": 110.62, "qb_scr_yds_press": 98.8,
    "qb_scr_ypa": 75.26, "qb_scr_zone_dev": 1363.13, "qb_sneak_conv": None, "qb_sneak_rate": 328.41,
    "qb_td_rate": 484.81, "qb_x10_rate": 184.72, "rb_adot": 23.09, "rb_box_epa_dev": None, "rb_box_expl10_dev": None,
    "rb_box_expl20_dev": 757.27, "rb_box_stuff_dev": 313.69, "rb_box_success_dev": None, "rb_box_ypc_dev": 475.02,
    "rb_btk": 404.43, "rb_carries_pg": 1.12, "rb_carry_share": 17.8, "rb_catch": 876.94, "rb_chk_share": 52.06,
    "rb_dir_epa_dev": None, "rb_dir_expl10_dev": 596.64, "rb_dir_expl20_dev": 724.5, "rb_dir_share": 106.95,
    "rb_dir_stuff_dev": None, "rb_dir_success_dev": 371.41, "rb_dir_ypc_dev": 484.68, "rb_early_touch_share": 42.55,
    "rb_epa": 357.55, "rb_expl10": 239.98, "rb_expl20": 456.96, "rb_fd": 609.29, "rb_gl_share": 4.92,
    "rb_line_yds": 225.45, "rb_pers_epa_dev": 503.11, "rb_pers_expl10_dev": None, "rb_pers_expl20_dev": None,
    "rb_pers_stuff_dev": 679.96, "rb_pers_success_dev": None, "rb_pers_ypc_dev": 889.3, "rb_rb_carry_share": 16.35,
    "rb_ryoe": 544.48, "rb_rz_share": 10.75, "rb_screen_share": 37.35, "rb_sg_epa_dev": 1616.59,
    "rb_sg_expl10_dev": 890.63, "rb_sg_expl20_dev": None, "rb_sg_share": 11.45, "rb_sg_stuff_dev": None,
    "rb_sg_success_dev": 621.86, "rb_sg_ypc_dev": None, "rb_side_epa_dev": 1247.61, "rb_side_expl10_dev": 277.77,
    "rb_side_expl20_dev": None, "rb_side_share": 72.25, "rb_side_stuff_dev": 602.27, "rb_side_success_dev": None,
    "rb_side_ypc_dev": 389.03, "rb_sit_all": 45.79, "rb_sit_early": 33.43, "rb_sit_goal_line": 8.47,
    "rb_sit_neutral": 25.11, "rb_sit_red_zone": 17.8, "rb_sit_short_yardage": 9.63, "rb_sit_third": 7.98,
    "rb_sit_two_minute": 11.74, "rb_snap_share": 48.81, "rb_stuff": 441.18, "rb_success": 323.41,
    "rb_sy_conv": 449.85, "rb_target_share": 59.93, "rb_targets_pg": 1.86, "rb_td": 626.77, "rb_tgt_per_db": 140.15,
    "rb_tgt_per_db_vs_man_dev": 193.8, "rb_tgt_per_db_vs_zone_dev": 418.07, "rb_third_touch_share": 53.69,
    "rb_two_minute_touch_share": 50.7, "rb_yac": 302.69, "rb_yac_rec": 101.4, "rb_ybc": 163.65, "rb_ypc": 228.62,
    "rb_ypt": 171.91, "tm_early_run": 318.98, "tm_lead_run": 144.45, "tm_neutral_run": 210.59, "tm_plays_pg": 12.55,
    "tm_qb_keep": 24.28, "tm_rpo": 163.68, "tm_rz_run": 92.79, "tm_trail_run": 575.07,
}
_K = None
_NOREPORT_SEEN = set()      # (season, week) with no injury report, ledgered once a process


def reload_k(path=None):
    global _K, _k_path
    _K = None
    _k_path = path or RELIABILITY_PATH


def k_for(metric):
    """(k, source) for a metric. A metric the table measured as unsupported
    returns (None, source). A missing or unreadable table falls back to the code
    defaults and is ledgered (RUSH-k-table)."""
    global _K
    if _K is None:
        _K = {"source": "default", "k": dict(_K_DEFAULT)}
        try:
            with open(_k_path, encoding="utf-8") as fh:
                meas = json.load(fh)
            _K["k"] = dict(meas.get("k") or {})
            _K["source"] = meas.get("source") or "reliability.json"
        except (OSError, ValueError) as e:
            import errlog
            errlog.note("RUSH-k-table", e, path=_k_path)
    return _K["k"].get(metric), _K["source"]


def known_metric(metric):
    k_for(metric)
    return metric in _K["k"]


def fld(num, den, unit, metric=None, prior=None, kind="observed", basis="", shrink=True, base=None, scale=1.0):
    """One profile field (see the module docstring). `prior` is the shrinkage
    target (None: unshrunk); `base` the same target built from the unit's RAW
    own figure (what the reliability measurement subtracts); `scale`
    multiplies num / den / prior for display units (never the shrinkage)."""
    raw = (num / den) if den else None
    k = None
    if metric:
        k, _src = k_for(metric)
    if unit == "share":
        # a share's prior is a probability: an additive split offset could push it past 0 or 1
        # (box-by-personnel heavy share -0.048; tgt_per_db vs zone 1.24 -- the review's m1). Priors,
        # bases and values of a share are CLAMPED to [0, 1]; `raw` stays the measured ratio
        prior = None if prior is None else min(1.0, max(0.0, prior))
        base = None if base is None else min(1.0, max(0.0, base))
    shrunk = shrink and prior is not None and metric is not None
    if shrunk:
        val = prior if k is None else ((num + k * prior) / (den + k) if (den or k) else prior)
    else:
        val = raw
    if unit == "share" and val is not None:
        val = min(1.0, max(0.0, val))
    out = {"value": None if val is None else round(val * scale, 4), "unit": unit,
           "kind": "proxy" if kind == "proxy" else ("modelled" if shrunk else kind),
           "n": round(den, 2) if isinstance(den, float) else den, "basis": basis,
           "raw": None if raw is None else round(raw * scale, 4)}
    if metric:
        out["metric"] = metric
        out["k"] = None if k is None else round(k, 2)
        if known_metric(metric):
            if k is None:
                out["reliability"] = 0.0
            elif (den or 0.0) + k > 0:
                out["reliability"] = round((den or 0.0) / ((den or 0.0) + k), 3)
    if prior is not None:
        out["prior"] = round(prior * scale, 4)
    if base is not None:
        out["base"] = round(base * scale, 4)
    return out


# ---- tallies --------------------------------------------------------------------------------
class RushTallies:
    """Every counter for a set of games (one season's worth, normally)."""

    def __init__(self, season):
        self.season = int(season)
        self.weeks = set()                  # (season, week) of every game added: the leakage proof
        self.n_games = 0
        self.rb = {}                        # gsis -> {(dir, box, dpers, sg): RV} designed runs by a non-QB (player-scoped)
        self.qbrun = {}                     # gsis -> {(dir, box, dpers, sg): RV} QB designed runs, sneaks excluded
        self.dfn = {}                       # team -> {"run": {(dir, box, dpers, sg, rpos): RV}, "box": {(opers, sg, run): BV}}
        self.off = {}                       # team -> {"run": {...same key as dfn...}}
        self.team_games = {}                # team -> [("off" | "def", record)]
        self.player_games = {}              # gsis -> [record]
        self.counts = {"sneak_ftn": 0, "sneak_rule": 0, "aborted": 0, "aborted_unknown": 0, "sacks": 0,
                       "sacks_with_rusher_id": 0, "rpo_pass": 0}
        # "aligned" (the aux store was built on this matchup store), "stale" (built on another: its
        # row extras and situational counts are ignored), "missing" (no aux), "mixed"; None = no game yet
        self.aux_status = None

    def _d(self, t):
        d = self.dfn.get(t)
        if d is None:
            d = self.dfn[t] = {"run": {}, "box": {}}
        return d

    def _o(self, t):
        d = self.off.get(t)
        if d is None:
            d = self.off[t] = {"run": {}}
        return d


def _add(d, key, vals):
    cur = d.get(key)
    if cur is None:
        d[key] = list(vals)
    else:
        for i, v in enumerate(vals):
            cur[i] += v


def _pos(positions, pid):
    p = positions.get(pid) if positions else None
    return "RB" if p in ("RB", "FB") else (p if p in ("QB", "WR", "TE") else "UNK")


def _usable_aux(aux, store):
    """The parts of an aux store a game may read: the row-aligned extras and
    situational counts only when the aux was built against this very store."""
    if not aux:
        return {}, False
    return aux, R.aux_matches(aux, store)


def add_game(T, store, aux, gi, start, stop, positions):
    """Fold one game's plays (and its aux rows) into T."""
    C = store["cols"]
    teams = store["teams"]
    g = store["games"][gi]
    season, week, gid = store["season"], g["week"], g["id"]
    T.weeks.add((season, week))
    T.n_games += 1
    aux, aligned = _usable_aux(aux, store)
    # incomplete: built on this store but without a source the season should have (R.aux_complete; the data
    # re-checks' R1 / R3) -- what it has is still read, and the snapshot ledgers RUSH-aux
    st_ = "missing" if not aux else ("stale" if not aligned else ("aligned" if R.aux_complete(aux) else "incomplete"))
    T.aux_status = st_ if T.aux_status in (None, st_) else "mixed"
    ex = aux.get("extras") if aligned else None
    abt = ex["aborted"] if ex else None
    ryd = ex["rush_yds"] if ex else None
    NA, NA_H = D.NA, D.NA_H
    recs = {}

    def rec_for(off_t, dfn_t):
        r = recs.get(off_t)
        if r is None:
            r = recs[off_t] = {"season": season, "week": week, "gi": gi, "id": gid, "date": g.get("date"),
                               "off": off_t, "dfn": dfn_t, "team": [0.0] * TL_W, "players": {}, "qbs": {},
                               "dpfr": None, "ol": {}, "starters": None}
        return r

    def pline(r, pid):
        v = r["players"].get(pid)
        if v is None:
            v = r["players"][pid] = [0.0] * PL_W
        return v

    def qline(r, pid):
        v = r["qbs"].get(pid)
        if v is None:
            v = r["qbs"][pid] = [0.0] * QL_W
        return v

    c_kind, c_off, c_dfn, c_rusher, c_yds = C["kind"], C["off"], C["dfn"], C["rusher"], C["yds"]
    for i in range(start, stop):
        kind = c_kind[i]
        off_t, dfn_t = teams[c_off[i]], teams[c_dfn[i]]
        r = rec_for(off_t, dfn_t)
        tm = r["team"]
        rusher = c_rusher[i]
        rpos = _pos(positions, rusher) if rusher else None
        yds = c_yds[i]
        # official rushing yards: nflverse rushing_yards where the row aligned (laterals), else yards_gained
        oyds = ryd[i] if (ryd is not None and ryd[i] != NA_H) else yds
        rtd = C["rtd"][i] == 1
        if kind == D.SACK:
            T.counts["sacks"] += 1
            if rusher:
                T.counts["sacks_with_rusher_id"] += 1
            # a sack is never a rush (NFL scoring): it falls through to the dropback below
        if kind in (D.RUN, D.SCRAMBLE, D.KNEEL) and rusher:
            pl = pline(r, rusher)
            pl[PL["rush"]] += 1
            pl[PL["rush_yds"]] += oyds
            if rtd:
                pl[PL["rush_td"]] += 1
        if kind == D.KNEEL:
            # kneels: negative rushing for the box score, never a team play, a carry
            # in any efficiency figure, or a QB "designed run". Class yards are
            # yards_gained (as every other class); the official total above reads
            # nflverse rushing_yards -- they differ only on a lateral or a recovery
            if rusher:
                pl = pline(r, rusher)
                pl[PL["kneel"]] += 1
                pl[PL["kneel_yds"]] += yds
                if rpos == "QB":
                    q = qline(r, rusher)
                    q[QL["kneel"]] += 1
                    q[QL["kneel_yds"]] += yds
            continue
        if kind == D.SPIKE:
            continue
        down, ytg, yl, qtr, gsr = C["down"][i], C["ytg"][i], C["yl"][i], C["qtr"][i], C["gsr"][i]
        sd = C["sd"][i]
        tmin = R.two_minute(qtr, gsr)
        sits = {"early": down in (1, 2), "third": down in (3, 4), "sy": down in (3, 4) and 0 < ytg <= 2,
                "rz": yl <= 20, "gl": yl <= 5, "2min": tmin, "neutral": R.neutral(C["wp"][i], qtr, gsr),
                "lead": sd >= LEAD and not tmin, "trail": sd <= -LEAD and not tmin}
        part = C["part"][i] == 1
        ftn = C["ftn"][i] == 1
        is_db = C["db"][i] == 1
        aborted = abt[i] if abt is not None else NA
        is_aborted = kind == D.RUN and aborted == 1
        is_des = kind == D.RUN and aborted != 1
        if kind == D.RUN and aborted == NA:
            T.counts["aborted_unknown"] += 1
        tm[TL["snaps"]] += 1
        if is_db:
            tm[TL["db"]] += 1
            for s, on in sits.items():
                if on:
                    tm[TL[f"db_{s}"]] += 1
        if is_des:
            tm[TL["des"]] += 1
            tm[TL["des_yds"]] += yds
            for s, on in sits.items():
                if on:
                    tm[TL[f"des_{s}"]] += 1
        # FTN's RPO flag: the play type, not the outcome -- an RPO that became a pass is a pass
        rpo = C["rpo"][i] if ftn else NA
        if ftn:
            tm[TL["ftn_plays"]] += 1
            if rpo == 1:
                tm[TL["rpo_plays"]] += 1
                if is_des:
                    tm[TL["rpo_runs"]] += 1
                elif is_db:
                    tm[TL["rpo_passes"]] += 1
                    T.counts["rpo_pass"] += 1
        # charting (participation; FTN's box where NGS has none, as the agreed profiles)
        box = C["box"][i] if part else NA
        if (box == NA or box <= 0) and ftn and C["fbox"][i] > 0:
            box = C["fbox"][i]
        bx = box_ix(box)
        dp = dpers_ix(C["ndb"][i] if part else NA)
        ndl = C["ndl"][i] if part else NA
        op = opers_ix(C["opers"][i]) if part else 0
        sg = 1 if C["sg"][i] else 0
        # ---- what the defense lines up in (every snap)
        Dd = T._d(dfn_t)
        _add(Dd["box"], (op, sg, 1 if is_des else 0),
             (1.0, 1.0 if bx else 0.0, float(box) if bx else 0.0, 1.0 if bx == 1 else 0.0, 1.0 if bx == 2 else 0.0,
              1.0 if bx == 3 else 0.0, 1.0 if dp else 0.0, 1.0 if dp == 1 else 0.0, 1.0 if dp == 2 else 0.0,
              1.0 if dp == 3 else 0.0, 1.0 if ndl > 0 else 0.0, 1.0 if 0 < ndl <= 3 else 0.0,
              1.0 if ndl == 4 else 0.0, 1.0 if ndl >= 5 else 0.0))
        if is_aborted:
            T.counts["aborted"] += 1
            tm[TL["abort"]] += 1
            if rusher:
                pl = pline(r, rusher)
                pl[PL["abort"]] += 1
                pl[PL["abort_yds"]] += yds
                if rpos == "QB":
                    q = qline(r, rusher)
                    q[QL["abort"]] += 1
                    q[QL["abort_yds"]] += yds
            continue
        epa = C["epa"][i]
        epa_f = 0.0 if epa == NA_H else epa / 100.0
        succ = 1.0 if C["succ"][i] == 1 else 0.0
        fd = 1.0 if C["fd"][i] == 1 else 0.0
        # ---- designed runs
        if is_des and rusher:
            dr = dir_ix(C["rloc"][i], C["rgap"][i])
            sneak = False
            if rpos == "QB":
                sn = C["sneak"][i] if ftn else NA
                if sn in (0, 1):
                    sneak = sn == 1
                    if sneak:
                        T.counts["sneak_ftn"] += 1
                else:
                    sneak = sneak_rule(sg, ytg, C["rloc"][i], C["rgap"][i])
                    if sneak:
                        T.counts["sneak_rule"] += 1
            conv = 1.0 if (fd or rtd) else 0.0
            vals = (1.0, float(yds), succ, epa_f, 1.0 if yds <= 0 else 0.0, 1.0 if yds >= 10 else 0.0,
                    1.0 if yds >= 20 else 0.0, 1.0 if rtd else 0.0, fd, line_yards(yds) if rpos == "RB" else 0.0,
                    1.0 if sits["sy"] else 0.0, conv if sits["sy"] else 0.0, 1.0 if sits["rz"] else 0.0,
                    1.0 if sits["gl"] else 0.0)
            rp = "QBS" if sneak else rpos
            key = (dr, bx, dp, sg, RPOS.index(rp))
            _add(Dd["run"], key, vals)
            _add(T._o(off_t)["run"], key, vals)
            # where a run goes: every designed run is the team's (TL des) and the defense's / offence's
            # (keyed by the rusher's position, so the RB-allowed figures read RB/FB runs only); a back's own
            # tally (T.rb, the efficiency profile and the reliability units) holds RB/FB runs ONLY -- a WR's
            # jet sweep or a TE's (Taysom Hill's) carry never becomes an "RB" unit (the review's B1: 2024's
            # T.rb held RB 135, WR 92, TE 14, FB 4, unknown 9)
            if rpos == "QB":
                if not sneak:
                    _add(T.qbrun.setdefault(rusher, {}), key[:4], vals)
            elif rpos == "RB":
                _add(T.rb.setdefault(rusher, {}), key[:4], vals)
            pl = pline(r, rusher)
            pl[PL["des"]] += 1
            pl[PL["des_yds"]] += yds
            pl[PL["des_succ"]] += succ
            pl[PL["des_fd"]] += fd
            pl[PL["des_epa"]] += epa_f
            if rtd:
                pl[PL["des_td"]] += 1
            if yds >= 10:
                pl[PL["des_x10"]] += 1
            if yds >= 20:
                pl[PL["des_x20"]] += 1
            if yds <= 0:
                pl[PL["des_stuff"]] += 1
            for s, on in sits.items():
                if on:
                    pl[PL[f"des_{s}"]] += 1
            if sits["gl"] and rtd:
                pl[PL["des_gl_td"]] += 1
            if sits["sy"] and conv:
                pl[PL["des_sy_conv"]] += 1
            if rpos == "RB":
                tm[TL["rb_des"]] += 1
                tm[TL["rb_des_yds"]] += yds
                tm[TL["rb_line_yds"]] += line_yards(yds)
                tm[TL["rb_stuff"]] += 1.0 if yds <= 0 else 0.0
                tm[TL["rb_succ"]] += succ
            if rpos == "QB":
                q = qline(r, rusher)
                if sneak:
                    tm[TL["qb_sneak"]] += 1
                    pl[PL["sneak"]] += 1
                    pl[PL["sneak_yds"]] += yds
                    q[QL["sneak"]] += 1
                    q[QL["sneak_yds"]] += yds
                    if rtd:
                        q[QL["sneak_td"]] += 1
                    if sits["sy"]:
                        q[QL["sneak_sy"]] += 1
                        q[QL["sneak_sy_conv"]] += conv
                else:
                    tm[TL["qb_des"]] += 1
                    q[QL["dsg"]] += 1
                    q[QL["dsg_yds"]] += yds
                    q[QL["dsg_succ"]] += succ
                    q[QL["dsg_epa"]] += epa_f
                    if rtd:
                        q[QL["dsg_td"]] += 1
                    if yds >= 10:
                        q[QL["dsg_x10"]] += 1
                    if rpo == 1:
                        tm[TL["rpo_qb_keeps"]] += 1
                        q[QL["rpo"]] += 1
                        q[QL["rpo_yds"]] += yds
                        pl[PL["rpo"]] += 1
                        pl[PL["rpo_yds"]] += yds
                if rtd:
                    q[QL["rush_td"]] += 1
                if sits["rz"]:
                    q[QL["rz_rush"]] += 1
                if sits["gl"]:
                    q[QL["gl_rush"]] += 1
                if yds >= 10:
                    q[QL["x10_rush"]] += 1
        # ---- scrambles (a dropback that became a run; nflverse leaves the passer
        # empty on them, so the scrambler is the rusher)
        if kind == D.SCRAMBLE and rusher:
            pl = pline(r, rusher)
            pl[PL["scr"]] += 1
            pl[PL["scr_yds"]] += yds
        # ---- the quarterback's dropback: passer on attempts / sacks, the rusher on a scramble
        if is_db:
            q_id = rusher if kind == D.SCRAMBLE else C["passer"][i]
            if q_id and _pos(positions, q_id) == "QB":
                q = qline(r, q_id)
                q[QL["db"]] += 1
                scr = kind == D.SCRAMBLE
                if kind == D.SACK or C["qbhit"][i] == 1:
                    q[QL["hs"]] += 1
                if kind == D.ATTEMPT:
                    q[QL["att"]] += 1
                if kind == D.SACK:
                    q[QL["sack"]] += 1
                if scr:
                    q[QL["scr"]] += 1
                    q[QL["scr_yds"]] += yds
                    if rtd:
                        q[QL["scr_td"]] += 1
                        q[QL["rush_td"]] += 1
                    if yds >= 10:
                        q[QL["scr_x10"]] += 1
                        q[QL["x10_rush"]] += 1
                    q[QL["scr_fd"]] += fd
                    if sits["rz"]:
                        q[QL["rz_rush"]] += 1
                    if sits["gl"]:
                        q[QL["gl_rush"]] += 1
                # splits: pressure and coverage only where scrambles are charted too
                if season >= SCR_CHART_ERA and part:
                    press = C["press"][i]
                    if press in (0, 1):
                        q[QL["press_n"]] += 1
                        if press == 1:
                            q[QL["press"]] += 1
                            if scr:
                                q[QL["scr_press"]] += 1
                                q[QL["scr_yds_press"]] += yds
                        elif scr:
                            q[QL["scr_nopress"]] += 1
                    mz = C["mz"][i]
                    if mz in (1, 2):
                        q[QL["mz_n"]] += 1
                        if mz == 1:
                            q[QL["man"]] += 1
                            if scr:
                                q[QL["scr_man"]] += 1
                        elif scr:
                            q[QL["scr_zone"]] += 1
                    fam = D.COV_FAMILY.get(D.COVERAGE[C["cov"][i]], "") if C["cov"][i] else ""
                    if fam in ("single_high", "two_high"):
                        q[QL["fam_n"]] += 1
                        if fam == "single_high":
                            q[QL["single"]] += 1
                            if scr:
                                q[QL["scr_single"]] += 1
                        else:
                            q[QL["two"]] += 1
                            if scr:
                                q[QL["scr_two"]] += 1
                # blitz: FTN's pass-rusher count (2022+), the one rusher count charted on scrambles before 2023
                if ftn and C["frush"][i] > 0:
                    q[QL["blitz_n"]] += 1
                    if C["frush"][i] >= 5:
                        q[QL["blitz"]] += 1
                        if scr:
                            q[QL["scr_blitz"]] += 1
                    elif scr:
                        q[QL["scr_noblitz"]] += 1
        # ---- targets
        if kind == D.ATTEMPT and C["receiver"][i]:
            rcv = C["receiver"][i]
            pl = pline(r, rcv)
            comp = C["comp"][i] == 1
            pl[PL["tgt"]] += 1
            tm[TL["tgt"]] += 1
            for s, on in sits.items():
                if on:
                    pl[PL[f"tgt_{s}"]] += 1
                    tm[TL[f"tgt_{s}"]] += 1
            air = C["air"][i]
            air_ok = air != NA and air > -99
            yac = C["yac"][i]
            yac_f = float(yac) if (comp and yac != NA and yac > -99) else 0.0
            if comp:
                pl[PL["rec"]] += 1
                pl[PL["rec_yds"]] += yds
                pl[PL["yac_sum"]] += yac_f
            if C["ptd"][i]:
                pl[PL["rec_td"]] += 1
            if air_ok:
                pl[PL["air_sum"]] += air
                pl[PL["air_n"]] += 1
            if ftn:
                pl[PL["ftn_tgt"]] += 1
                if C["screen"][i] == 1:
                    pl[PL["screen_tgt"]] += 1
                if C["readt"][i] == D.READ_IX["CHK"]:
                    pl[PL["chk_tgt"]] += 1
            mz = C["mz"][i] if part else 0
            if mz == 1:
                pl[PL["tgt_man"]] += 1
                pl[PL["yds_man"]] += yds if comp else 0
            elif mz == 2:
                pl[PL["tgt_zone"]] += 1
                pl[PL["yds_zone"]] += yds if comp else 0
            if _pos(positions, rcv) == "RB":
                tm[TL["rb_tgt"]] += 1
                if comp:
                    tm[TL["rb_rec"]] += 1
                    tm[TL["rb_recy"]] += yds
                    tm[TL["rb_yac"]] += yac_f
                if air_ok:
                    tm[TL["rb_air_sum"]] += air
                    tm[TL["rb_air_n"]] += 1
        if C["fuml"][i] and C["fumbler"][i] and C["fumbler"][i] == rusher and kind in (D.RUN, D.SCRAMBLE):
            pline(r, rusher)[PL["fuml"]] += 1
    _close_game(T, store, aux, aligned, gi, recs, positions)
    return recs


def _close_game(T, store, aux, aligned, gi, recs, positions):
    """Per-game joins: the agreed on-field vector, the situational counts, PFR
    snaps / rush / pass / def, NGS, the QB's exposure; then the records."""
    teams = store["teams"]
    g = store["games"][gi]
    gid, week = g["id"], g["week"]
    onfield = (store.get("onfield") or {}).get(gi) or {}
    sit = ((aux.get("sit") or {}) if aligned else {})
    sit_p = (sit.get("players") or {}).get(gi) or {}
    sit_t = (sit.get("teams") or {}).get(gi) or {}
    snaps = (aux.get("snaps") or {}).get(gid) or {}
    pfr_rush = (aux.get("pfr_rush") or {}).get(gid) or {}
    pfr_pass = (aux.get("pfr_pass") or {}).get(gid) or {}
    pfr_def = (aux.get("pfr_def") or {}).get(gid) or {}
    ngs = (aux.get("ngs") or {}).get(week) or {}
    for off_t, r in recs.items():
        tm = r["team"]
        for pid, v in onfield.items():
            if teams[v[D.OF_TEAM]] != off_t or _pos(positions, pid) not in ("RB", "QB"):
                continue
            pl = r["players"].get(pid)
            if pl is None:
                pl = r["players"][pid] = [0.0] * PL_W
            for nm, j in (("of_snaps", D.OF_SNAPS), ("of_db", D.OF_DB), ("of_run", D.OF_RUSH), ("of_man", D.OF_MAN),
                          ("of_zone", D.OF_ZONE), ("of_tgt", D.OF_TGT), ("of_tgt_man", D.OF_TGT_MAN),
                          ("of_tgt_zone", D.OF_TGT_ZONE)):
                pl[PL[nm]] = v[j]
        ti = teams.index(off_t)
        tv = sit_t.get(ti)
        if tv is not None:
            for k, s in enumerate(R.SIT):
                tm[TL[f"sit_{s}"]] = tv[k]
        for pid, v in sit_p.items():
            if v[0] != ti:
                continue
            pl = r["players"].get(pid)
            if pl is None:
                pl = r["players"][pid] = [0.0] * PL_W
            for k, s in enumerate(R.SIT):
                pl[PL[f"sit_{s}"]] = v[k + 1]
        sn = snaps.get(off_t) or {}
        tm[TL["pfr_team_snaps"]] = sn.get("team", 0)
        r["ol"] = dict(sn.get("ol") or {})
        for pid, rec_ in (sn.get("skill") or {}).items():
            if _pos(positions, pid) not in ("RB", "QB"):
                continue
            s_n, st_n = rec_[0], (rec_[2] if len(rec_) > 2 else 0)
            pl = r["players"].get(pid)
            if pl is None:
                pl = r["players"][pid] = [0.0] * PL_W
            pl[PL["pfr_st_snaps"]] = st_n
            if s_n > 0:
                # the snap-share denominator only for games he took an offensive snap
                pl[PL["pfr_snaps"]] = s_n
                pl[PL["pfr_team_snaps"]] = sn.get("team", 0)
        for pid, pl in r["players"].items():
            pl[PL["pos_rb"]] = 1.0 if _pos(positions, pid) == "RB" else 0.0
            # his APPEARANCE in the game for every field a fitted feature may read: a rush of any class, a target, a
            # dropback, or a participation on-field snap (seasons that have it) -- never PFR's snap counts, whose
            # lines (special-teams-only or offence-only in PFR) would carry PFR content into the usage windows (the
            # rule review's M8: ~1,100 of 6,041 RB rows moved with PFR rows). The PFR-inclusive game set feeds only
            # the restricted `any_snap` block and the PFR snap shares.
            pl[PL["app"]] = 1.0 if (pl[PL["rush"]] + pl[PL["des"]] + pl[PL["tgt"]] + pl[PL["scr"]] + pl[PL["abort"]]
                                    + pl[PL["kneel"]] + pl[PL["of_snaps"]] + pl[PL["sit_all"]] > 0 or pid in r["qbs"]) else 0.0
            pr = pfr_rush.get(pid)
            if pr:
                pl[PL["pfr_car"]], pl[PL["pfr_ybc"]], pl[PL["pfr_yac"]], pl[PL["pfr_btk"]], pl[PL["pfr_rbtk"]] = pr
                if _pos(positions, pid) == "RB":
                    tm[TL["rb_pfr_car"]] += pr[0]
                    tm[TL["rb_pfr_ybc"]] += pr[1]
                    tm[TL["rb_pfr_yac"]] += pr[2]
                    tm[TL["rb_pfr_btk"]] += pr[3]
            ng = ngs.get(pid)
            if ng and ng[0] == off_t and ng[3] is not None:
                pl[PL["ngs_games"]] += 1
                pl[PL["ngs_att"]] += ng[1]
                pl[PL["ngs_yds"]] += ng[2]
                pl[PL["ngs_exp"]] += ng[3]
                pl[PL["ngs_pct8_att"]] += (ng[6] or 0.0) / 100.0 * ng[1]
                pl[PL["ngs_ttlos_att"]] += (ng[7] or 0.0) * ng[1]
        for pid, q in r["qbs"].items():
            if tm[TL["db"]]:
                q[QL["expo"]] = tm[TL["snaps"]] * q[QL["db"]] / tm[TL["db"]]
            pp = pfr_pass.get(pid)
            if pp:
                for k, nm in enumerate(("pfr_pressured", "pfr_hurried", "pfr_hit", "pfr_blitzed", "pfr_sacked")):
                    q[QL[nm]] = pp[k]
                q[QL["pfr_rows"]] = 1
        dp = pfr_def.get(r["dfn"])
        r["dpfr"] = list(dp) if dp else None
        T.team_games.setdefault(off_t, []).append(("off", r))
        T.team_games.setdefault(r["dfn"], []).append(("def", r))
        for pid in set(r["players"]) | set(r["qbs"]):
            T.player_games.setdefault(pid, []).append(r)


def season_tallies(store, aux, positions, max_week=None, min_week=None, week_filter=None):
    """Tallies over a store's games with min_week <= week < max_week (either
    bound None for open); `week_filter(week)` further selects (the split-half
    measurement). max_week = the cutoff keeps strictly earlier games."""
    T = RushTallies(store["season"])
    for gi, start, stop in P.game_ranges(store):
        w = store["games"][gi]["week"]
        if max_week is not None and w >= max_week:
            continue
        if min_week is not None and w < min_week:
            continue
        if week_filter is not None and not week_filter(w):
            continue
        add_game(T, store, aux, gi, start, stop, positions)
    return T


# ---- aggregation helpers ----------------------------------------------------------------------
def _merge(tallies, getter):
    return P._merge(tallies, getter)


def _agg(d, select, width):
    return P._agg(d, select, width)


def _sum_lines(items, idx_name, width):
    """Weighted sum of per-game lines: items [(w, line)]."""
    out = [0.0] * width
    for w, line in items:
        if line:
            for i, v in enumerate(line):
                out[i] += v * w
    return out


def _r(num, den):
    return num / den if den else None


def _rate_block(v, L, own=None, metric_prefix="rb", unit_ypc="yds_per_carry", base_from=None, basis=""):
    """Designed-run efficiency from an RV vector. Overall (own is None): shrunk
    toward the league RV `L`. A split (own given, the unit's overall EB dict;
    base_from the unit's overall RAW dict): toward own + (league split - league
    overall), k of the split's departure."""
    n = v[RV["n"]]
    out = {"carries": round(n, 2)}
    spec = (("ypc", "yds", unit_ypc), ("success", "succ", "share"), ("epa", "epa", "epa_per_play"),
            ("stuff", "stuff", "share"), ("expl10", "x10", "share"), ("expl20", "x20", "share"))
    for name, col, unit in spec:
        lg = _r(L[RV[col]], L[RV["n"]])
        if own is None:
            out[name] = fld(v[RV[col]], n, unit, f"{metric_prefix}_{name}", lg, basis=basis)
        else:
            lg_all = own["_league"].get(name)
            off = (lg - lg_all) if (lg is not None and lg_all is not None) else 0.0
            pr = None if own.get(name) is None else own[name] + off
            br = None if (base_from or {}).get(name) is None else base_from[name] + off
            out[name] = fld(v[RV[col]], n, unit, f"{metric_prefix}_{name}_dev", pr, basis=basis, base=br)
    return out


def _own(block, L):
    """{"ypc": eb, ...} plus the league overall values, for split priors."""
    own = {k: (block[k]["value"] if isinstance(block.get(k), dict) else None)
           for k in ("ypc", "success", "epa", "stuff", "expl10", "expl20")}
    raw = {k: (block[k]["raw"] if isinstance(block.get(k), dict) else None)
           for k in ("ypc", "success", "epa", "stuff", "expl10", "expl20")}
    lg = {name: _r(L[RV[col]], L[RV["n"]]) for name, col in
          (("ypc", "yds"), ("success", "succ"), ("epa", "epa"), ("stuff", "stuff"), ("expl10", "x10"), ("expl20", "x20"))}
    own["_league"] = lg
    return own, raw


# ---- where each field comes from ---------------------------------------------------------------------
# Tags: "pbp", "participation", "ftn", "injuries", "pfr" (PFR advanced stats), "pfr_snaps" (PFR snap counts),
# "ngs" (NFL Next Gen Stats weekly rushing), "none" (not available), or "derived:<a>+<b>..." for a mix. The
# licence default (Sports Reference's terms, section 5; the NFL's terms for NGS): a field whose tag names pfr,
# pfr_snaps or ngs is DISPLAYED with credit and never enters a fitted model feature until the owner decides
# otherwise -- `restricted(tag)`. First matching rule wins; anything unmatched is pbp.
_SOURCE_RULES = {
    "rb": ((r"^usage\.snap_share$", "pfr_snaps"), (r"^recent\.snap_share$", "pfr_snaps"), (r"^any_snap\.", "derived:pbp+pfr_snaps"),
           (r"^usage\.(.*_onfield|snap_share_charted|onfield_plays_current)$", "participation"),
           (r"^usage\.tgt_per_db", "derived:participation+pbp"),
           (r"^rushing\.by_box\.", "derived:participation+ftn+pbp"),
           (r"^rushing\.by_personnel_faced\.", "derived:participation+pbp"),
           (r"^rushing\.line\.ybc$", "pfr"), (r"^rushing\.back\.(yac|broken_tackles)$", "pfr"),
           (r"^rushing\.back\.ryoe_carry_coverage$", "derived:ngs+pbp"), (r"^rushing\.back\.", "ngs"),
           (r"^receiving\.(screen_share|checkdown_share)$", "derived:ftn+pbp"),
           (r"^receiving\.ypt_vs_", "derived:participation+pbp"),
           # every other usage / recent figure: pbp counts over a game set that participation's on-field snaps also
           # decide (the appearance rule) -- the data re-checks' last nit; both parts unrestricted
           (r"^(usage|recent)\.", "derived:participation+pbp")),
    # designed.* excludes sneaks, which FTN identifies (the rule where it has no row): ftn+pbp (the re-check's nit)
    "qb": ((r"^designed\.read_option$", "none"), (r"^designed\.", "derived:ftn+pbp"), (r"^sneak\.", "derived:ftn+pbp"),
           (r"^scramble\.vs\.(blitz|no_blitz)$", "derived:ftn+pbp"),
           (r"^scramble\.(vs\.|yds_per_pressured_db|share_under_pressure|pressure_rate_faced|man_rate_faced)", "derived:participation+pbp"),
           (r"^pressure_pfr$", "pfr")),
    "def": ((r"^vs_rb\.by_box\.", "derived:participation+ftn+pbp"), (r"^vs_sneak\.", "derived:ftn+pbp"),
            (r"^box\.by_offense_personnel\.[^.]+\.(nickel|dime|base)$", "participation"),
            (r"^box\.", "derived:participation+ftn+pbp"), (r"^(personnel|front_proxy)\.", "participation"),
            (r"^contact\.", "pfr")),
    "dq": ((r"^pressure$", "derived:participation+pbp"), (r"^pressure_pfr$", "pfr"), (r"^blitz$", "derived:ftn+pbp"),
           (r"^containment_proxy\.", "derived:participation+pbp")),
    "ol": ((r"^ybc$", "pfr"), (r"^starters\.out_report$", "derived:injuries+pfr_snaps"), (r"^starters\.", "pfr_snaps"),
           (r"^ol_listed_out$", "injuries")),
    "tc": ((r"^rpo_rate$", "derived:ftn+pbp"),),
}
SOURCE_TAGS = ("pbp", "participation", "ftn", "injuries", "pfr", "pfr_snaps", "ngs", "none")
RESTRICTED = ("pfr", "pfr_snaps", "ngs")
_SOURCE_CACHE = {}          # (kind, path) -> tag: the paths are static, a few hundred keys


def SOURCE_OF(kind, path):
    """The source tag of a profile field: kind "rb" / "qb" / "def" (defense_run) / "dq" (defense_qb) / "ol" /
    "tc" (team_context), path its dotted path (RP.flatten)."""
    key = (kind, path)
    tag = _SOURCE_CACHE.get(key)
    if tag is None:
        import re
        tag = next((t for pat, t in _SOURCE_RULES[kind] if re.match(pat, path)), "pbp")
        _SOURCE_CACHE[key] = tag
    return tag


def restricted(tag):
    """True when a source tag involves PFR (advanced stats or snap counts) or NGS: display with credit, never a
    fitted model feature (the licence default)."""
    parts = tag[len("derived:"):].split("+") if tag.startswith("derived:") else [tag]
    return any(p in RESTRICTED for p in parts)


# ---- the snapshot ---------------------------------------------------------------------------------
class RushSnapshot:
    """Rushing profiles as of (season, week) from the current season's tallies
    and the prior seasons' at their weights. `aux` {season: aux store} supplies
    the cutoff week's injury report (pre-game) and nothing else at read time."""

    def __init__(self, season, week, current, priors=(), aux=None, decay=DECAY):
        self.season, self.week = int(season), int(week)
        self.weights, self.tallies = {}, []
        if current is not None:
            self.tallies.append((1.0, current))
            self.weights[int(season)] = 1.0
        for T in priors:
            w = decay ** (int(season) - int(T.season))
            self.tallies.append((w, T))
            self.weights[int(T.season)] = round(w, 4)
        self.decay = decay
        self.aux = aux or {}
        self._league = None
        self._qbt = None
        self._k_src = k_for("rb_ypc")[1]
        for _w, T in self.tallies:
            for s, wk in T.weeks:
                if s > self.season or (s == self.season and wk >= self.week):
                    raise ValueError(f"tallies hold {s} week {wk}, cutoff {self.season} week {self.week}")
        # every profile says which seasons' aux it read and how (the review's M3: a missing or stale aux
        # turned aborted snaps into designed runs and emptied the on-field shares with nothing saying so)
        self.aux_status = {int(T.season): T.aux_status for _w, T in self.tallies if T.aux_status is not None}
        bad = {s: v for s, v in self.aux_status.items() if v != "aligned"}
        if bad:
            import errlog
            errlog.note("RUSH-aux", msg=f"snapshot {self.season} week {self.week}: aux {bad}")

    def contains(self, season, week):
        return any((season, week) in T.weeks for _w, T in self.tallies)

    def _stamp(self, d, kind):
        # every leaf says where it comes from (SOURCE_OF): the licence default is that PFR- and NGS-derived
        # fields are displayed with credit and never enter a fitted model feature (`restricted`)
        for path, f in flatten(d).items():
            f["source"] = SOURCE_OF(kind, path)
        d["as_of"] = [self.season, self.week]
        d["weights"] = dict(self.weights)
        d["k_source"] = self._k_src
        d["aux"] = dict(self.aux_status)
        return d

    def _games(self, key, side=None, team=None):
        """[(w, record)] for a team (key=team, side "off"/"def") or a player (key
        = gsis, team filter optional), sorted oldest first."""
        out = []
        for w, T in self.tallies:
            if side is not None:
                for sd, r in T.team_games.get(key) or []:
                    if sd == side:
                        out.append((w, r))
            else:
                for r in T.player_games.get(key) or []:
                    if team is None or r["off"] == team:
                        out.append((w, r))
        out.sort(key=lambda wr: (wr[1]["season"], wr[1]["week"]))
        return out

    # ---- league priors
    def league(self):
        if self._league is not None:
            return self._league
        run = _merge(self.tallies, lambda T: {(t,) + k: v for t, d in T.dfn.items() for k, v in d["run"].items()})
        box = _merge(self.tallies, lambda T: {(t,) + k: v for t, d in T.dfn.items() for k, v in d["box"].items()})
        L = {"run_all": _agg(run, lambda k: True, RV_W)}
        L["rb"] = _agg(run, lambda k: RPOS[k[5]] == "RB", RV_W)
        L["qb"] = _agg(run, lambda k: RPOS[k[5]] == "QB", RV_W)
        L["rb_dir"] = {d: _agg(run, lambda k, d=d: RPOS[k[5]] == "RB" and k[1] == d, RV_W) for d in range(1, 8)}
        L["rb_side"] = {s: _agg(run, lambda k, s=s: RPOS[k[5]] == "RB" and SIDE_OF.get(k[1]) == s, RV_W) for s in SIDES}
        L["rb_box"] = {b: _agg(run, lambda k, b=b: RPOS[k[5]] == "RB" and k[2] == b, RV_W) for b in (1, 2, 3)}
        L["rb_pers"] = {p: _agg(run, lambda k, p=p: RPOS[k[5]] == "RB" and k[3] == p, RV_W) for p in (1, 2, 3)}
        L["rb_sg"] = {s: _agg(run, lambda k, s=s: RPOS[k[5]] == "RB" and k[4] == s, RV_W) for s in (0, 1)}
        L["box_all"] = _agg(box, lambda k: True, BV_W)
        L["box_op"] = {o: _agg(box, lambda k, o=o: k[1] == o, BV_W) for o in range(1, len(OPERS))}
        L["box_sg"] = {s: _agg(box, lambda k, s=s: k[2] == s, BV_W) for s in (0, 1)}
        L["box_run"] = _agg(box, lambda k: k[3] == 1, BV_W)
        rb_dir_n = sum(L["rb_dir"][d][0] for d in L["rb_dir"])
        L["rb_dir_share"] = {d: _r(L["rb_dir"][d][0], rb_dir_n) for d in L["rb_dir"]}
        L["rb_side_share"] = {s: _r(L["rb_side"][s][0], rb_dir_n) for s in SIDES}
        # per-game lines league-wide: offence records (team), players by position, QBs
        tl = [0.0] * TL_W
        dpfr = [0.0] * len(DP_NAMES)
        dpfr_g = 0.0
        g_w = 0.0
        rbl = [0.0] * PL_W
        ql = [0.0] * QL_W
        for w, T in self.tallies:
            for lst in T.team_games.values():
                for side, r in lst:
                    if side != "off":
                        continue
                    g_w += w
                    for i, v in enumerate(r["team"]):
                        tl[i] += v * w
                    if r["dpfr"]:
                        dpfr_g += w
                        for i, v in enumerate(r["dpfr"]):
                            dpfr[i] += v * w
                    for pid, line in r["players"].items():
                        if line[PL["pos_rb"]] > 0:
                            for i, v in enumerate(line):
                                rbl[i] += v * w
                    for pid, line in r["qbs"].items():
                        for i, v in enumerate(line):
                            ql[i] += v * w
        L["team"], L["team_games"], L["rb_line"], L["qb_line"] = tl, g_w, rbl, ql
        L["dpfr"], L["dpfr_games"] = dpfr, dpfr_g
        self._league = L
        return L

    # ---- the QB rushing totals (for the defense's observed-over-expected)
    def _qb_totals(self):
        if self._qbt is not None:
            return self._qbt
        tot = {}
        for w, T in self.tallies:
            for lst in T.team_games.values():
                for side, r in lst:
                    if side != "off":
                        continue
                    for pid, q in r["qbs"].items():
                        cur = tot.get(pid)
                        if cur is None:
                            cur = tot[pid] = [0.0] * QL_W
                        for i, v in enumerate(q):
                            cur[i] += v * w
        self._qbt = tot
        return tot

    # ---- running back --------------------------------------------------------------------
    def rb(self, pid, team=None):
        """The back's rushing and receiving profile. Usage is team-scoped when
        `team` is given; efficiency pools all his teams (it is his)."""
        L = self.league()
        mine = _merge(self.tallies, lambda T: T.rb.get(pid))
        games_all = self._games(pid)
        # `games`: the games pbp or participation shows him in (PL "app") -- every usage figure, window and games
        # count reads these; `games_any` adds the games only PFR's snap counts list him in (special teams only, or
        # offensive snaps with no play or participation line): read by the PFR snap shares and the restricted
        # `any_snap` display block, never by an unrestricted field
        games_any = [(w, r) for w, r in games_all if team is None or r["off"] == team]
        games = [(w, r) for w, r in games_any if (r["players"].get(pid) or _ZERO_PL)[PL["app"]] > 0]
        line_all = _sum_lines([(w, r["players"].get(pid)) for w, r in games_all], None, PL_W)
        line = _sum_lines([(w, r["players"].get(pid)) for w, r in games], None, PL_W)
        line_any = _sum_lines([(w, r["players"].get(pid)) for w, r in games_any], None, PL_W)
        tml = _sum_lines([(w, r["team"]) for w, r in games], None, TL_W)
        wsum = sum(w for w, r in games)
        cur_db = sum(r["players"].get(pid)[PL["of_db"]] for w, r in games if w == 1.0 and r["season"] == self.season)
        cur_sit = sum(r["team"][TL["sit_all"]] for w, r in games if w == 1.0 and r["season"] == self.season)
        out = {"gsis": D.gsis_str(pid), "team": team,
               "games": {"current": sum(1 for w, r in games if w == 1.0), "weighted": round(wsum, 2)},
               "scope": {"usage": ("team-scoped: his games for %s" % team) if team else "player: all his games",
                         "efficiency": "player: every team he played for in the window"}}
        # ---- usage (team-scoped)
        u = {}
        u["carries_pg"] = fld(line[PL["des"]], wsum, "per_game", "rb_carries_pg", basis="designed runs per game (sneaks, kneels, aborted snaps out)")
        u["targets_pg"] = fld(line[PL["tgt"]], wsum, "per_game", "rb_targets_pg", basis="targets per game")
        u["carry_share"] = fld(line[PL["des"]], tml[TL["des"]], "share", "rb_carry_share",
                               basis="his designed runs / the team's designed runs, same games (committee share)")
        u["rb_carry_share"] = fld(line[PL["des"]], tml[TL["rb_des"]], "share", "rb_rb_carry_share",
                                  basis="his designed runs / the team's RB designed runs, same games")
        u["target_share"] = fld(line[PL["tgt"]], tml[TL["tgt"]], "share", "rb_target_share", basis="his targets / the team's targets, same games")
        u["rz_carry_share"] = fld(line[PL["des_rz"]], tml[TL["des_rz"]], "share", "rb_rz_share",
                                  basis="designed runs inside the 20 / the team's, same games")
        u["gl_carry_share"] = fld(line[PL["des_gl"]], tml[TL["des_gl"]], "share", "rb_gl_share",
                                  basis="designed runs inside the 5 / the team's, same games")
        for s, nm in (("early", "early"), ("third", "third"), ("2min", "two_minute")):
            u[f"{nm}_touch_share"] = fld(line[PL[f"des_{s}"]] + line[PL[f"tgt_{s}"]],
                                         tml[TL[f"des_{s}"]] + tml[TL[f"db_{s}"]], "share", f"rb_{nm}_touch_share",
                                         basis=f"(his designed runs + targets) / the team's (designed runs + dropbacks), {nm.replace('_', ' ')} plays")
        # on-field by situation (participation; same charted plays both sides; none for 2026)
        for s, nm in (("all", "snap_share_charted"), ("early", "early_onfield"), ("third", "third_onfield"),
                      ("short_yardage", "short_yardage_onfield"), ("red_zone", "red_zone_onfield"),
                      ("goal_line", "goal_line_onfield"), ("two_minute", "two_minute_onfield"), ("neutral", "neutral_onfield")):
            u[nm] = fld(line[PL[f"sit_{s}"]], tml[TL[f"sit_{s}"]], "share", f"rb_sit_{s}",
                        basis=f"plays he was on the field for / the team's plays, {s.replace('_', ' ')}, participation-charted plays only (2016-2025)")
        u["onfield_plays_current"] = fld(cur_sit, 1, "count",
                                         basis="this season's charted team plays behind the on-field shares above (0 on every 2026 row: no participation file; they then read prior seasons only)")
        u["snap_share"] = fld(line_any[PL["pfr_snaps"]], line_any[PL["pfr_team_snaps"]], "share", "rb_snap_share",
                              basis="PFR offensive snaps / the team's PFR offensive snaps, games PFR lists him (every season incl. 2026)")
        u["tgt_per_db"] = dict(fld(line[PL["of_tgt"]], line[PL["of_db"]], "share", "rb_tgt_per_db", kind="proxy",
                                   basis="targets per dropback he was on the field for (participation, same plays; a snap is not a route)"),
                               n_current=cur_db)
        lg_tpd = _r(L["rb_line"][PL["of_tgt"]], L["rb_line"][PL["of_db"]])
        own_tpd = u["tgt_per_db"]["raw"]
        lg_man = _r(L["rb_line"][PL["of_tgt_man"]], L["rb_line"][PL["of_man"]])
        lg_zone = _r(L["rb_line"][PL["of_tgt_zone"]], L["rb_line"][PL["of_zone"]])
        for nm, num, den, lg in (("tgt_per_db_vs_man", "of_tgt_man", "of_man", lg_man), ("tgt_per_db_vs_zone", "of_tgt_zone", "of_zone", lg_zone)):
            pr = None if (own_tpd is None or lg is None or not lg_tpd) else own_tpd * lg / lg_tpd
            u[nm] = fld(line[PL[num]], line[PL[den]], "share", f"rb_{nm}_dev", pr, kind="proxy", base=pr,
                        basis="targets per on-field dropback vs this coverage, same plays; prior = his overall rate x the league's man/zone ratio")
        out["usage"] = u
        # ---- rushing efficiency (player-scoped)
        allv = _agg(mine, lambda k: True, RV_W)
        rush = _rate_block(allv, L["rb"], basis="designed runs, all his teams")
        own, raw = _own(rush, L["rb"])
        rush["first_down"] = fld(allv[RV["fd"]], allv[RV["n"]], "share", "rb_fd", _r(L["rb"][RV["fd"]], L["rb"][RV["n"]]),
                                 basis="first downs per designed run")
        rush["td_rate"] = fld(allv[RV["td"]], allv[RV["n"]], "share", "rb_td", _r(L["rb"][RV["td"]], L["rb"][RV["n"]]),
                              basis="rushing TDs per designed run")
        rush["short_yardage_conv"] = fld(allv[RV["sy_conv"]], allv[RV["sy"]], "share", "rb_sy_conv",
                                         _r(L["rb"][RV["sy_conv"]], L["rb"][RV["sy"]]),
                                         basis="3rd/4th & 1-2 designed runs converted (first down or TD)")
        rush["line_yds"] = fld(allv[RV["ly"]], allv[RV["n"]], "line_yds_per_carry", "rb_line_yds",
                               _r(L["rb"][RV["ly"]], L["rb"][RV["n"]]),
                               basis="line yards per carry by the stated convention (120% of losses, 100% of 0-4, 50% of 5-10, none past 10; not FO's official ALY) -- what the LINE produced on his runs")
        # by direction x gap: his distribution and his efficiency there
        dirs = {}
        n_dir = sum(_agg(mine, lambda k, d=d: k[0] == d, RV_W)[0] for d in range(1, 8))
        for d in range(1, 8):
            v = _agg(mine, lambda k, d=d: k[0] == d, RV_W)
            blk = _rate_block(v, L["rb_dir"][d], own=own, base_from=raw, metric_prefix="rb_dir",
                              basis=f"designed runs {DIRS[d].replace('_', ' ')}")
            blk["share"] = fld(v[0], n_dir, "share", "rb_dir_share", L["rb_dir_share"].get(d), base=L["rb_dir_share"].get(d),
                               basis="share of his charted-direction runs")
            dirs[DIRS[d]] = blk
        rush["by_direction"] = dirs
        sides = {}
        for s in SIDES:
            v = _agg(mine, lambda k, s=s: SIDE_OF.get(k[0]) == s, RV_W)
            blk = _rate_block(v, L["rb_side"][s], own=own, base_from=raw, metric_prefix="rb_side",
                              basis={"edge": "end gap, either side", "tackle": "tackle gap, either side",
                                     "interior": "guard gaps and middle"}[s])
            blk["share"] = fld(v[0], n_dir, "share", "rb_side_share", L["rb_side_share"].get(s), base=L["rb_side_share"].get(s),
                               basis="share of his charted-direction runs")
            sides[s] = blk
        rush["by_side"] = sides
        rush["by_box"] = {BOX[b]: _rate_block(_agg(mine, lambda k, b=b: k[1] == b, RV_W), L["rb_box"][b], own=own, base_from=raw,
                                              metric_prefix="rb_box", basis=f"{BOX[b]} box (<=6 / 7 / 8+ defenders; NGS, FTN where NGS has none)")
                          for b in (1, 2, 3)}
        rush["by_personnel_faced"] = {DPERS[p]: _rate_block(_agg(mine, lambda k, p=p: k[2] == p, RV_W), L["rb_pers"][p], own=own,
                                                            base_from=raw, metric_prefix="rb_pers",
                                                            basis=f"vs {DPERS[p]} (DB count from participation; none for 2026)")
                                      for p in (1, 2, 3)}
        sgv = {s: _agg(mine, lambda k, s=s: k[3] == s, RV_W) for s in (0, 1)}
        rush["by_formation"] = {nm: _rate_block(sgv[s], L["rb_sg"][s], own=own, base_from=raw, metric_prefix="rb_sg",
                                                basis=f"{nm.replace('_', ' ')} (nflfastR shotgun flag)")
                                for s, nm in ((1, "shotgun"), (0, "under_center"))}
        lg_sg = _r(L["rb_sg"][1][0], L["rb_sg"][0][0] + L["rb_sg"][1][0])
        rush["shotgun_share"] = fld(sgv[1][0], sgv[0][0] + sgv[1][0], "share", "rb_sg_share", lg_sg, base=lg_sg,
                                    basis="share of his designed runs from shotgun")
        # what the line produced vs what the back added (PFR; NGS)
        lg_rb = L["rb_line"]
        rush["line"] = {"ybc": fld(line_all[PL["pfr_ybc"]], line_all[PL["pfr_car"]], "yds_per_carry", "rb_ybc",
                                   _r(lg_rb[PL["pfr_ybc"]], lg_rb[PL["pfr_car"]]),
                                   basis="PFR yards before contact per PFR carry -- mostly the blocking (its line yards: rushing.line_yds)")}
        rush["back"] = {"yac": fld(line_all[PL["pfr_yac"]], line_all[PL["pfr_car"]], "yds_per_carry", "rb_yac",
                                   _r(lg_rb[PL["pfr_yac"]], lg_rb[PL["pfr_car"]]), basis="PFR yards after contact per PFR carry -- the back"),
                        "broken_tackles": fld(line_all[PL["pfr_btk"]], line_all[PL["pfr_car"]], "per_carry", "rb_btk",
                                              _r(lg_rb[PL["pfr_btk"]], lg_rb[PL["pfr_car"]]), basis="PFR rushing broken tackles per carry"),
                        "ryoe": fld(line_all[PL["ngs_yds"]] - line_all[PL["ngs_exp"]], line_all[PL["ngs_att"]], "yds_per_carry", "rb_ryoe",
                                    _r(lg_rb[PL["ngs_yds"]] - lg_rb[PL["ngs_exp"]], lg_rb[PL["ngs_att"]]),
                                    basis="NGS rush yards over expected per attempt -- ONLY his games with 10+ rushes (NGS weekly rows)"),
                        "ryoe_games": line_all[PL["ngs_games"]], "ryoe_carry_coverage": fld(line_all[PL["ngs_att"]], line_all[PL["des"]], "share",
                                                                                           basis="NGS attempts / his designed runs: how much of his work the RYOE covers"),
                        "eight_box_share_ngs": fld(line_all[PL["ngs_pct8_att"]], line_all[PL["ngs_att"]], "share",
                                                   basis="NGS share of attempts vs 8+ defenders (games with 10+ rushes)"),
                        "time_to_los": fld(line_all[PL["ngs_ttlos_att"]], line_all[PL["ngs_att"]], "seconds",
                                           basis="NGS average time to the line of scrimmage (games with 10+ rushes)")}
        out["rushing"] = rush
        # ---- receiving (player-scoped efficiency)
        lr = L["rb_line"]
        out["receiving"] = {
            "targets": round(line_all[PL["tgt"]], 2),
            "catch": fld(line_all[PL["rec"]], line_all[PL["tgt"]], "share", "rb_catch", _r(lr[PL["rec"]], lr[PL["tgt"]]), basis="receptions per target"),
            "ypt": fld(line_all[PL["rec_yds"]], line_all[PL["tgt"]], "yds_per_target", "rb_ypt", _r(lr[PL["rec_yds"]], lr[PL["tgt"]]), basis="receiving yards per target"),
            "yac_rec": fld(line_all[PL["yac_sum"]], line_all[PL["rec"]], "yds_per_reception", "rb_yac_rec", _r(lr[PL["yac_sum"]], lr[PL["rec"]]), basis="YAC per reception"),
            "adot": fld(line_all[PL["air_sum"]], line_all[PL["air_n"]], "yds", "rb_adot", _r(lr[PL["air_sum"]], lr[PL["air_n"]]), basis="air yards per target"),
            "screen_share": fld(line_all[PL["screen_tgt"]], line_all[PL["ftn_tgt"]], "share", "rb_screen_share", _r(lr[PL["screen_tgt"]], lr[PL["ftn_tgt"]]),
                                basis="FTN screen passes / his FTN-charted targets"),
            "checkdown_share": fld(line_all[PL["chk_tgt"]], line_all[PL["ftn_tgt"]], "share", "rb_chk_share", _r(lr[PL["chk_tgt"]], lr[PL["ftn_tgt"]]),
                                   kind="proxy", basis="FTN read_thrown = 'CHK' / his FTN-charted targets; FTN's dictionary was not reached, so CHK is READ as checkdown, unverified"),
            "ypt_vs_man": fld(line_all[PL["yds_man"]], line_all[PL["tgt_man"]], "yds_per_target", basis="YPT vs man (participation; reported, not shrunk: the agreed measurement found man-minus-zone efficiency noise)"),
            "ypt_vs_zone": fld(line_all[PL["yds_zone"]], line_all[PL["tgt_zone"]], "yds_per_target", basis="YPT vs zone (participation)"),
            "scope": "player: all his teams"}
        # ---- recent (the last RECENT games he played for this team, unweighted)
        rec_g = games[-RECENT:]
        rl = _sum_lines([(1.0, r["players"].get(pid)) for w, r in rec_g], None, PL_W)
        rt = _sum_lines([(1.0, r["team"]) for w, r in rec_g], None, TL_W)
        rec_any = games_any[-RECENT:]
        rla = _sum_lines([(1.0, r["players"].get(pid)) for w, r in rec_any], None, PL_W)
        rb_ = "his last %d games%s, unweighted across seasons" % (RECENT, " for this team" if team else "")
        out["recent"] = {"games": len(rec_g),
                         "carries_pg": fld(rl[PL["des"]], len(rec_g), "per_game", basis=f"designed runs per game, {rb_}"),
                         "carry_share": fld(rl[PL["des"]], rt[TL["des"]], "share", basis=f"his designed runs / the team's, {rb_}"),
                         "target_share": fld(rl[PL["tgt"]], rt[TL["tgt"]], "share", basis=f"his targets / the team's, {rb_}"),
                         "snap_share": fld(rla[PL["pfr_snaps"]], rla[PL["pfr_team_snaps"]], "share",
                                           basis=f"PFR offensive snaps / the team's, his last {RECENT} games PFR lists him"
                                                 + (" for this team" if team else "")),
                         "rz_carry_share": fld(rl[PL["des_rz"]], rt[TL["des_rz"]], "share",
                                               basis=f"designed runs inside the 20 / the team's, {rb_}")}
        # ---- display only (restricted, source pfr_snaps): the same usage over every game PFR's snap counts list him
        # in, special-teams-only games included -- Kalshi settles a player on one snap
        tma = _sum_lines([(w, r["team"]) for w, r in games_any], None, TL_W)
        wany = sum(w for w, r in games_any)
        rta = _sum_lines([(1.0, r["team"]) for w, r in rec_any], None, TL_W)
        ab_ = "every game pbp, participation OR PFR's snap counts list him in (special-teams-only included) -- display only"
        out["any_snap"] = {
            "games": fld(wany, 1, "games", basis=f"his games, weighted, {ab_}"),
            "carries_pg": fld(line_any[PL["des"]], wany, "per_game", basis=f"designed runs per game, {ab_}"),
            "targets_pg": fld(line_any[PL["tgt"]], wany, "per_game", basis=f"targets per game, {ab_}"),
            "carry_share": fld(line_any[PL["des"]], tma[TL["des"]], "share", basis=f"his designed runs / the team's, {ab_}"),
            "target_share": fld(line_any[PL["tgt"]], tma[TL["tgt"]], "share", basis=f"his targets / the team's, {ab_}"),
            "recent_carry_share": fld(rla[PL["des"]], rta[TL["des"]], "share",
                                      basis=f"his designed runs / the team's, his last {RECENT} such games")}
        return self._stamp(out, "rb")

    # ---- quarterback rushing -------------------------------------------------------------------
    def qb(self, pid, team=None):
        """The quarterback's rushing, decomposed: designed runs, sneaks, RPO
        keeps, scrambles (overall and by pressure / blitz / coverage), kneels,
        aborted snaps. Player-scoped (all his teams) unless `team` is given."""
        L = self.league()
        lq = L["qb_line"]
        games = [(w, r) for w, r in self._games(pid) if pid in r["qbs"] and (team is None or r["off"] == team)]
        q = _sum_lines([(w, r["qbs"][pid]) for w, r in games], None, QL_W)
        wsum = sum(w for w, r in games)
        expo = q[QL["expo"]]
        db = q[QL["db"]]
        out = {"gsis": D.gsis_str(pid), "team": team, "games": {"current": sum(1 for w, r in games if w == 1.0), "weighted": round(wsum, 2)},
               "scope": "player: all his teams" if team is None else f"team-scoped: his games for {team}",
               "exposure_basis": "exposure = the team's snaps x his share of its dropbacks in each game (team plays while he was the passer)"}
        lg = lambda a, b: _r(lq[QL[a]], lq[QL[b]])
        dsg = {"per_game": fld(q[QL["dsg"]], wsum, "per_game", basis="designed runs per game (not sneaks, kneels, scrambles, aborted snaps)"),
               "rate": fld(q[QL["dsg"]], expo, "per_play", "qb_dsg_rate", lg("dsg", "expo"), basis="designed runs per exposure play"),
               "ypc": fld(q[QL["dsg_yds"]], q[QL["dsg"]], "yds_per_carry", "qb_dsg_ypc", lg("dsg_yds", "dsg"), basis="yards per designed run"),
               "success": fld(q[QL["dsg_succ"]], q[QL["dsg"]], "share", "qb_dsg_success", lg("dsg_succ", "dsg"), basis="EPA-success per designed run"),
               "expl10": fld(q[QL["dsg_x10"]], q[QL["dsg"]], "share", "qb_dsg_expl10", lg("dsg_x10", "dsg"), basis="designed runs of 10+ yards"),
               "yds_rate": fld(q[QL["dsg_yds"]], expo, "yds_per_play", "qb_dsg_yds_rate", lg("dsg_yds", "expo"),
                               basis="designed-run yards per exposure play"),
               "rpo_keeps_rate": fld(q[QL["rpo"]], expo, "per_play", "qb_rpo_rate", lg("rpo", "expo"),
                                     basis="FTN RPO plays the QB kept and ran, per exposure play (2022+; an RPO that became a pass is a pass)"),
               "rpo_share_of_designed": fld(q[QL["rpo"]], q[QL["dsg"]], "share", basis="RPO keeps / his designed runs"),
               "read_option": {"value": None, "unit": "share", "kind": "not_available", "n": 0,
                               "basis": "NOT AVAILABLE: no free source identifies read-option keeps"}}
        out["designed"] = dsg
        out["sneak"] = {"rate": fld(q[QL["sneak"]], expo, "per_play", "qb_sneak_rate", lg("sneak", "expo"),
                                    basis="QB sneaks per exposure play (FTN is_qb_sneak, rule-coded where FTN is absent)"),
                        "per_game": fld(q[QL["sneak"]], wsum, "per_game", basis="sneaks per game"),
                        "short_yardage_conv": fld(q[QL["sneak_sy_conv"]], q[QL["sneak_sy"]], "share", "qb_sneak_conv",
                                                  lg("sneak_sy_conv", "sneak_sy"), basis="3rd/4th & 1-2 sneaks converted")}
        scr_rate = fld(q[QL["scr"]], db, "share", "qb_scr_rate", lg("scr", "db"), basis="scrambles per dropback (attempts + sacks + scrambles)")
        own_sr, raw_sr = scr_rate["value"], scr_rate["raw"]
        lg_sr = lg("scr", "db")
        scr = {"rate": scr_rate,
               "ypa": fld(q[QL["scr_yds"]], q[QL["scr"]], "yds_per_carry", "qb_scr_ypa", lg("scr_yds", "scr"), basis="yards per scramble"),
               "expl10": fld(q[QL["scr_x10"]], q[QL["scr"]], "share", "qb_scr_expl10", lg("scr_x10", "scr"), basis="scrambles of 10+ yards"),
               "per_game": fld(q[QL["scr"]], wsum, "per_game", basis="scrambles per game"),
               "yds_per_db": fld(q[QL["scr_yds"]], db, "yds_per_dropback", "qb_scr_yds_db", lg("scr_yds", "db"),
                                 basis="scramble yards per dropback"),
               "yds_per_pressured_db": fld(q[QL["scr_yds_press"]], q[QL["press"]], "yds_per_dropback", "qb_scr_yds_press",
                                           lg("scr_yds_press", "press"), basis="scramble yards per pressured dropback (2023+ charting)")}

        def cond(name, num, den, lnum, lden, basis):
            """A scramble-rate split, shrunk toward his own rate x the league's
            split / overall ratio (multiplicative: pressure multiplies a
            scramble rate, it does not add to it)."""
            lgc = _r(lnum, lden)
            ratio = (lgc / lg_sr) if (lgc is not None and lg_sr) else None
            pr = None if (own_sr is None or ratio is None) else own_sr * ratio
            br = None if (raw_sr is None or ratio is None) else raw_sr * ratio
            return fld(num, den, "share", f"qb_scr_{name}_dev", pr, base=br, basis=basis)
        g_ = lambda nm: q[QL[nm]]
        l_ = lambda nm: lq[QL[nm]]
        scr["vs"] = {
            "pressure": cond("press", g_("scr_press"), g_("press"), l_("scr_press"), l_("press"),
                             "scramble rate on pressured dropbacks = the pressure-to-scramble conversion (participation, 2023+)"),
            "no_pressure": cond("nopress", g_("scr_nopress"), g_("press_n") - g_("press"), l_("scr_nopress"), l_("press_n") - l_("press"),
                                "scramble rate on unpressured dropbacks (participation, 2023+)"),
            "blitz": cond("blitz", g_("scr_blitz"), g_("blitz"), l_("scr_blitz"), l_("blitz"),
                          "scramble rate vs 5+ rushers (FTN n_pass_rushers, 2022+)"),
            "no_blitz": cond("noblitz", g_("scr_noblitz"), g_("blitz_n") - g_("blitz"), l_("scr_noblitz"), l_("blitz_n") - l_("blitz"),
                             "scramble rate vs 4 or fewer rushers (FTN, 2022+)"),
            "man": cond("man", g_("scr_man"), g_("man"), l_("scr_man"), l_("man"), "scramble rate vs man coverage (participation, 2023+)"),
            "zone": cond("zone", g_("scr_zone"), g_("mz_n") - g_("man"), l_("scr_zone"), l_("mz_n") - l_("man"),
                         "scramble rate vs zone coverage (participation, 2023+)"),
            "single_high": cond("single", g_("scr_single"), g_("single"), l_("scr_single"), l_("single"),
                                "scramble rate vs single-high shells (Cover 1 / 3; 2023+)"),
            "two_high": cond("two", g_("scr_two"), g_("two"), l_("scr_two"), l_("two"),
                             "scramble rate vs two-high shells (Cover 2 / 4 / 6 / 2-Man; 2023+)")}
        scr["share_under_pressure"] = fld(q[QL["scr_press"]], q[QL["scr_press"]] + q[QL["scr_nopress"]], "share",
                                          basis="share of his 2023+ charted scrambles that came under pressure")
        scr["pressure_rate_faced"] = fld(q[QL["press"]], q[QL["press_n"]], "share", basis="pressured share of his charted dropbacks (2023+)")
        scr["man_rate_faced"] = fld(q[QL["man"]], q[QL["mz_n"]], "share", basis="man share of his charted dropbacks (2023+)")
        scr["note"] = ("pressure / coverage splits read 2023+ dropbacks only: participation charts neither on 2021-22 "
                       "scrambles, so mixing those seasons would count their scrambles as never pressured")
        out["scramble"] = scr
        out["kneel"] = {"per_game": fld(q[QL["kneel"]], wsum, "per_game", "qb_kneel_pg", basis="kneel-downs per game (NFL scoring: rushes, negative yards)"),
                        "yds_per_game": fld(q[QL["kneel_yds"]], wsum, "yds_per_game", basis="kneel yards per game")}
        out["aborted"] = {"per_game": fld(q[QL["abort"]], wsum, "per_game", basis="aborted snaps (fumbled exchanges) charged as his rushes, per game"),
                          "yds_per_game": fld(q[QL["abort_yds"]], wsum, "yds_per_game", basis="their yards per game")}
        out["scoring"] = {"rush_td_rate": fld(q[QL["rush_td"]], expo, "per_play", "qb_td_rate", lg("rush_td", "expo"),
                                              basis="rushing TDs (designed + sneak + scramble) per exposure play"),
                          "rz_rush_rate": fld(q[QL["rz_rush"]], expo, "per_play", "qb_rz_rate", lg("rz_rush", "expo"),
                                              basis="rushes inside the 20 (designed + sneak + scramble) per exposure play"),
                          "gl_rush_rate": fld(q[QL["gl_rush"]], expo, "per_play", "qb_gl_rate", lg("gl_rush", "expo"),
                                              basis="rushes inside the 5 per exposure play"),
                          "rush_td_pg": fld(q[QL["rush_td"]], wsum, "per_game", basis="rushing TDs per game"),
                          "x10_rate": fld(q[QL["x10_rush"]], expo, "per_play", "qb_x10_rate", lg("x10_rush", "expo"),
                                          basis="rushes of 10+ yards (designed or scramble) per exposure play")}
        tot_rush = q[QL["dsg"]] + q[QL["sneak"]] + q[QL["scr"]] + q[QL["kneel"]] + q[QL["abort"]]
        tot_yds = q[QL["dsg_yds"]] + q[QL["sneak_yds"]] + q[QL["scr_yds"]] + q[QL["kneel_yds"]] + q[QL["abort_yds"]]
        out["totals"] = {"rushes_pg": fld(tot_rush, wsum, "per_game", basis="official rushes per game: designed + sneak + scramble + kneel + aborted"),
                         "yds_pg": fld(tot_yds, wsum, "yds_per_game",
                                       basis="rushing yards per game, the five classes' yards_gained summed (kneels negative; sacks never); "
                                             "nflverse rushing_yards, the official figure, differs only on a lateral"),
                         "partition": {"designed": round(q[QL["dsg"]], 2), "sneak": round(q[QL["sneak"]], 2), "scramble": round(q[QL["scr"]], 2),
                                       "kneel": round(q[QL["kneel"]], 2), "aborted": round(q[QL["abort"]], 2)},
                         "dropbacks": round(db, 2), "exposure": round(expo, 2)}
        # by direction (designed, not sneaks) -- reported, unshrunk
        mine = _merge(self.tallies, lambda T: T.qbrun.get(pid))
        out["designed"]["by_side"] = {s: fld(_agg(mine, lambda k, s=s: SIDE_OF.get(k[0]) == s, RV_W)[0],
                                             sum(_agg(mine, lambda k, s2=s2: SIDE_OF.get(k[0]) == s2, RV_W)[0] for s2 in SIDES), "share",
                                             basis="share of his charted designed runs by side") for s in SIDES}
        out["hit_sack_rate"] = fld(q[QL["hs"]], db, "share", "qb_hit_sack_rate", lg("hs", "db"), kind="proxy",
                                   basis="sacks + QB hits that were not sacks, per dropback (pbp qb_hit; every season incl. 2026) -- "
                                         "a proxy: a hit or a sack is not a pressure")
        out["pressure_pfr"] = fld(q[QL["pfr_pressured"]], _pfr_db(games, pid), "share", kind="proxy",
                                  basis="PFR times pressured / his dropbacks in games PFR charted -- the in-season pressure source for 2026; PFR's charting, not NGS's")
        return self._stamp(out, "qb")

    # ---- defense vs the run --------------------------------------------------------------------
    def defense_run(self, team):
        L = self.league()
        run = _merge(self.tallies, lambda T: (T.dfn.get(team) or {}).get("run"))
        box = _merge(self.tallies, lambda T: (T.dfn.get(team) or {}).get("box"))
        games = self._games(team, side="def")
        wsum = sum(w for w, r in games)
        tl = _sum_lines([(w, r["team"]) for w, r in games], None, TL_W)
        out = {"team": team, "games": {"current": sum(1 for w, r in games if w == 1.0), "weighted": round(wsum, 2)}}
        rbv = _agg(run, lambda k: RPOS[k[4]] == "RB", RV_W)
        blk = _rate_block(rbv, L["rb"], metric_prefix="def_rb", basis="RB/FB designed runs allowed")
        blk["line_yds"] = fld(rbv[RV["ly"]], rbv[RV["n"]], "line_yds_per_carry", "def_rb_line_yds", _r(L["rb"][RV["ly"]], L["rb"][RV["n"]]),
                              basis="line yards allowed per RB carry")
        blk["first_down"] = fld(rbv[RV["fd"]], rbv[RV["n"]], "share", "def_rb_fd", _r(L["rb"][RV["fd"]], L["rb"][RV["n"]]), basis="first downs allowed per RB carry")
        blk["short_yardage_conv"] = fld(rbv[RV["sy_conv"]], rbv[RV["sy"]], "share", "def_rb_sy_conv", _r(L["rb"][RV["sy_conv"]], L["rb"][RV["sy"]]),
                                        basis="3rd/4th & 1-2 RB runs converted against it")
        own, raw = _own(blk, L["rb"])
        blk["by_direction"] = {DIRS[d]: _rate_block(_agg(run, lambda k, d=d: RPOS[k[4]] == "RB" and k[0] == d, RV_W), L["rb_dir"][d],
                                                    own=own, base_from=raw, metric_prefix="def_dir", basis=f"RB runs {DIRS[d].replace('_', ' ')} allowed")
                               for d in range(1, 8)}
        blk["by_side"] = {s: _rate_block(_agg(run, lambda k, s=s: RPOS[k[4]] == "RB" and SIDE_OF.get(k[0]) == s, RV_W), L["rb_side"][s],
                                         own=own, base_from=raw, metric_prefix="def_side",
                                         basis={"edge": "edge (end gap) RB runs allowed", "tackle": "tackle-gap RB runs allowed",
                                                "interior": "interior (guard / middle) RB runs allowed"}[s])
                          for s in SIDES}
        blk["by_box"] = {BOX[b]: _rate_block(_agg(run, lambda k, b=b: RPOS[k[4]] == "RB" and k[1] == b, RV_W), L["rb_box"][b],
                                             own=own, base_from=raw, metric_prefix="def_box", basis=f"RB runs vs its {BOX[b]} boxes")
                         for b in (1, 2, 3)}
        out["vs_rb"] = blk
        # QB designed runs (not sneaks) and sneaks allowed -- reported, unshrunk (the OE block is the QB matchup)
        qv = _agg(run, lambda k: RPOS[k[4]] == "QB", RV_W)
        sv = _agg(run, lambda k: RPOS[k[4]] == "QBS", RV_W)
        out["vs_qb_designed"] = {"carries": round(qv[0], 2), "ypc": fld(qv[RV["yds"]], qv[0], "yds_per_carry", basis="QB designed runs allowed"),
                                 "success": fld(qv[RV["succ"]], qv[0], "share", basis="QB designed runs allowed")}
        out["vs_sneak"] = {"att": round(sv[0], 2), "conv": fld(sv[RV["sy_conv"]], sv[RV["sy"]], "share", basis="3rd/4th & 1-2 sneaks converted against it")}
        # what it lines up in (every snap)
        ba = _agg(box, lambda k: True, BV_W)
        lb = L["box_all"]

        def bshare(v, col, den, metric, lgv, base=None, basis=""):
            return fld(v[BV[col]], v[BV[den]], "share", metric, lgv, base=base, basis=basis)
        lg_light, lg_heavy = _r(lb[BV["light"]], lb[BV["box_n"]]), _r(lb[BV["heavy"]], lb[BV["box_n"]])
        bx = {"light": bshare(ba, "light", "box_n", "def_light_share", lg_light, basis="<=6 in the box, every snap"),
              "heavy": bshare(ba, "heavy", "box_n", "def_heavy_share", lg_heavy, basis="8+ in the box, every snap"),
              "mean": fld(ba[BV["box_sum"]], ba[BV["box_n"]], "defenders", "def_box_mean", _r(lb[BV["box_sum"]], lb[BV["box_n"]]),
                          basis="mean box count, every snap"),
              "source": "NGS defenders_in_box (participation), FTN n_defense_box where NGS has none (2026: FTN only)"}
        own_h = bx["heavy"]["value"]
        own_h_raw = bx["heavy"]["raw"]
        own_l, own_l_raw = bx["light"]["value"], bx["light"]["raw"]
        by_op = {}
        for o in range(1, len(OPERS) - 1):
            v = _agg(box, lambda k, o=o: k[0] == o, BV_W)
            lo = L["box_op"][o]
            hr = _r(lo[BV["heavy"]], lo[BV["box_n"]])
            lr_ = _r(lo[BV["light"]], lo[BV["box_n"]])
            off_h = (hr - lg_heavy) if (hr is not None and lg_heavy is not None) else None
            off_l = (lr_ - lg_light) if (lr_ is not None and lg_light is not None) else None
            by_op[OPERS[o]] = {
                "snaps": round(v[0], 1),
                "heavy": fld(v[BV["heavy"]], v[BV["box_n"]], "share", "def_box_op_heavy_dev", None if own_h is None or off_h is None else own_h + off_h,
                             base=None if own_h_raw is None or off_h is None else own_h_raw + off_h, basis=f"8+ box vs {OPERS[o]} personnel"),
                "light": fld(v[BV["light"]], v[BV["box_n"]], "share", "def_box_op_light_dev", None if own_l is None or off_l is None else own_l + off_l,
                             base=None if own_l_raw is None or off_l is None else own_l_raw + off_l, basis=f"<=6 box vs {OPERS[o]} personnel"),
                "mean_box": fld(v[BV["box_sum"]], v[BV["box_n"]], "defenders", basis=f"mean box vs {OPERS[o]}"),
                "nickel": fld(v[BV["nickel"]], v[BV["pers_n"]], "share", basis=f"5 DB vs {OPERS[o]}"),
                "dime": fld(v[BV["dime"]], v[BV["pers_n"]], "share", basis=f"6+ DB vs {OPERS[o]}"),
                "base": fld(v[BV["base"]], v[BV["pers_n"]], "share", basis=f"<=4 DB vs {OPERS[o]}")}
        bx["by_offense_personnel"] = by_op
        by_sg = {}
        for s, nm in ((1, "shotgun"), (0, "under_center")):
            v = _agg(box, lambda k, s=s: k[1] == s, BV_W)
            ls = L["box_sg"][s]
            hr = _r(ls[BV["heavy"]], ls[BV["box_n"]])
            off_h = (hr - lg_heavy) if (hr is not None and lg_heavy is not None) else None
            by_sg[nm] = {"snaps": round(v[0], 1),
                         "heavy": fld(v[BV["heavy"]], v[BV["box_n"]], "share", "def_box_sg_dev",
                                      None if own_h is None or off_h is None else own_h + off_h,
                                      base=None if own_h_raw is None or off_h is None else own_h_raw + off_h, basis=f"8+ box vs {nm}"),
                         "mean_box": fld(v[BV["box_sum"]], v[BV["box_n"]], "defenders", basis=f"mean box vs {nm}")}
        bx["by_formation"] = by_sg
        rv_ = _agg(box, lambda k: k[2] == 1, BV_W)
        bx["on_designed_runs"] = {"heavy": fld(rv_[BV["heavy"]], rv_[BV["box_n"]], "share", basis="8+ box on the designed runs it faced"),
                                  "mean_box": fld(rv_[BV["box_sum"]], rv_[BV["box_n"]], "defenders", basis="mean box on designed runs faced")}
        out["box"] = bx
        lp = L["box_all"]
        out["personnel"] = {"nickel": fld(ba[BV["nickel"]], ba[BV["pers_n"]], "share", "def_nickel_share", _r(lp[BV["nickel"]], lp[BV["pers_n"]]),
                                          basis="5 DB on the field, every snap (participation; none 2026)"),
                            "dime": fld(ba[BV["dime"]], ba[BV["pers_n"]], "share", "def_dime_share", _r(lp[BV["dime"]], lp[BV["pers_n"]]),
                                        basis="6+ DB, every snap"),
                            "base": fld(ba[BV["base"]], ba[BV["pers_n"]], "share", "def_base_share", _r(lp[BV["base"]], lp[BV["pers_n"]]),
                                        basis="<=4 DB, every snap")}
        out["front_proxy"] = {"kind": "proxy",
                              "basis": "DL count from participation's defense personnel string: bodies, not alignment (odd/even, over/under are in no free source)",
                              "dl3_or_fewer": fld(ba[BV["dl3"]], ba[BV["dl_n"]], "share", "def_dl3_share", _r(lp[BV["dl3"]], lp[BV["dl_n"]]),
                                                  kind="proxy", basis="3 or fewer DL ('3-man front' proxy)"),
                              "dl4": fld(ba[BV["dl4"]], ba[BV["dl_n"]], "share", "def_dl4_share", _r(lp[BV["dl4"]], lp[BV["dl_n"]]),
                                         kind="proxy", basis="4 DL ('4-man front' proxy)"),
                              "dl5_plus": fld(ba[BV["dl5"]], ba[BV["dl_n"]], "share", kind="proxy", basis="5+ DL (goal line / heavy)")}
        # tackling and contact (PFR)
        lt = L["team"]
        dp = [0.0] * len(DP_NAMES)
        dp_g = 0.0
        for w, r in games:
            if r["dpfr"]:
                dp_g += w
                for i, v in enumerate(r["dpfr"]):
                    dp[i] += v * w
        ldp, ldg = L["dpfr"], L["dpfr_games"]
        out["contact"] = {"ybc_allowed": fld(tl[TL["rb_pfr_ybc"]], tl[TL["rb_pfr_car"]], "yds_per_carry", "def_ybc",
                                             _r(lt[TL["rb_pfr_ybc"]], lt[TL["rb_pfr_car"]]), basis="opponent RBs' PFR yards before contact per carry"),
                          "yac_allowed": fld(tl[TL["rb_pfr_yac"]], tl[TL["rb_pfr_car"]], "yds_per_carry", "def_yac",
                                             _r(lt[TL["rb_pfr_yac"]], lt[TL["rb_pfr_car"]]), basis="opponent RBs' PFR yards after contact per carry"),
                          "missed_tackles_pg": fld(dp[2], dp_g, "per_game", "def_mtkl_pg", _r(ldp[2], ldg), basis="PFR missed tackles per game (all plays)"),
                          "missed_tackle_rate": fld(dp[2], dp[1] + dp[2], "share", "def_mtkl_rate", _r(ldp[2], ldp[1] + ldp[2]),
                                                    basis="PFR missed / (tackles + missed), all plays")}
        # RB receiving allowed
        out["vs_rb_receiving"] = {
            "tgt_per_db": fld(tl[TL["rb_tgt"]], tl[TL["db"]], "share", "def_rb_tgt_db", _r(lt[TL["rb_tgt"]], lt[TL["db"]]), basis="RB targets per opponent dropback"),
            "ypt": fld(tl[TL["rb_recy"]], tl[TL["rb_tgt"]], "yds_per_target", "def_rb_ypt", _r(lt[TL["rb_recy"]], lt[TL["rb_tgt"]]), basis="yards per RB target allowed"),
            "yac_rec": fld(tl[TL["rb_yac"]], tl[TL["rb_rec"]], "yds_per_reception", "def_rb_yac_rec", _r(lt[TL["rb_yac"]], lt[TL["rb_rec"]]), basis="YAC per RB reception allowed"),
            "catch": fld(tl[TL["rb_rec"]], tl[TL["rb_tgt"]], "share", "def_rb_catch", _r(lt[TL["rb_rec"]], lt[TL["rb_tgt"]]), basis="RB catch rate allowed")}
        out["per_game"] = {"rb_carries": fld(tl[TL["rb_des"]], wsum, "per_game", basis="RB designed runs faced per game"),
                           "rb_rush_yds": fld(tl[TL["rb_des_yds"]], wsum, "yds_per_game", basis="RB designed-run yards allowed per game"),
                           "opp_plays": fld(tl[TL["snaps"]], wsum, "per_game", basis="opponent plays per game")}
        return self._stamp(out, "def")

    # ---- defense vs the quarterback's legs (opponent-adjusted) ----------------------------------
    def defense_qb(self, team, rates_from=None):
        """Observed over expected against the QBs it faced: each game's expected
        = the facing QB's own rate (leave-one-out: every game of his in the
        snapshot but this one, shrunk toward the league by its measured k) x
        that game's opportunities. The ratio is shrunk toward 1 by the k
        measured on it (in expected events). `rates_from` (a snapshot) supplies
        the QB rates instead of this one (the split-half measurement)."""
        L = self.league()
        src = rates_from or self
        qt = src._qb_totals()
        lq = src.league()["qb_line"]
        games = self._games(team, side="def")
        spec = (  # name, obs field, opportunity field, rate numerator / denominator, metric of the QB rate, unit basis
            ("scrambles", "scr", "db", "scr", "db", "qb_scr_rate", "scrambles per dropback faced"),
            ("scramble_yds", "scr_yds", "db", "scr_yds", "db", "qb_scr_yds_db", "scramble yards per dropback faced"),
            ("designed_runs", "dsg", "expo", "dsg", "expo", "qb_dsg_rate", "QB designed runs per exposure play"),
            ("designed_yds", "dsg_yds", "expo", "dsg_yds", "expo", "qb_dsg_yds_rate", "QB designed-run yards per exposure play"),
            ("explosive_runs", "x10_rush", "expo", "x10_rush", "expo", "qb_x10_rate", "QB runs of 10+ (designed or scramble) per exposure play"),
            ("rush_tds", "rush_td", "expo", "rush_td", "expo", "qb_td_rate", "QB rushing TDs per exposure play"),
            ("red_zone_rushes", "rz_rush", "expo", "rz_rush", "expo", "qb_rz_rate", "QB rushes inside the 20 per exposure play"),
            ("hit_sack_rate", "hs", "db", "hs", "db", "qb_hit_sack_rate",
             "sacks + QB hits per dropback faced (pbp qb_hit; every season incl. 2026) -- a proxy: a hit or a sack is not a pressure"),
            ("containment_proxy", "scr_yds_press", "press", "scr_yds_press", "press", "qb_scr_yds_press",
             "scramble yards per PRESSURED dropback (2023+ charting) -- a containment proxy"))
        out = {"team": team, "games": {"weighted": round(sum(w for w, r in games), 2)},
               "basis": "observed / expected against each facing QB's own leave-one-out rate x the game's opportunities"}
        for name, ob, opp, rn, rd, qmetric, basis in spec:
            obs = exp = opp_n = 0.0
            lg_rate = _r(lq[QL[rn]], lq[QL[rd]])
            kq = k_for(qmetric)[0] if qmetric else None
            for w, r in games:
                for pid, ql in r["qbs"].items():
                    if ql[QL[opp]] <= 0:
                        continue
                    tot = qt.get(pid)
                    num = den = 0.0
                    if tot is not None:
                        # leave this game out of the QB's own rate, at the weight it carries in the rate's snapshot
                        wo = _weight_of(src, r)
                        num = tot[QL[rn]] - wo * ql[QL[rn]]
                        den = tot[QL[rd]] - wo * ql[QL[rd]]
                    if lg_rate is None:
                        continue
                    k = kq if kq else 0.0
                    rate = (num + k * lg_rate) / (den + k) if (den + k) > 0 else lg_rate
                    obs += w * ql[QL[ob]]
                    exp += w * ql[QL[opp]] * rate
                    opp_n += w * ql[QL[opp]]
            kd = "proxy" if name in ("containment_proxy", "hit_sack_rate") else "observed"
            out[name] = {"observed": round(obs, 2), "expected": round(exp, 2),
                         "oe_ratio": fld(obs, exp, "ratio", f"def_{name}_oe", 1.0, kind=kd,
                                         basis=f"{basis}: observed / expected (1 = what these QBs do elsewhere)"),
                         "oe_per_opp": fld(obs - exp, opp_n, "per_opportunity", kind=kd, basis=f"{basis}: (observed - expected) per opportunity"),
                         "raw_rate": fld(obs, opp_n, "rate", kind=kd, basis=f"{basis}, raw (not adjusted)"),
                         "opportunities": round(opp_n, 2)}
        out["containment_proxy"]["kind"] = "proxy"
        # pressure and blitz (the same plays; participation pressure ends at 2025, PFR's per-game counts stand in)
        q = _sum_lines([(w, ql) for w, r in games for ql in r["qbs"].values()], None, QL_W)
        lqq = L["qb_line"]
        out["pressure"] = fld(q[QL["press"]], q[QL["press_n"]], "share", "def_pressure", _r(lqq[QL["press"]], lqq[QL["press_n"]]),
                              basis="pressured share of opponent dropbacks (participation, 2023+ charting incl. scrambles)")
        out["pressure_pfr"] = fld(q[QL["pfr_pressured"]], _pfr_db_def(games), "share", "def_pressure_pfr",
                                  _r(lqq[QL["pfr_pressured"]], _pfr_db_league(self)), kind="proxy",
                                  basis="opponent QBs' PFR times pressured / their dropbacks (PFR charting; the 2026 pressure source)")
        out["blitz"] = fld(q[QL["blitz"]], q[QL["blitz_n"]], "share", "def_blitz", _r(lqq[QL["blitz"]], lqq[QL["blitz_n"]]),
                           basis="5+ pass rushers on opponent dropbacks (FTN, 2022+)")
        out["scramble_rate_raw"] = fld(q[QL["scr"]], q[QL["db"]], "share", basis="scrambles per opponent dropback, NOT adjusted")
        out["scramble_ypa_allowed"] = fld(q[QL["scr_yds"]], q[QL["scr"]], "yds_per_carry",
                                          basis="yards per scramble the QBs it faced gained, raw (not opponent-adjusted; per-dropback OE above)")
        return self._stamp(out, "dq")

    # ---- the offensive line ------------------------------------------------------------------------
    def offense_line(self, team):
        """What the line produces on RB designed runs (line yards, stuffs,
        success, YBC by PFR, by gap), and the as-of starting five with the
        week's absences (the injury report of the cutoff week, published before
        kickoff, and the last game's snaps)."""
        L = self.league()
        run = _merge(self.tallies, lambda T: (T.off.get(team) or {}).get("run"))
        games = self._games(team, side="off")
        tl = _sum_lines([(w, r["team"]) for w, r in games], None, TL_W)
        rbv = _agg(run, lambda k: RPOS[k[4]] == "RB", RV_W)
        lr = L["rb"]
        out = {"team": team, "games": {"current": sum(1 for w, r in games if w == 1.0), "weighted": round(sum(w for w, r in games), 2)}}
        out["line_yds"] = fld(rbv[RV["ly"]], rbv[RV["n"]], "line_yds_per_carry", "ol_line_yds", _r(lr[RV["ly"]], lr[RV["n"]]),
                              basis="line yards per RB designed run by the stated convention (120% of losses, 100% of 0-4, 50% of 5-10, 0 past 10; unadjusted, not FO's official ALY)")
        out["stuff"] = fld(rbv[RV["stuff"]], rbv[RV["n"]], "share", "ol_stuff", _r(lr[RV["stuff"]], lr[RV["n"]]), basis="RB designed runs for <= 0")
        out["success"] = fld(rbv[RV["succ"]], rbv[RV["n"]], "share", "ol_success", _r(lr[RV["succ"]], lr[RV["n"]]), basis="RB designed-run EPA success")
        out["ybc"] = fld(tl[TL["rb_pfr_ybc"]], tl[TL["rb_pfr_car"]], "yds_per_carry", "ol_ybc",
                         _r(L["team"][TL["rb_pfr_ybc"]], L["team"][TL["rb_pfr_car"]]), basis="its RBs' PFR yards before contact per carry")
        own_s, raw_s = out["success"]["value"], out["success"]["raw"]
        lg_s = _r(lr[RV["succ"]], lr[RV["n"]])
        gaps = {}
        for s in SIDES:
            v = _agg(run, lambda k, s=s: RPOS[k[4]] == "RB" and SIDE_OF.get(k[0]) == s, RV_W)
            ls = L["rb_side"][s]
            off = (_r(ls[RV["succ"]], ls[RV["n"]]) - lg_s) if (lg_s is not None and ls[RV["n"]]) else None
            gaps[s] = {"carries": round(v[0], 1),
                       "success": fld(v[RV["succ"]], v[0], "share", "ol_gap_success_dev", None if own_s is None or off is None else own_s + off,
                                      base=None if raw_s is None or off is None else raw_s + off, basis=f"RB success, {s} runs"),
                       "line_yds": fld(v[RV["ly"]], v[0], "line_yds_per_carry", basis=f"line yards, {s} runs")}
        out["by_gap"] = gaps
        out["separation"] = ("the LINE: line_yds, ybc, stuff, by_gap; the BACK: rb().rushing.back (PFR yards after contact, "
                             "broken tackles, NGS RYOE) -- a model reading both never counts the blocking twice")
        out["starters"] = self._ol_starters(team, games)
        # every lineman (T / G / C, starter or not) the cutoff week's report lists Out or Doubtful: needs no PFR snaps
        rep = self._report(team)
        ol_rep = None if rep is None else [v for v in rep.values() if v[0] in R.OL_POS]
        out["ol_listed_out"] = {"value": None if ol_rep is None else sum(1 for v in ol_rep if v[1] >= 2), "unit": "count",
                                "kind": "observed", "n": 0 if ol_rep is None else len(ol_rep),
                                "basis": "offensive linemen (T / G / C, any) listed Out or Doubtful on this week's injury report "
                                         "(published before kickoff); None with no report for the week" if rep is not None
                                         else "no injury report for this week in the aux store"}
        return self._stamp(out, "ol")

    def _ol_starters(self, team, games):
        """The as-of starting five: the five offensive linemen with the most PFR
        snaps over the team's last four games THIS SEASON in the snapshot
        (strictly before the cutoff; none before its first game); `out_report`: how many of them the cutoff week's injury
        report lists Out or Doubtful (pre-kickoff); `missed_last`: how many took
        under half the snaps of the team's last game (IR players leave the
        report, so this catches them)."""
        # this season's games only: at week 1 the "last game" would be last season's week 18, where
        # starters rest (21 week-1 rows read five of five starters missing before this rule)
        last = [r for w, r in games if r["season"] == self.season][-4:]
        if not last or not any(r["ol"] for r in last):
            why = ("no game this season before the cutoff" if not last else "no PFR snap counts for the team's recent games")
            empty = {"value": None, "unit": "count", "kind": "observed", "n": 0, "basis": why}
            return {"five": [], "out_report": dict(empty), "missed_last_game": dict(empty),
                    "continuity": dict(empty, unit="share"), "games_read": len(last)}
        tot = {}
        for r in last:
            for key, (pos, s_n) in r["ol"].items():
                tot[key] = tot.get(key, 0) + s_n
        five = [k for k, v in sorted(tot.items(), key=lambda kv: -kv[1])[:5]]
        lastg = last[-1]
        team_snaps = lastg["team"][TL["pfr_team_snaps"]] or max((s for _p, s in lastg["ol"].values()), default=0)
        missed = sum(1 for k in five if (lastg["ol"].get(k, ("", 0))[1] < 0.5 * team_snaps))
        rep = self._report(team)
        out_rep = None
        if rep is not None:
            out_rep = sum(1 for k in five if isinstance(k, int) and rep.get(k, ("", 0, 0))[1] >= 2)
        cont = sum(tot[k] for k in five) / sum(tot.values()) if tot else None
        return {"five": [D.gsis_str(k) if isinstance(k, int) else str(k) for k in five],
                "out_report": {"value": out_rep, "unit": "count", "kind": "observed", "n": 5,
                               "basis": "of the as-of starting five, listed Out or Doubtful on this week's injury report (published before kickoff)"
                               if rep is not None else "no injury report for this week in the aux store"},
                "missed_last_game": {"value": missed, "unit": "count", "kind": "observed", "n": 5,
                                     "basis": "of the as-of starting five, under half the team's snaps in its last game (PFR)"},
                "continuity": {"value": None if cont is None else round(cont, 4), "unit": "share", "kind": "observed",
                               "n": len(last), "basis": "share of the team's OL snaps over its last four games taken by those five"},
                "games_read": len(last)}

    def _report(self, team):
        """The cutoff week's report for a team: None when the store holds no
        report for that WEEK (status then reads "unknown", the OL and RB-room
        absences None -- the review's M1: a week-8 board before nflverse carried
        the week's report read every back "active"), and None too when the week's
        report exists but lists NOBODY from the team: a playing team always has
        rows (2 of 1,818 playing team-games in 2023-26 had none -- a data gap, so
        "unknown", not "active"; the data re-check's Opus R2). Otherwise the
        team's {gsis: (position, status code, practice code)}; a player of a
        listed team who is not on it is active."""
        aux = self.aux.get(self.season)
        if not aux or not aux.get("injuries"):
            return None
        wk = aux["injuries"].get(self.week)
        if wk is None:
            key = (self.season, self.week)
            if key not in _NOREPORT_SEEN:
                _NOREPORT_SEEN.add(key)
                import errlog
                errlog.note("RUSH-noreport", msg=f"{self.season} week {self.week}: no injury report in the aux store")
            return None
        rows = wk.get(team)
        if not rows:
            key = (self.season, self.week, team)
            if key not in _NOREPORT_SEEN:
                _NOREPORT_SEEN.add(key)
                import errlog
                errlog.note("RUSH-noreport", msg=f"{self.season} week {self.week}: the report lists nobody from {team}")
            return None
        return rows

    def status(self, pid, team):
        """The player's game status on the cutoff week's injury report (published
        before kickoff): 'questionable' / 'doubtful' / 'out' / 'active' (listed
        with no game status, or not listed) / 'unknown' (no report in the store)."""
        rep = self._report(team)
        if rep is None:
            wk_held = bool(self.aux.get(self.season)) and self.week in (self.aux[self.season].get("injuries") or {})
            why = (f"{self.season} week {self.week}'s report lists nobody from {team} (read as a gap)" if wk_held
                   else f"no injury report for {self.season} week {self.week} in the aux store")
            return {"value": "unknown", "source": why, "kind": "observed"}
        v = rep.get(pid)
        code = v[1] if v else 0
        return {"value": {0: "active", 1: "questionable", 2: "doubtful", 3: "out"}[code],
                "practice": R.PRACTICE[v[2]] if v else "", "listed": bool(v),
                "source": f"nflverse injuries {self.season} week {self.week} (final report, pre-kickoff)", "kind": "observed"}

    def rb_room_out(self, team, exclude=None):
        """The carry share (team-scoped, the team's last four games) of its backs
        the cutoff week's report lists Out or Doubtful -- the work that has to go
        somewhere. A back on injured reserve is not on the report (and has no
        recent share)."""
        rep = self._report(team)
        games = self._games(team, side="off")[-RECENT:]
        if rep is None or not games:
            return {"value": None, "unit": "share", "kind": "observed", "n": 0, "basis": "no report or no recent games"}
        team_des = sum(r["team"][TL["des"]] for w, r in games)
        out_des = 0.0
        names = []
        for pid, (pos, code, _pr) in rep.items():
            if code < 2 or pos not in ("RB", "FB") or pid == exclude:
                continue
            d = sum((r["players"].get(pid) or [0.0] * PL_W)[PL["des"]] for w, r in games)
            if d:
                out_des += d
                names.append(D.gsis_str(pid))
        return {"value": round(out_des / team_des, 4) if team_des else None, "unit": "share", "kind": "observed",
                "n": round(team_des, 1), "out": names,
                "basis": "carry share over the team's last four games of the backs listed Out/Doubtful this week"}

    # ---- team run context -----------------------------------------------------------------------------
    def team_context(self, team):
        L = self.league()
        games = self._games(team, side="off")
        wsum = sum(w for w, r in games)
        tl = _sum_lines([(w, r["team"]) for w, r in games], None, TL_W)
        lt = L["team"]

        def rr(s):
            return tl[TL[f"des_{s}"]], tl[TL[f"des_{s}"]] + tl[TL[f"db_{s}"]], _r(lt[TL[f"des_{s}"]], lt[TL[f"des_{s}"]] + lt[TL[f"db_{s}"]])
        out = {"team": team, "games": {"current": sum(1 for w, r in games if w == 1.0), "weighted": round(wsum, 2)}}
        for s, nm, basis in (("neutral", "neutral_run_rate", "designed runs / (designed runs + dropbacks), win probability 20-80% outside the two-minute drill"),
                             ("lead", "leading_run_rate", f"run rate leading by {LEAD}+ (two-minute drill out)"),
                             ("trail", "trailing_run_rate", f"run rate trailing by {LEAD}+ (two-minute drill out)"),
                             ("rz", "red_zone_run_rate", "run rate inside the 20"),
                             ("early", "early_down_run_rate", "run rate on 1st and 2nd down")):
            n_, d_, lg = rr(s)
            out[nm] = fld(n_, d_, "share", f"tm_{s}_run", lg, basis=basis)
        out["plays_pg"] = fld(tl[TL["snaps"]], wsum, "per_game", "tm_plays_pg", _r(lt[TL["snaps"]], L["team_games"]),
                              basis="pace: offensive plays per game (kneels and spikes out)")
        out["qb_keep_share"] = fld(tl[TL["qb_des"]] + tl[TL["qb_sneak"]], tl[TL["des"]], "share", "tm_qb_keep",
                                   _r(lt[TL["qb_des"]] + lt[TL["qb_sneak"]], lt[TL["des"]]),
                                   basis="QB designed runs + sneaks / the team's designed runs")
        out["rpo_rate"] = fld(tl[TL["rpo_plays"]], tl[TL["ftn_plays"]], "share", "tm_rpo", _r(lt[TL["rpo_plays"]], lt[TL["ftn_plays"]]),
                              basis="FTN RPO plays / FTN-charted plays (2022+)")
        out["rpo_split"] = {"runs": round(tl[TL["rpo_runs"]], 1), "qb_keeps": round(tl[TL["rpo_qb_keeps"]], 1),
                            "passes": round(tl[TL["rpo_passes"]], 1),
                            "basis": "FTN RPO plays by what they became: a designed run (handoff or QB keep) or a dropback (never a rush)"}
        out["rb_carries_pg"] = fld(tl[TL["rb_des"]], wsum, "per_game", basis="RB designed runs per game")
        out["designed_runs_pg"] = fld(tl[TL["des"]], wsum, "per_game", basis="designed runs per game")
        return self._stamp(out, "tc")


# ---- small helpers used above -------------------------------------------------------------------------
def _pfr_db(games, pid):
    return sum(w * r["qbs"][pid][QL["db"]] for w, r in games if r["qbs"][pid][QL["pfr_rows"]])


def _pfr_db_def(games):
    return sum(w * ql[QL["db"]] for w, r in games for ql in r["qbs"].values() if ql[QL["pfr_rows"]])


def _pfr_db_league(S):
    tot = 0.0
    for w, T in S.tallies:
        for lst in T.team_games.values():
            for side, r in lst:
                if side == "off":
                    tot += sum(w * ql[QL["db"]] for ql in r["qbs"].values() if ql[QL["pfr_rows"]])
    return tot


def _weight_of(S, r):
    """The weight a game's record carries in snapshot S (0 when S does not hold it)."""
    for w, T in S.tallies:
        if T.season == r["season"] and (r["season"], r["week"]) in T.weeks:
            return w
    return 0.0


# ---- building a snapshot ------------------------------------------------------------------------------------
def build(stores, auxes, season, week, positions, decay=DECAY, back=BACK):
    """The rushing snapshot as of (season, week) from {season: store} and
    {season: aux}: the cutoff season's games with week < `week`, and `back`
    prior seasons whole."""
    cur = stores.get(int(season))
    current = season_tallies(cur, auxes.get(int(season)), positions, max_week=int(week)) if cur else None
    priors = []
    for s in range(int(season) - back, int(season)):
        st = stores.get(s)
        if st:
            priors.append(season_tallies(st, auxes.get(s), positions))
    return RushSnapshot(season, week, current, priors, aux=auxes, decay=decay)


def flatten(profile, prefix=""):
    """{path: field} for every field (a dict carrying "unit" and "kind") in a
    profile -- what the dataset and the reliability measurement read."""
    out = {}
    for k, v in (profile or {}).items():
        if isinstance(v, dict):
            if "unit" in v and "kind" in v and "value" in v:
                out[prefix + k] = v
            else:
                out.update(flatten(v, prefix + k + "."))
    return out
