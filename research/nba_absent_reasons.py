"""Of the regulars missing from a game (15+ minutes in his team's previous game,
not in this one), how many does ESPN's box call COACH'S DECISION (active, not
used: no pre-game injury report shows it), how many are inactive with an
injury / rest / personal reason, and how many leave no box line at all?

The NBA matchup engine's rung 6 (teammates out) reads absence off the box in
research and off ESPN's "Out" listings in production; this sizes the gap
(research/reviews/nba_matchup_orchestrator.md, O4).

    python3 -I research/nba_absent_reasons.py <raw summaries dir> 2024 2025

<raw summaries dir>/<season>/<event id>.json[.gz] as research/nba_matchup_fetch.py
writes them. Measured 2026-10-23 on 2024-25 + 2025-26 (3,355 absences): no box
line 57.1% (59.4% by minutes), inactive with a reason 24.7% (26.0%), coach's
decision 18.2% (14.6%)."""
import json, sys, gzip, os, collections
root = sys.argv[1]
c = collections.Counter(); cw = collections.Counter(); seasons = collections.Counter()
for season in sys.argv[2:]:
    d = os.path.join(root, season)
    games = []
    for f in os.listdir(d):
        raw = open(os.path.join(d, f), 'rb').read()
        j = json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)
        if not isinstance(j, dict) or 'header' not in j:
            continue
        comp = j['header']['competitions'][0]
        if str(j['header'].get('season', {}).get('type')) not in ('2',):
            continue
        games.append((comp['date'], j))
    games.sort(key=lambda x: x[0])
    last = {}   # team id -> {pid: minutes} of its previous game
    for date, j in games:
        for t in j['boxscore'].get('players', []):
            tid = t['team']['id']
            cur, dnp = {}, {}
            for st in t.get('statistics', []):
                keys = st.get('keys') or st.get('names') or []
                for a in st.get('athletes', []):
                    pid = (a.get('athlete') or {}).get('id')
                    if pid is None:
                        continue
                    if a.get('didNotPlay') or not a.get('stats'):
                        dnp[pid] = a.get('reason') or 'DNP (no reason)'
                        continue
                    try:
                        m = float(a['stats'][keys.index('minutes')]) if 'minutes' in keys else float(a['stats'][0])
                    except (ValueError, IndexError):
                        m = 0.0
                    cur[pid] = m
            prev = last.get(tid) or {}
            for pid, m in prev.items():
                if m < 15 or pid in cur:
                    continue
                r = dnp.get(pid)
                if r is None:
                    k = 'not in box (not with team / traded / G League)'
                elif r == "COACH'S DECISION":
                    k = "COACH'S DECISION"
                else:
                    k = 'injury/rest/personal reason (inactive)'
                c[k] += 1; cw[k] += m; seasons[season] += 1
            last[tid] = cur
print(dict(seasons)); tot = sum(c.values())
for k, v in c.most_common():
    print(f"{k}: {v} ({v/tot:.1%}); minutes-weighted {cw[k]/sum(cw.values()):.1%}")
