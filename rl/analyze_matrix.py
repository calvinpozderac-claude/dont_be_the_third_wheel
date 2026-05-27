#!/usr/bin/env python3
"""
Rigorous payout matrix analysis for Don't Be the Third Wheel.

Checks:
  1. Pure Nash Equilibria (strategy collapse detection)
  2. Mixed strategy NE via best-response dynamics (10 random starts)
  3. Mean / SD by role under realistic strategy profiles
  4. Card 2 / 5 / 3 / 6 ability interactions
  5. Side-by-side comparison of matrix variants
"""

import numpy as np
from itertools import product

# ── Matrix definitions ─────────────────────────────────────────────────────────
# Key: (TW_act, N1_act, N2_act)  0=PS 1=MM
# Val: (TW_score, N1_score, N2_score)

CURRENT = {
    (0,0,0): ( 0, +1, +1),   # PS/PS/PS
    (0,1,0): ( 0, -1, +1),   # PS/MM/PS — lone MM punished
    (0,0,1): ( 0, +1, -1),   # PS/PS/MM — symmetric
    (0,1,1): ( 0, +2, +2),   # PS/MM/MM — normals coordinate
    (1,0,0): (-2, +1, +1),   # MM/PS/PS — TW crashes alone
    (1,1,0): (+2, +2,  0),   # MM/MM/PS — TW+N1 connect
    (1,0,1): (+2,  0, +2),   # MM/PS/MM — symmetric
    (1,1,1): (-2, +2, +2),   # MM/MM/MM — chaos, TW loses
}

# Variant A: quiet date = 0 baseline; lone MM is neutral (no embarrassment penalty)
VARIANT_A = {
    (0,0,0): ( 0,  0,  0),   # PS/PS/PS — nothing happens, nobody scores
    (0,1,0): ( 0,  0,  0),   # PS/MM/PS — brave but unrequited, neutral
    (0,0,1): ( 0,  0,  0),   # PS/PS/MM — symmetric
    (0,1,1): ( 0, +2, +2),   # PS/MM/MM — unchanged
    (1,0,0): (-2, +1, +1),   # MM/PS/PS — unchanged
    (1,1,0): (+2, +2,  0),   # MM/MM/PS — unchanged
    (1,0,1): (+2,  0, +2),   # MM/PS/MM — unchanged
    (1,1,1): (-2, +2, +2),   # MM/MM/MM — unchanged
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def label(a): return "MM" if a else "PS"

def with_card(m, card):
    """Return matrix with card modifier applied to MM/MM/MM outcome."""
    mm = dict(m)
    if   card == 2: mm[(1,1,1)] = ( 0, +2, +2)
    elif card == 5: mm[(1,1,1)] = (-4, +2, +2)
    return mm

def pure_ne(m):
    """Return list of pure strategy Nash Equilibria."""
    ne = []
    for profile in product([0,1], repeat=3):
        tw, n1, n2 = profile
        s = m[profile]
        if (all(m[(t,n1,n2)][0] <= s[0] for t in [0,1]) and
            all(m[(tw,n,n2)][1] <= s[1] for n in [0,1]) and
            all(m[(tw,n1,n)][2] <= s[2] for n in [0,1])):
            ne.append(profile)
    return ne

def is_strict(m, profile):
    tw, n1, n2 = profile
    s = m[profile]
    return (all(t == tw  or m[(t,n1,n2)][0] < s[0] for t in [0,1]) and
            all(n == n1  or m[(tw,n,n2)][1] < s[1] for n in [0,1]) and
            all(n == n2  or m[(tw,n1,n)][2] < s[2] for n in [0,1]))

def expected_scores(m, tw_p, n1_p, n2_p):
    """Expected score (mean, SD) for each role given mixed strategies."""
    means = [0.0, 0.0, 0.0]
    ex2   = [0.0, 0.0, 0.0]
    for tw, n1, n2 in product([0,1], repeat=3):
        p = ((tw_p if tw else 1-tw_p) *
             (n1_p if n1 else 1-n1_p) *
             (n2_p if n2 else 1-n2_p))
        for r, sc in enumerate(m[(tw,n1,n2)]):
            means[r] += p * sc
            ex2[r]   += p * sc * sc
    return [(means[r], (max(0, ex2[r] - means[r]**2))**0.5) for r in range(3)]

def br_step(m, probs):
    """One gradient step of smooth best-response dynamics."""
    tw_p, n1_p, n2_p = probs
    ev = np.zeros((3, 2))   # ev[player][action]
    for tw, n1, n2 in product([0,1], repeat=3):
        acts  = [tw, n1, n2]
        joint = [tw_p if tw else 1-tw_p,
                 n1_p if n1 else 1-n1_p,
                 n2_p if n2 else 1-n2_p]
        p_joint = joint[0] * joint[1] * joint[2]
        for r, sc in enumerate(m[(tw, n1, n2)]):
            if joint[r] > 1e-10:
                ev[r][acts[r]] += (p_joint / joint[r]) * sc
    return ev[:, 1] - ev[:, 0]   # EV(MM) - EV(PS) for each player

def br_dynamics(m, n_iter=3000, lr=0.08, seed=None):
    """Gradient ascent toward Nash Equilibrium; returns final (tw_mm, n1_mm, n2_mm)."""
    rng   = np.random.RandomState(seed)
    probs = rng.uniform(0.15, 0.85, 3)
    for _ in range(n_iter):
        delta = br_step(m, probs)
        probs = np.clip(probs + lr * delta, 0.0, 1.0)
    return tuple(round(float(p), 2) for p in probs)

# ── Main analysis ──────────────────────────────────────────────────────────────

def analyze(name, m):
    W = 62
    print(f"\n{'='*W}")
    print(f"  {name}")
    print(f"{'='*W}")

    # ── 1. Pure NE ──────────────────────────────────────────────────────────────
    ne = pure_ne(m)
    print(f"\n[1] Pure strategy Nash Equilibria:")
    for p in ne:
        tw, n1, n2 = p
        s    = m[p]
        kind = "STRICT" if is_strict(m, p) else "weak  "
        print(f"    ({label(tw)}/{label(n1)}/{label(n2)}) [{kind}]  "
              f"TW={s[0]:+d}  N1={s[1]:+d}  N2={s[2]:+d}")
    if not ne:
        print("    (none — only mixed-strategy NE)")

    has_allps = (0,0,0) in ne
    print(f"    Strategy-collapse risk (all-PS is NE): "
          f"{'YES ⚠' if has_allps else 'none ✓'}")

    # ── 2. Mean / SD table ──────────────────────────────────────────────────────
    print(f"\n[2] Expected score by role (mean ± SD) across strategy profiles:")
    print(f"    {'Profile':<32}  {'TW μ':>5} {'σ':>5}  {'N μ':>5} {'σ':>5}  {'gap':>4}")
    scenarios = [
        ("50/50 uniform random",      0.50, 0.50, 0.50),
        ("All-PS collapse",           0.00, 0.00, 0.00),
        ("All-MM collapse",           1.00, 1.00, 1.00),
        ("TW-shy  (TW=0.2)",          0.20, 0.50, 0.50),
        ("N coordinate (N=0.7)",      0.20, 0.70, 0.70),
        ("Everyone cautious (N=0.3)", 0.25, 0.30, 0.30),
        ("NE-like: PS/MM/MM",         0.00, 1.00, 1.00),
    ]
    for lbl, tw_p, n1_p, n2_p in scenarios:
        st   = expected_scores(m, tw_p, n1_p, n2_p)
        tw_m, tw_s = st[0]
        n_m  = (st[1][0] + st[2][0]) / 2
        n_s  = (st[1][1] + st[2][1]) / 2
        gap  = n_m - tw_m
        print(f"    {lbl:<32}  {tw_m:>+5.2f} {tw_s:>5.2f}  {n_m:>+5.2f} {n_s:>5.2f}  {gap:>+4.2f}")

    # ── 3. Card ability interactions ────────────────────────────────────────────
    st_base  = expected_scores(m, 0.5, 0.5, 0.5)
    st_card2 = expected_scores(with_card(m, 2), 0.5, 0.5, 0.5)
    st_card5 = expected_scores(with_card(m, 5), 0.5, 0.5, 0.5)

    print(f"\n[3] Card modifier impact on TW expected score (50/50 play):")
    print(f"    Base (no modifier): μ={st_base[0][0]:+.3f}  σ={st_base[0][1]:.3f}")
    print(f"    Card 2 (safe MM/MM/MM): μ={st_card2[0][0]:+.3f}  σ={st_card2[0][1]:.3f}"
          f"  Δμ={st_card2[0][0]-st_base[0][0]:+.3f}")
    print(f"    Card 5 (hurt MM/MM/MM): μ={st_card5[0][0]:+.3f}  σ={st_card5[0][1]:.3f}"
          f"  Δμ={st_card5[0][0]-st_base[0][0]:+.3f}")

    # Card 3: peek at one player before choosing (best-response to known action)
    # Simulated: TW peeks at N1 (50/50 N2); N1 peeks at TW (50/50 N2)
    tw_peek = sum(0.25 * max(m[(1,n1,n2)][0], m[(0,n1,n2)][0])
                  for n1, n2 in product([0,1], repeat=2))
    n1_peek = sum(0.25 * max(m[(tw,1,n2)][1], m[(tw,0,n2)][1])
                  for tw, n2 in product([0,1], repeat=2))
    print(f"\n    Card 3 (peek) — best-response EV gain vs blind 50/50:")
    print(f"    TW peeks N1 : {st_base[0][0]:+.3f} → {tw_peek:+.3f}  Δ={tw_peek-st_base[0][0]:+.3f}")
    print(f"    N1 peeks TW : {st_base[1][0]:+.3f} → {n1_peek:+.3f}  Δ={n1_peek-st_base[1][0]:+.3f}")

    # Card 6: show player reveals move → target best-responds (TW 50/50)
    # Scenario: N1 shows to N2. N2 sees N1 action and picks best response.
    # N1 chooses each action 50/50 (committed before N2 responds).
    n2_reveal_ev = 0.0
    n1_exposed_ev = 0.0
    for tw, n1 in product([0,1], repeat=2):
        n2_mm = sum(0.5 * m[(tw2, n1, 1)][2] for tw2 in [0,1])
        n2_ps = sum(0.5 * m[(tw2, n1, 0)][2] for tw2 in [0,1])
        n2_br = 1 if n2_mm >= n2_ps else 0
        n2_reveal_ev  += 0.25 * m[(tw, n1, n2_br)][2]
        n1_exposed_ev += 0.25 * m[(tw, n1, n2_br)][1]
    print(f"\n    Card 6 (show) — N1 reveals to N2, N2 best-responds (TW 50/50):")
    print(f"    N2 (sees move) : {st_base[2][0]:+.3f} → {n2_reveal_ev:+.3f}  Δ={n2_reveal_ev-st_base[2][0]:+.3f}")
    print(f"    N1 (exposed)   : {st_base[1][0]:+.3f} → {n1_exposed_ev:+.3f}  Δ={n1_exposed_ev-st_base[1][0]:+.3f}")

    # ── 4. Best-response dynamics ───────────────────────────────────────────────
    print(f"\n[4] Best-response dynamics convergence (10 random starts):")
    tally = {}
    for seed in range(10):
        ep = br_dynamics(m, seed=seed)
        tally[ep] = tally.get(ep, 0) + 1
    for ep, cnt in sorted(tally.items(), key=lambda x: -x[1]):
        tw_p, n1_p, n2_p = ep
        flags = ""
        if tw_p <= 0.05 and n1_p <= 0.05 and n2_p <= 0.05:
            flags = "  ← ALL-PS COLLAPSE ⚠"
        elif tw_p >= 0.95 and n1_p >= 0.95 and n2_p >= 0.95:
            flags = "  ← ALL-MM COLLAPSE ⚠"
        elif n1_p >= 0.95 and n2_p >= 0.95 and tw_p <= 0.1:
            flags = "  ← PS/MM/MM equilibrium ✓"
        print(f"    TW={tw_p:.2f}  N1={n1_p:.2f}  N2={n2_p:.2f}   ({cnt}/10 starts){flags}")

    # ── 5. Desired property check ───────────────────────────────────────────────
    st50 = expected_scores(m, 0.5, 0.5, 0.5)
    tw_mean, tw_sd  = st50[0]
    n_mean = (st50[1][0] + st50[2][0]) / 2
    n_sd   = (st50[1][1] + st50[2][1]) / 2
    print(f"\n[5] Design-goal checks (50/50 baseline):")
    print(f"    TW lower mean than normals : {tw_mean:.3f} < {n_mean:.3f}  {'✓' if tw_mean < n_mean else '✗'}")
    print(f"    TW higher variance         : σ={tw_sd:.3f} > σ={n_sd:.3f}  {'✓' if tw_sd > n_sd else '✗ (equal)' if tw_sd == n_sd else '✗'}")
    coord_bonus = m[(0,1,1)][1] - max(m[(0,1,0)][1], m[(0,0,0)][1])
    print(f"    Coordination bonus (MM+MM vs best alternative): +{coord_bonus}  {'✓' if coord_bonus > 0 else '✗'}")
    print(f"    No all-PS NE (collapse-free): {'✓' if not has_allps else '✗'}")


if __name__ == "__main__":
    print("DON'T BE THE THIRD WHEEL — Payout Matrix Analysis")
    print("Comparing current matrix against Variant A")

    analyze("CURRENT MATRIX", CURRENT)
    analyze("VARIANT A  (quiet-date=0, solo-MM=neutral)", VARIANT_A)

    # ── Quick side-by-side summary ─────────────────────────────────────────────
    W = 62
    print(f"\n{'='*W}")
    print(f"  SIDE-BY-SIDE SUMMARY (50/50 uniform play)")
    print(f"{'='*W}")
    for name, m in [("Current", CURRENT), ("Variant A", VARIANT_A)]:
        st = expected_scores(m, 0.5, 0.5, 0.5)
        tw_m, tw_s = st[0]
        n_m  = (st[1][0]+st[2][0])/2
        n_s  = (st[1][1]+st[2][1])/2
        ne   = pure_ne(m)
        has_allps = (0,0,0) in ne
        print(f"  {name:<12}  TW μ={tw_m:+.3f} σ={tw_s:.3f}  "
              f"N μ={n_m:+.3f} σ={n_s:.3f}  "
              f"all-PS NE={'yes ⚠' if has_allps else 'no ✓'}")
