"""Resolve a tennis tournament's court surface via SerpAPI (Google results).

THE GAP THIS FILLS. Surface is a top driver in tennis, and tennis_data models it
properly -- but only if it is TOLD the surface. For ATP/WTA that comes off ESPN;
for ITF, which is over 90% of a Kalshi tennis board, there was no source at all.
The ITF site is bot-walled, Sofascore 403s, ESPN carries no surface field and no
ITF, and the Wikipedia season pages do not hold the calendars. So every
unidentified stop fell back to a keyword list, and everything else was modelled
surface-agnostically.

Google knows. ITF tournament pages state it plainly ("is played on the Clay",
"Surface: Red Clay"), so one search per tournament resolves it.

WHY THIS IS AFFORDABLE. Surface is a property of a VENUE, not of a week: M25
Koszalin is clay this year and was clay last year. So a tournament is looked up
once and cached forever, and only genuinely new stops ever cost a search. A board
carries ~25-30 distinct tournaments, so the steady state is a handful of searches
a month against a 240/month free tier -- and there is a hard per-run cap below,
so a pathological slate cannot drain the quota.

Set SERPAPI_KEY in the environment. No key -> this module does nothing and the
caller keeps its existing behaviour.
"""

import json
import os
import re
import urllib.parse
import urllib.request

_CACHE_KEY = "tennis_surface_lookups"     # {normalised tournament: surface|""}
_ENDPOINT = "https://serpapi.com/search.json"
_TIMEOUT = 20
# Hard ceiling on searches per build. The quota is small and shared with anything
# else using the key, so a slate full of unknown stops resolves over several days
# rather than in one greedy pass.
MAX_LOOKUPS_PER_RUN = 8
# Refuse to spend the last of the quota, so a human still has searches to debug with.
MIN_SEARCHES_LEFT = 20
# How long a failed lookup stays cached before it is worth one more search.
_NEG_RETRY_DAYS = 21

# Canonical ways the answer actually appears, strongest first. Matching a phrase
# beats counting loose keyword mentions -- "clay" shows up in unrelated text, but
# "played on the clay" is the site stating the fact.
_PATTERNS = (
    (3, re.compile(r"\bsurface\s*[:\-]\s*(?:red\s+|green\s+|indoor\s+|outdoor\s+)*"
                   r"(clay|hard|grass|carpet)", re.I)),
    (3, re.compile(r"\bis\s+played\s+on\s+(?:the\s+)?(?:red\s+|indoor\s+|outdoor\s+)*"
                   r"(clay|hard|grass|carpet)", re.I)),
    (2, re.compile(r"\bon\s+(?:an?\s+|the\s+)?(clay|hard|grass|carpet)\s+court", re.I)),
    (1, re.compile(r"\b(clay|hard|grass|carpet)\s*court", re.I)),
)
_SURFACES = {"clay": "Clay", "grass": "Grass", "hard": "Hard", "carpet": "Hard"}


def key():
    return os.environ.get("SERPAPI_KEY") or ""


def enabled():
    return bool(key())


def _norm(s):
    return " ".join((s or "").lower().split())


def searches_left():
    """Remaining quota, or None if it cannot be read. Checking the account is
    itself free, so this never costs a search."""
    if not enabled():
        return None
    try:
        url = f"https://serpapi.com/account?api_key={urllib.parse.quote(key())}"
        req = urllib.request.Request(url, headers={"User-Agent": "vigil/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            d = json.loads(r.read())
        return d.get("total_searches_left")
    except Exception:
        return None


def _score(text):
    """{surface: weight} from one blob of result text."""
    out = {}
    for weight, pat in _PATTERNS:
        for m in pat.finditer(text or ""):
            surf = _SURFACES.get(m.group(1).lower())
            if surf:
                out[surf] = out.get(surf, 0) + weight
    return out


def _decide(payload):
    """Weighted vote across the answer box, knowledge graph and top results.
    Returns a surface only when one clearly wins -- an ambiguous read is better
    left unknown, since the caller models unknown surfaces agnostically rather
    than guessing."""
    votes = {}

    def add(text, mult=1):
        for s, w in _score(text).items():
            votes[s] = votes.get(s, 0) + w * mult

    ab = payload.get("answer_box") or {}
    add(json.dumps(ab), 2)                       # the box is Google's own answer
    add(json.dumps(payload.get("knowledge_graph") or {}), 2)
    for r in (payload.get("organic_results") or [])[:6]:
        add(f"{r.get('title', '')} {r.get('snippet', '')}")
    if not votes:
        return None, votes
    best = max(votes, key=votes.get)
    top = votes[best]
    runner = max([v for k, v in votes.items() if k != best], default=0)
    # Two conditions, both absolute rather than ratio-based. Results for one
    # tournament routinely mention others, so a rival surface picking up a few
    # loose points is normal and a ratio test throws away good answers because of
    # it. Weights are 3 for a site stating the fact outright and 1-2 for a loose
    # mention, so: >= 6 means at least two canonical statements (or one plus
    # corroboration), and a margin of >= 3 means one clear statement more than
    # anything arguing otherwise. Short of that, return None -- the caller models
    # an unknown surface agnostically, which beats a coin-flip guess.
    if top < 6 or (top - runner) < 3:
        return None, votes
    return best, votes


def _search(tournament):
    # Deliberately NEUTRAL. An earlier version asked "...surface clay or hard",
    # which seeded both words into the results and then counted them as evidence
    # -- the query was voting in its own election. Ask only for the surface.
    #
    # "ITF" is prepended only for names that look like ITF stops (M15/W75/...),
    # since it sharpens those but drags a tour event like "Los Cabos" toward the
    # wrong pages entirely.
    prefix = "ITF " if re.match(r"^[MW]\d{2,3}\b", tournament.strip()) else ""
    q = f"{prefix}{tournament} tennis tournament court surface"
    url = (f"{_ENDPOINT}?engine=google&q={urllib.parse.quote(q)}"
           f"&api_key={urllib.parse.quote(key())}")
    req = urllib.request.Request(url, headers={"User-Agent": "vigil/1.0"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read())


def lookup(tournament, votes_out=None):
    """Surface for one tournament, or None. Costs one search. No caching here --
    callers should go through `resolve`."""
    if not enabled() or not tournament:
        return None
    try:
        surf, votes = _decide(_search(tournament))
    except Exception:
        return None
    if votes_out is not None:
        votes_out.update(votes)
    return surf


def resolve(tournaments, budget=MAX_LOOKUPS_PER_RUN):
    """{tournament: surface} for the names given, using the persistent cache and
    spending at most `budget` searches on names never seen before.

    A name that resolves to nothing is cached as a negative so it is not retried
    every build -- the quota is too small to keep asking a question Google has
    already declined to answer. But negatives EXPIRE (`_NEG_RETRY_DAYS`), because
    a permanent one is a trap: a stop that failed once for a transient reason, or
    under an older and worse query, would never be asked again no matter how much
    the extraction improved. Positives never expire -- a venue's surface does not
    change. Negatives are stored as a timestamp, positives as a string, so the two
    are told apart by type."""
    try:
        import deep_cache
        cache = deep_cache.load(_CACHE_KEY)[0] or {}
    except Exception:
        cache, deep_cache = {}, None

    import time
    now = time.time()
    out, todo = {}, []
    for t in tournaments:
        n = _norm(t)
        if not n:
            continue
        hit = cache.get(n)
        if isinstance(hit, str) and hit:
            out[t] = hit                       # resolved: permanent
        elif isinstance(hit, (int, float)) and (now - hit) < _NEG_RETRY_DAYS * 86400:
            continue                           # negative, still cooling off
        elif t not in todo:
            todo.append(t)                     # new, or a negative that has aged out

    if todo and enabled():
        left = searches_left()
        if left is not None and left <= MIN_SEARCHES_LEFT:
            todo = []                       # protect the remaining quota
        elif left is not None:
            budget = min(budget, max(0, left - MIN_SEARCHES_LEFT))
    else:
        todo = []

    dirty = False
    for t in todo[:budget]:
        surf = lookup(t)
        cache[_norm(t)] = surf or now       # a bare timestamp marks a negative
        dirty = True
        if surf:
            out[t] = surf
    if dirty:
        try:
            import deep_cache as dc
            dc.save(_CACHE_KEY, cache)
        except Exception:
            pass
    return out
