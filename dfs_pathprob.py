"""The exact probability that the field sampler builds one given lineup.

Stage 3C said an exact construction probability was "not possible" because
the sampler's state carries the set of players and the money already spent,
so paths never collapse and a dynamic programme over all lineups at once
cannot exist. That much is true. It is the wrong question, though: nobody
needs every lineup's probability simultaneously. For ONE fixed nine-man
lineup the paths that build it are few, because every choice that is not a
member of that lineup kills the path immediately.

What the sampler does, in order, is fixed: the quarterback; a Bernoulli for
whether the row avoids a defense facing him; a stack count from the published
distribution; that many of his pass-catchers; a Bernoulli bring-back from his
opponent; then backs, tight end, receivers and the flex, which never take a
pass-catcher from either side of his game; the defense last. So for a target
lineup the partition is almost forced -- his teammates at receiver and tight
end can ONLY have come from the stack, a catcher on his opponent can only be
the bring-back, everyone else can only be a fill -- and what is left to
enumerate is the order inside each phase and the two Bernoullis. A few
thousand paths, not a combinatorial explosion.

Each pick is Gumbel-max over the eligible set, which is a softmax, so a
path's probability is the product of exp(logit_i) / sum over eligible, and
the lineup's probability is the sum over paths. The eligible set is where all
the difficulty lives: salary reserves for the defense, per-row floors for
every unfilled slot, the rule that a rostered back puts a defense off limits.
Rather than re-implement any of that -- a copy of production logic inside
research is precisely the mistake that made Stage 2F score the wrong lineups
-- this DRIVES the real `classic_sample`: the random draws are forced down a
chosen path and the pick function is replaced by one that takes the forced
player and records the exact log-probability the real eligibility mask gives
it. The numbers therefore come from the sampler itself, and any change to the
sampler changes them automatically.

    p, detail = lineup_probability(players, names, beta, kappa, ...)
"""
import itertools
import math

try:
    import numpy as np
except ImportError:                 # the server has no numpy and never calls this
    np = None

import dfs_tourney as T


def available():
    return np is not None


class _Forced:
    """Stands in for the generator inside one `classic_sample` call with
    n=1, handing back the draws that send it down the chosen path."""

    def __init__(self, careful, k_stack, bring):
        self.careful, self.k_stack, self.bring = careful, k_stack, bring
        self._randoms = 0

    def random(self, n):
        # the two Bernoullis, in the order the sampler draws them: the
        # careful flag first (careful is u >= dst_own_p, so a high draw means
        # careful), then the bring-back (drawn when u < bring_p, so a low
        # draw means it happens)
        self._randoms += 1
        if self._randoms == 1:
            return np.array([1.0 if self.careful else 0.0])
        return np.array([0.0 if self.bring else 1.0])

    def choice(self, k, size=None, p=None):
        return np.array([self.k_stack])

    def gumbel(self, size=None):
        return np.zeros(size)


class _Picker:
    """Replaces `_gumbel_pick` for the duration of one forced run: hands back
    the next player the path calls for and records log P(that player | the
    real eligibility mask at this point).

    Stages the sampler runs but this row sits out -- the third back attempt
    once both back slots are full, the flex when a stack overflowed into it --
    come through with an empty eligibility row, so they consume nothing. That
    is why the path only has to name the players it wants, in order, and not
    count how many times the sampler will ask."""

    def __init__(self, wants):
        self.wants = list(wants)
        self.at = 0
        self.logp = 0.0
        self.dead = False

    def __call__(self, logits, elig, rng):
        row = elig[0]
        if not row.any():
            return np.array([-1], dtype=np.int64)      # nobody eligible: a stage this row skips
        if self.at >= len(self.wants):
            self.dead = True                            # the sampler wants a pick the path has not got
            return np.array([-1], dtype=np.int64)
        want = self.wants[self.at]
        self.at += 1
        if not row[want]:
            self.dead = True                            # the path wants a player who is not eligible
            return np.array([-1], dtype=np.int64)
        lg = logits[row].astype(np.float64)
        m = lg.max()
        self.logp += float(logits[want]) - float(m + math.log(np.exp(lg - m).sum()))
        return np.array([want], dtype=np.int64)


def _phases(players, ids, qb_i):
    """Split the target lineup into the phases that can possibly have
    produced each man: the stack (his own catchers), the bring-back (a
    catcher on his opponent), and the fills."""
    qteam = players[qb_i].get("team")
    qopp = players[qb_i].get("opp")
    stack, bring, rbs, wrs, tes, dst = [], [], [], [], [], []
    for i in ids:
        if i == qb_i:
            continue
        p = players[i]
        pos = (p.get("pos") or "").upper()
        if pos == "DST":
            dst.append(i)
        elif pos in ("WR", "TE") and p.get("team") == qteam:
            stack.append(i)
        elif pos in ("WR", "TE") and p.get("team") == qopp:
            bring.append(i)
        elif pos == "RB":
            rbs.append(i)
        elif pos == "WR":
            wrs.append(i)
        elif pos == "TE":
            tes.append(i)
    return qteam, qopp, stack, bring, rbs, wrs, tes, dst


def _orders(qb_i, stack, bring, rbs, wrs, tes, dst):
    """Every pick order that could end at this lineup, in the order the
    sampler asks: the quarterback, his stacked catchers, the bring-back, then
    backs, tight end, receivers, the flex, and the defense last.

    The flex is whichever man is left once the named slots are full, and only
    a back or a receiver may sit there (the sampler's flex column is RB/WR),
    so the choice of who goes there is small and explicit. Anything this
    enumeration gets wrong shows up as a path whose finished lineup is not
    the target, and those are dropped rather than counted."""
    flex_options = [None] + list(rbs) + list(wrs)
    seen = set()
    for flex in flex_options:
        main_rbs = [i for i in rbs if i != flex]
        main_wrs = [i for i in wrs if i != flex]
        if len(main_rbs) != 2:
            continue
        for st in itertools.permutations(stack):
            for rb_o in itertools.permutations(main_rbs):
                for te_o in itertools.permutations(tes):
                    for wr_o in itertools.permutations(main_wrs):
                        seq = ([qb_i] + list(st) + list(bring) + list(rb_o)
                               + list(te_o) + list(wr_o)
                               + ([flex] if flex is not None else []) + list(dst))
                        key = tuple(seq)
                        if key in seen:
                            continue
                        seen.add(key)
                        yield seq


def lineup_probability(players, names, beta, kappa, cap=50000,
                       stack_dist=None, bring_p=None, dst_own_p=None,
                       rules=False, exclude=None, max_paths=200000):
    """P(one draw of `classic_sample` is exactly this lineup), summed over
    every path that builds it. Returns (probability, detail)."""
    if np is None:
        raise RuntimeError("numpy is required")
    stack_dist = T.CL_STACK_DIST if stack_dist is None else stack_dist
    bring_p = T.CL_BRING_BACK if bring_p is None else bring_p
    dst_own_p = T.CL_DST_VS_OWN_QB if dst_own_p is None else dst_own_p
    ids, missing = T.probe_index(names, players)
    if ids is None:
        return 0.0, {"missing": missing}
    qb_list = [i for i in ids if (players[i].get("pos") or "").upper() == "QB"]
    if len(qb_list) != 1:
        return 0.0, {"missing": ["exactly one quarterback"]}
    qb_i = qb_list[0]
    qteam, qopp, stack, bring, rbs, wrs, tes, dst = _phases(players, ids, qb_i)
    if len(bring) > 1 or len(dst) != 1:
        return 0.0, {"missing": ["a lineup this sampler cannot build"]}
    dist = np.asarray(stack_dist, dtype=np.float64)
    if rules:
        dist = dist.copy()
        dist[0] = 0.0
    dist = dist / dist.sum()
    k = len(stack)
    if k >= len(dist) or not dist[k:].any():
        return 0.0, {"missing": [f"a {k}-man stack this field never draws"]}
    # A lineup with k stacked catchers can also come from a LARGER drawn stack
    # count whose extra picks found nobody left to take -- his fourth
    # pass-catcher already used, or priced out once the defense's salary is
    # reserved. Measured against three million draws, leaving those paths out
    # put the most popular lineups about 20% light. The picker only lets such
    # a path live if the sampler really had nobody eligible at that step, so a
    # bigger count cannot be claimed for free.
    k_range = [kk for kk in range(k, len(dist)) if dist[kk] > 0]
    total, paths, kept = 0.0, 0, 0
    orig = T._gumbel_pick
    for careful in (True, False):
        # P(careful) = 1 - dst_own_p by construction: careful = (u >= dst_own_p)
        p_care = (1.0 - dst_own_p) if careful else dst_own_p
        if p_care <= 0:
            continue
        for bring_drawn in ((True,) if bring else (True, False)):
            p_bring = bring_p if bring_drawn else (1.0 - bring_p)
            if p_bring <= 0:
                continue
            want_bring = list(bring) if bring_drawn else []
            for k_drawn in k_range:
              for seq in _orders(qb_i, stack, want_bring, rbs, wrs, tes, dst):
                  paths += 1
                  if paths > max_paths:
                      raise RuntimeError("too many paths; this lineup needs a smarter enumeration")
                  pk = _Picker(seq)
                  try:
                      T._gumbel_pick = pk
                      rng = _Forced(careful, k_drawn, bring_drawn)
                      out = T.classic_sample(players, 1, rng, beta, kappa, cap=cap,
                                             stack_dist=stack_dist, bring_p=bring_p,
                                             dst_own_p=dst_own_p, rules=rules, exclude=exclude)
                  finally:
                      T._gumbel_pick = orig
                  if pk.dead or not len(out) or sorted(int(x) for x in out[0]) != sorted(ids):
                      continue
                  kept += 1
                  total += p_care * p_bring * float(dist[k_drawn]) * math.exp(pk.logp)
    return total, {"paths_tried": paths, "paths_alive": kept, "stack": len(stack),
                   "bring_back": len(bring), "qteam": qteam, "qopp": qopp}


def expected_copies(p, entries):
    """How many of a contest's entries are this exact lineup, in expectation."""
    return float(p) * float(entries)
