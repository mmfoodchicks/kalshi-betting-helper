"""What the coaching engine's registered reads share: file hashes, the
registration block's own hash, the clean-tree check, and the error-control
arithmetic (Student t tails, a two-look O'Brien-Fleming boundary, power).
RESEARCH. The R1 read (research/nfl_coach_r1_read.py) is pinned and does not
import this; the confirmatory and DFS reads do.
"""
import hashlib
import json
import math
import os
import subprocess

from research.nfl_deep_weight_read import norm_cdf, t_cdf, t_ppf      # pure math, no I/O

__all__ = ("norm_cdf", "t_cdf", "t_ppf")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG_DIR = os.path.join(ROOT, "research", "data", "nfl_coach")


def sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def block_hash(reg):
    body = json.dumps({k: v for k, v in reg.items() if k != "block_sha256"}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def seal(reg):
    reg["block_sha256"] = block_hash(reg)
    return reg


def dirty():
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    return out.returncode != 0 or bool(out.stdout.strip())


def common_refusal(reg, now_utc, not_before, is_dirty, out_path):
    """The refusals every read shares, in the R1 read's order: the date, a
    second read, a dirty tree, a registration that does not match itself, a
    pinned file that differs. None when the read may go on."""
    if now_utc < not_before:
        return f"before the registered date ({not_before})"
    if os.path.exists(out_path):
        return f"already read ({os.path.relpath(out_path, ROOT)} exists): the sealed rows are read once"
    if dirty() if is_dirty is None else is_dirty:
        return "dirty tree: commit or remove every change first"
    if block_hash(reg) != reg["block_sha256"]:
        return "the registration block does not match its own hash"
    for rel, h in reg["files"].items():
        if sha(rel) != h:
            return f"{rel} differs from its registered sha256"
    return None


# ---- two looks, O'Brien-Fleming shape ---------------------------------------------
def _phi(z):
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def cross_prob(c, frac=0.5, drift=0.0, futility=False):
    """P(reject) for looks at information `frac` and 1 with boundaries
    c / sqrt(frac) and c. `drift` is the final look's mean z. `futility`: a
    first-look z at or below 0 stops the test without rejecting."""
    b1 = c / math.sqrt(frac)
    rho = math.sqrt(frac)
    m1 = drift * rho
    p1 = 1.0 - norm_cdf(b1 - m1)
    lo = 0.0 if futility else m1 - 9.0
    n = 4000
    h = (b1 - lo) / n
    acc = 0.0
    s = math.sqrt(1.0 - frac)
    for i in range(n + 1):
        z1 = lo + i * h
        # Z2 | Z1 = z1 is normal with mean drift + rho (z1 - m1) and SD sqrt(1 - frac)
        f = _phi(z1 - m1) * (1.0 - norm_cdf((c - drift - rho * (z1 - m1)) / s))
        acc += f * (1 if i in (0, n) else 4 if i % 2 else 2)
    return p1 + acc * h / 3.0


def obf_c(alpha, frac=0.5):
    """The final-look z boundary whose two-look crossing probability is `alpha`
    under no effect (futility NOT credited: the boundary holds whether or not
    the futility stop is obeyed)."""
    lo, hi = 1.0, 4.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if cross_prob(mid, frac) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def drift_for_power(c, power, frac=0.5):
    lo, hi = 0.0, 8.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        if cross_prob(c, frac, mid, futility=True) < power:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def one_look_power(effect, se, df, alpha):
    """P(mean / se > t critical) for a one-sided t test, normal approximation
    to the noncentral t (df here is 90+)."""
    return 1.0 - norm_cdf(t_ppf(1.0 - alpha, df) - effect / se)
