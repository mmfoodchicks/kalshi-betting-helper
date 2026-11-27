"""Every registered test in one place: what it decides, when it may be read, and where it stands (2026-11-27).

The owner's backlog item 9 (a research dashboard). By late November the project held a dozen pre-registered
reads -- the NFL deep weight's, the coaching engine's R1 / confirmatory / DFS / R2 reads, the classic field
protocol, the accuracy batch's dated reads, the market anchor -- each in its own report, each with its own rule
for when it may be opened. This module is the one list. The Research tab reads it (`/api/research/registry`).

An entry names its registration (a committed file, pinned by the guard), the read script when there is one,
the earliest moment it may be read (`not_before`, UTC; for a file that states one, the file's own figure is
read and wins), what else it waits for (`waits_for`, plain words: graded games, the owner's exports), and the
file a finished read leaves (`result`). The status is computed, never typed:

  read          the result file is committed (its verdict is in the report named)
  sealed        not before `not_before`
  waiting       past its date (or undated) but waiting on data the server cannot count
  readable      past its date with nothing else named -- run its read script, once, from a clean tree

Nothing here reads a sealed row or runs a read. Dates are UTC; the tab shows them in Mountain time.
"""
import datetime
import json
import os

import errlog

_HERE = os.path.dirname(os.path.abspath(__file__))

# kind: "registered" (a hashed rule, read once) / "dated" (a read the reports fixed a date or a count for)
REGISTRY = [
    {"id": "nfl_deep_weight", "area": "NFL game model", "kind": "registered",
     "title": "NFL deep-engine weight: 0.30 against 0 on the live record",
     "decides": "keeps nfl_game_sim._DEEP_WEIGHT at 0.30 only if the deep engine beats the drive model by 2 SE; "
                "inconclusive sends it to 0 (the owner, 2026-11-26)",
     "registration": "research/reports/nfl_deep_weight_rule.md", "script": "research/nfl_deep_weight_read.py",
     "waits_for": "100 graded nfl_deep games (84 graded on 2026-11-26); expect 0 at the read -- 100 games cannot "
                  "reach 2 SE at the effect seen",
     "report": "research/reports/nfl_deep_weight_rule.md", "result": None},
    {"id": "nfl_deep_prospective", "area": "NFL game model", "kind": "registered",
     "title": "NFL deep weight's way back: the prospective test (fresh games only)",
     "decides": "0.30 returns only by O'Brien-Fleming looks at 130 / 260 / 390 fresh games (Student t on weeks - 1), "
                "futility at look 2, powered 80% at 0.015 nats a game",
     "registration": "research/reports/nfl_deep_weight_rule.md", "script": "research/nfl_deep_weight_read.py",
     "waits_for": "the 100-game read sending the weight to 0, then 130 fresh graded games",
     "report": "research/reports/nfl_deep_weight_rule.md", "result": None},
    {"id": "coach_r1", "area": "Coaching engine", "kind": "registered",
     "title": "Coaching R1 (game management) against the current engine",
     "decides": "all five criteria: scenarios, end-of-half fidelity, level, late-state win probability (2 SE), "
                "pre-game non-inferiority. A FAIL is final for coach-r1.0",
     "registration": "research/data/nfl_coach/prereg_r1.json", "script": "research/nfl_coach_r1_read.py",
     "not_before": "2027-01-06T06:00:00Z", "waits_for": "2026 weeks 13-18 (sealed), at least 90 games",
     "report": "research/reports/nfl_coach_r1.md", "result": "research/data/nfl_coach/read_r1.json"},
    {"id": "coach_c4", "area": "Coaching engine", "kind": "registered",
     "title": "Coaching R1, criterion 4 confirmed on fresh games (only if R1 fails criterion 4 alone)",
     "decides": "family-wise one-sided 5% across R1 and this test; two O'Brien-Fleming looks on the 2027 regular "
                "season (after weeks 9 and 18), futility at look 1, no third test",
     "registration": "research/data/nfl_coach/prereg_c4.json", "script": "research/nfl_coach_c4_confirm.py",
     "not_before": "2027-11-01T00:00:00Z", "waits_for": "R1's read failing criterion 4 alone; 2027 week 9",
     "report": "research/reports/nfl_coach_followup.md", "result": None},
    {"id": "coach_dfs", "area": "Coaching engine", "kind": "registered",
     "title": "Coaching engine in the DFS worlds: player points against production's worlds",
     "decides": "offence CRPS paired by game, 2.5% one-sided, guardrails on correlations, PIT, defenses, kickers; "
                "PASS / NOT ESTABLISHED / WORSE, one look",
     "registration": "research/data/nfl_coach/prereg_dfs.json", "script": "research/nfl_coach_dfs_read.py",
     "not_before": "2027-01-06T06:00:00Z",
     "waits_for": "its --dry rehearsal on the PC (weeks 2-5) BEFORE 2027-01-06; 2026 weeks 13-18, 85+ games",
     "report": "research/reports/nfl_coach_followup.md", "result": None},
    {"id": "coach_r2_h1", "area": "Coaching engine", "kind": "registered",
     "title": "Coaching R2: who gets the ball by situation",
     "decides": "carries + targets log score gain over 2 SE, carries above 0, targets and the three realism "
                "figures not worse by more than 1 SE",
     "registration": "research/data/nfl_coach/prereg_r2.json", "script": "research/nfl_coach_r2_read.py",
     "not_before": "2027-01-06T06:00:00Z",
     "waits_for": "R1's read committed first; the owner's ruling on sharing weeks 13-18 with R1 (else 2027 weeks 1-9)",
     "report": "research/reports/nfl_coach_r2_evidence.md", "result": "research/data/nfl_coach/read_r2_h1.json"},
    {"id": "coach_r2_h2", "area": "Coaching engine", "kind": "registered",
     "title": "Coaching R2: in-game adaptation of the pass call",
     "decides": "the decision-level gain over 2 SE on 2026 weeks 13-18 plus 2027 (500+ team-games)",
     "registration": "research/data/nfl_coach/prereg_r2.json", "script": "research/nfl_coach_r2_read.py",
     "not_before": "2028-01-13T06:00:00Z", "waits_for": "the 2027 regular season",
     "report": "research/reports/nfl_coach_r2_evidence.md", "result": "research/data/nfl_coach/read_r2_h2.json"},
    {"id": "cl_field2", "area": "DFS field", "kind": "registered",
     "title": "Classic field second pass: the Kalshi-moved optimizer share (F1c) and F1",
     "decides": "the owner's five duplication criteria (dk_mix_classic.OWNER) plus three smaller diagnostics, on the "
                "first two Sunday main-slate Millionaires locking after the registration, each read once",
     "registration": "research/data/cl_field2/protocol.json", "script": "research/cl_field2.py",
     "waits_for": "the owner's DraftKings standings exports for the 2026-11-29 and 2026-12-06 Millionaires",
     "report": "research/reports/cl_field2_protocol.md", "result": None},
    {"id": "dk_dup_criteria", "area": "DFS field", "kind": "registered",
     "title": "Duplication calibrated: any field model, per format",
     "decides": "aggregate collision within 2x, 10 of the real top 20 builds in the model's top 200, copies within "
                "[0.5, 2], unique-share and histogram checks, the rest no worse -- on EVERY validation contest",
     "registration": "research/reports/dk_duplication.md", "script": "research/dk_duplication.py",
     "waits_for": "5 new contests per format built after 2026-11-26, each board's stamped figure and its export",
     "report": "research/reports/dk_duplication.md", "result": None},
    {"id": "nfl_market_anchor", "area": "NFL game model", "kind": "dated",
     "title": "NFL market anchor (0.3 sim + 0.7 close), registered after it failed its own rule",
     "decides": "game_xref section 8.3: weeks 9-18, read once",
     "registration": "research/reports/game_xref.md", "script": None,
     "not_before": "2027-01-12T00:00:00Z", "waits_for": "2026 weeks 9-18",
     "report": "research/reports/game_xref.md", "result": None},
    {"id": "acc_b1", "area": "Accuracy batch (13.2)", "kind": "dated",
     "title": "B1: the NBA's last-season prior", "decides": "accuracy_gaps 13.2",
     "registration": "research/reports/accuracy_gaps.md", "script": None,
     "not_before": "2027-01-02T00:00:00Z", "waits_for": None,
     "report": "research/reports/accuracy_gaps.md", "result": None},
    {"id": "acc_b4_h2", "area": "Accuracy batch (13.2)", "kind": "dated",
     "title": "B4 / H2: the NBA and NHL deep weights on the live shadow record", "decides": "accuracy_gaps 13.2",
     "registration": "research/reports/accuracy_gaps.md", "script": None,
     "waits_for": "400 graded shadow games a league", "report": "research/reports/accuracy_gaps.md", "result": None},
    {"id": "acc_c1", "area": "Accuracy batch (13.2)", "kind": "dated",
     "title": "C1: DraftKings' book against Kalshi as a level (nba/nhl/nfl/cfb_book)",
     "decides": "only there may the book replace Kalshi as a level",
     "registration": "research/reports/accuracy_gaps.md", "script": None,
     "waits_for": "10 dates and 150 games a league", "report": "research/reports/accuracy_gaps.md", "result": None},
    {"id": "acc_n1", "area": "Accuracy batch (13.2)", "kind": "dated",
     "title": "N1: ESPN's projections beside Sleeper's", "decides": "accuracy_gaps 13.2",
     "registration": "research/reports/accuracy_gaps.md", "script": None,
     "not_before": "2027-01-06T00:00:00Z", "waits_for": "2026 week 18 + 48 h",
     "report": "research/reports/accuracy_gaps.md", "result": None},
    {"id": "acc_c3", "area": "Accuracy batch (13.2)", "kind": "dated",
     "title": "C3: the season simulators (NFL playoffs, college CFP) against the analytic resolution",
     "decides": "replay 2024 and 2025 week by week: the compressed engine's win totals, playoff / CFP odds against "
                "Phi(margin/18) college and Phi(margin/13.2) NFL, scored on real outcomes; the analytic resolution "
                "replaces the engine only if it wins both seasons by > 2 SE",
     "registration": "research/reports/accuracy_gaps.md", "script": None, "due_by": "2026-12-06T00:00:00Z",
     "waits_for": None, "report": "research/reports/accuracy_gaps.md", "result": None},
    {"id": "acc_g2", "area": "Accuracy batch (13.2)", "kind": "dated",
     "title": "G2: a market-implied college preseason prior (stored opening lines)",
     "decides": "K_m chosen on 2024, tested on 2025 AND 2026 weeks 1-6, > 2 SE each season; ships for the 2027 "
                "preseason", "registration": "research/reports/accuracy_gaps.md", "script": None,
     "waits_for": None, "report": "research/reports/accuracy_gaps.md", "result": None},
    {"id": "acc_n2", "area": "Accuracy batch (13.2)", "kind": "dated",
     "title": "N2: the rushing prop shape by role", "decides": "weeks 10-16 rushing rungs, > 2 SE by game",
     "registration": "research/reports/accuracy_gaps.md", "script": None,
     "not_before": "2026-12-30T00:00:00Z", "waits_for": "2026 week 16's rungs graded",
     "report": "research/reports/accuracy_gaps.md", "result": None},
    {"id": "acc_tennis", "area": "Accuracy batch (13.2)", "kind": "dated",
     "title": "Tennis: the blend per series", "decides": "accuracy_gaps 13.2",
     "registration": "research/reports/accuracy_gaps.md", "script": None,
     "not_before": "2027-01-01T00:00:00Z", "waits_for": "December's pre-game rows (chosen on rows before "
     "2026-12-01)", "report": "research/reports/accuracy_gaps.md", "result": None},
    {"id": "weather_stage2", "area": "DFS inputs", "kind": "registered",
     "title": "Weather stage 2: wind only, the frozen pre-game forecast", "decides": "nfl_weather.md",
     "registration": "research/reports/nfl_weather.md", "script": "research/nfl_weather_stage2.py",
     "not_before": "2027-01-06T00:00:00Z", "waits_for": "2026 week 18",
     "report": "research/reports/nfl_weather.md", "result": None},
    {"id": "nfl_rush", "area": "Matchups", "kind": "registered",
     "title": "NFL rushing model: 35 decisions over 8 markets", "decides": "read 2026-10-29: no group passed its "
     "2025 gate -- nothing ships (a statement about power)",
     "registration": "research/data/nfl_rush_model/prereg.json", "script": None, "waits_for": None,
     "report": "research/reports/nfl_rush_model.md", "result": "research/data/nfl_rush_model/validate.json"},
    {"id": "nfl_matchup_model", "area": "Matchups", "kind": "registered",
     "title": "NFL matchup model (defense against Sleeper's line)", "decides": "read: only a passing-TD game-script "
     "term ships", "registration": "research/data/nfl_matchup_model/prereg.json", "script": None, "waits_for": None,
     "report": "research/reports/nfl_matchup_model.md", "result": "research/data/nfl_matchup_model/prereg.json"},
    {"id": "nfl_matchup_rush_cv", "area": "Matchups", "kind": "registered",
     "title": "NFL rushing calibration (line-dependent fix)", "decides": "read: failed its own 2025 gate",
     "registration": "research/data/nfl_matchup_model/rush_cv/prereg.json", "script": None, "waits_for": None,
     "report": "research/reports/nfl_matchup_model.md",
     "result": "research/data/nfl_matchup_model/rush_cv/prereg.json"},
]

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, _ISO).replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return datetime.datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp()


def _file_not_before(path):
    """A registration file's own not-before date, when it states one at its top level (it wins)."""
    if not path or not path.endswith(".json"):
        return None
    try:
        with open(os.path.join(_HERE, path)) as fh:
            d = json.load(fh)
        v = d.get("not_before_utc") if isinstance(d, dict) else None
        return v if isinstance(v, str) else None
    except (OSError, ValueError) as e:
        errlog.note("RREG-read", e, path=path)
        return None


def status(entry, now=None):
    """The entry with its computed status: read / sealed / waiting / readable (and `due_soon`)."""
    now = datetime.datetime.now(datetime.timezone.utc).timestamp() if now is None else float(now)
    e = dict(entry)
    nb = _file_not_before(e.get("registration")) or e.get("not_before")
    e["not_before"] = nb
    t = _ts(nb)
    res = e.get("result")
    done = bool(res) and os.path.exists(os.path.join(_HERE, res))
    if done:
        e["status"] = "read"
    elif t is not None and now < t:
        e["status"] = "sealed"
    elif e.get("waits_for"):
        e["status"] = "waiting"
    else:
        e["status"] = "readable"
    due = _ts(e.get("due_by"))
    e["due_soon"] = bool(due is not None and not done and now <= due <= now + 14 * 86400)
    e["overdue"] = bool(due is not None and not done and now > due)
    e["registration_exists"] = os.path.exists(os.path.join(_HERE, e.get("registration") or ""))
    return e


def listing(now=None):
    rows = [status(e, now) for e in REGISTRY]
    order = {"readable": 0, "waiting": 1, "sealed": 2, "read": 3}
    rows.sort(key=lambda r: (not r["due_soon"] and not r["overdue"], order.get(r["status"], 9),
                             _ts(r.get("not_before")) or 0))
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"entries": rows, "counts": counts,
            "generated": datetime.datetime.now(datetime.timezone.utc).strftime(_ISO)}
