#!/usr/bin/env python3
"""
deep_dive.py — In-depth RL comparison of three payout matrices.

Three matrices:
  old   (canonical / app.py)
  new   (reduced normal reward when TW MMs)
  test  (bystanders get 0 when they don't participate)

For each matrix:
  1. Train 2^16 episodes with rich 12-bin strategy tracking
  2. Report MM rates across (TW/Normal) × (Ahead/Tied/Behind) × (Early/Late)
  3. Grid-search starting bonuses (b0=0, b1, b2) to find the balance point

Usage:
  python3 deep_dive.py               # full run
  python3 deep_dive.py --quick       # 2^13 episodes (smoke test)
  python3 deep_dive.py --no-balance  # skip balance grid, main training only
"""

import argparse, json, os, time
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.distributions import Categorical
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

rng = np.random.default_rng()

# ── payout matrices ────────────────────────────────────────────────────────────
# Keys:  (tw_action, n1_action, n2_action)  — 0=PS, 1=MM
# Values: (tw_reward, n1_reward, n2_reward)

MATRICES = {
    "old": {
        "label": "Old / Canonical",
        "matrix": {
            (0, 0, 0): ( 0,  1,  1),
            (0, 1, 0): ( 0, -1,  1),
            (0, 0, 1): ( 0,  1, -1),
            (0, 1, 1): ( 0,  2,  2),
            (1, 0, 0): (-2,  1,  1),
            (1, 1, 0): ( 2,  2,  0),
            (1, 0, 1): ( 2,  0,  2),
            (1, 1, 1): (-2,  2,  2),
        },
    },
    "new": {
        "label": "New (TW pair = +1 for normal)",
        "matrix": {
            (0, 0, 0): ( 0,  1,  1),
            (0, 1, 0): ( 0, -1,  1),
            (0, 0, 1): ( 0,  1, -1),
            (0, 1, 1): ( 0,  2,  2),
            (1, 0, 0): (-2,  1,  1),
            (1, 1, 0): ( 2,  1,  0),
            (1, 0, 1): ( 2,  0,  1),
            (1, 1, 1): (-2,  1,  1),
        },
    },
    "test": {
        "label": "Test (bystanders get 0)",
        "matrix": {
            (0, 0, 0): ( 0,  1,  1),
            (0, 1, 0): ( 0, -1,  0),
            (0, 0, 1): ( 0,  0, -1),
            (0, 1, 1): ( 0,  2,  2),
            (1, 0, 0): (-2,  0,  0),
            (1, 1, 0): ( 2,  1,  0),
            (1, 0, 1): ( 2,  0,  1),
            (1, 1, 1): (-2,  1,  1),
        },
    },
}

# Player 0 is TW 1×, player 1 is TW 2×, player 2 is TW 3× (per episode, dates shuffled)
BASE_TW_SCHEDULE = np.array([0, 1, 1, 2, 2, 2])

DEFAULTS = dict(
    num_episodes=2**16,   # 65536 — main training
    lr=0.002,
    hidden_size=64,
    entropy_beta=0.10,
    batch_size=64,
    epsilon=0.0,
    log_interval=1024,
)

BALANCE_EPISODES = 2**13   # 8192 per grid point


# ── model ──────────────────────────────────────────────────────────────────────
class Agent(nn.Module):
    def __init__(self, hidden=64):
        super().__init__()
        self.fc1 = nn.Linear(27, hidden)
        self.fc2 = nn.Linear(hidden, hidden)
        self.fc3 = nn.Linear(hidden, 2)
        self.saved_log_probs = []
        self.saved_entropies = []

    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        return F.softmax(self.fc3(x), dim=1)

    def select_action(self, state, epsilon=0.0):
        s = torch.from_numpy(state).float().unsqueeze(0)
        probs = self(s)
        m = Categorical(probs)
        action = m.sample() if np.random.rand() >= epsilon else torch.tensor(np.random.randint(2))
        self.saved_log_probs.append(m.log_prob(action))
        self.saved_entropies.append(m.entropy())
        return action.item()


# ── environment ─────────────────────────────────────────────────────────────────
class Env:
    def __init__(self, payout):
        self.payout = payout

    def reset(self, start_scores=(0, 0, 0)):
        self.tw_schedule = BASE_TW_SCHEDULE.copy()
        rng.shuffle(self.tw_schedule)
        self.scores = np.array(start_scores, dtype=float)
        self.history = np.zeros((3, 6))   # [player, date]
        self.current_date = 0

    def get_ego_state(self, player):
        tw_flags = (self.tw_schedule == player).astype(float)
        p1, p2 = (player + 1) % 3, (player + 2) % 3
        ego_hist = np.concatenate([self.history[player], self.history[p1], self.history[p2]])
        rel_scores = np.array([
            self.scores[player] - self.scores[p1],
            self.scores[player] - self.scores[p2],
            self.scores[p1] - self.scores[p2],
        ])
        return np.concatenate([tw_flags, ego_hist, rel_scores])  # 27-dim

    def step(self, actions):
        d = self.current_date
        tw = self.tw_schedule[d]
        normals = [i for i in range(3) if i != tw]
        key = (actions[tw], actions[normals[0]], actions[normals[1]])
        rews = self.payout[key]
        self.scores[tw] += rews[0]
        self.scores[normals[0]] += rews[1]
        self.scores[normals[1]] += rews[2]
        for i, a in enumerate(actions):
            self.history[i, d] = 1 if a == 1 else -1
        self.current_date += 1
        return self.current_date >= 6


# ── 12-bin strategy tracker ────────────────────────────────────────────────────
# Axes: [tw_flag (0/1)] [score_pos (0=ahead, 1=tied, 2=behind)] [phase (0=early, 1=late)] [action (0=PS, 1=MM)]
def make_bins():
    return np.zeros((2, 3, 2, 2), dtype=np.int64)

_POS = {"ahead": 0, "tied": 1, "behind": 2}

def record(bins, is_tw, scores_i, scores_opp_max, date, action):
    pos = 0 if scores_i > scores_opp_max else (2 if scores_i < scores_opp_max else 1)
    phase = 0 if date < 3 else 1
    bins[int(is_tw), pos, phase, action] += 1

def mm_rate(bins):
    """(2,3,2) array: MM rate per [tw][pos][phase]. NaN when no samples."""
    total = bins.sum(axis=-1)
    mm    = bins[..., 1]
    with np.errstate(invalid="ignore"):
        return np.where(total > 0, mm / total, np.nan)


# ── core training loop ─────────────────────────────────────────────────────────
def train(payout, hp, start_scores=(0, 0, 0), verbose=True, tag=""):
    env = Env(payout)
    agents = [Agent(hp["hidden_size"]) for _ in range(3)]
    opts   = [optim.Adam(a.parameters(), lr=hp["lr"]) for a in agents]
    for o in opts: o.zero_grad()

    n_ep, log_i = hp["num_episodes"], hp["log_interval"]

    recent_wins = np.zeros(3)
    win_history = [[], [], []]
    all_bins    = make_bins()
    mm_tw_hist  = []
    mm_nor_hist = []

    for ep in range(n_ep):
        env.reset(start_scores)
        ep_bins = make_bins()

        done = False
        while not done:
            d = env.current_date
            actions, ctxs = [], []
            for i, ag in enumerate(agents):
                state = env.get_ego_state(i)
                a = ag.select_action(state, hp["epsilon"])
                actions.append(a)
                is_tw = (env.tw_schedule[d] == i)
                opp_max = max(env.scores[j] for j in range(3) if j != i)
                ctxs.append((is_tw, env.scores[i], opp_max, d))
            done = env.step(actions)
            for i, (a, (is_tw, si, om, d_)) in enumerate(zip(actions, ctxs)):
                record(all_bins, is_tw, si, om, d_, a)
                record(ep_bins, is_tw, si, om, d_, a)

        tw_total  = ep_bins[1].sum();  nor_total = ep_bins[0].sum()
        mm_tw_hist.append(ep_bins[1, :, :, 1].sum()  / max(1, tw_total))
        mm_nor_hist.append(ep_bins[0, :, :, 1].sum() / max(1, nor_total))

        for i, ag in enumerate(agents):
            opp = [env.scores[j] for j in range(3) if j != i]
            rew = 2*(env.scores[i] > max(opp)) + (env.scores[i] == max(opp)) - 1
            loss = (sum(-lp * rew for lp in ag.saved_log_probs)
                    - hp["entropy_beta"] * sum(ag.saved_entropies)) / hp["batch_size"]
            loss.backward()
            del ag.saved_log_probs[:]; del ag.saved_entropies[:]

        ms = np.max(env.scores)
        tied = np.sum(env.scores == ms) > 1
        for i in range(3):
            if env.scores[i] == ms:
                recent_wins[i] += 0.5 + 0.5 * (not tied)

        if (ep + 1) % hp["batch_size"] == 0:
            for o in opts: o.step(); o.zero_grad()

        if (ep + 1) % log_i == 0:
            wr = recent_wins / log_i
            for i in range(3): win_history[i].append(float(wr[i]))
            if verbose:
                tw_r  = float(np.mean(mm_tw_hist[-log_i:]))  if mm_tw_hist  else 0.0
                nor_r = float(np.mean(mm_nor_hist[-log_i:])) if mm_nor_hist else 0.0
                print(f"  {tag}ep {ep+1:6d}  wins={np.round(wr,3)}"
                      f"  MM_TW={tw_r:.3f}  MM_nor={nor_r:.3f}")
            recent_wins.fill(0)

    q = max(1, len(win_history[0]) // 4)
    return dict(
        wins=[float(np.mean(win_history[i][-q:])) for i in range(3)],
        win_history=win_history,
        mm_rates=mm_rate(all_bins),   # shape (2,3,2)
        all_bins=all_bins,
        mm_tw_hist=mm_tw_hist,
        mm_nor_hist=mm_nor_hist,
    )


# ── balance grid search ────────────────────────────────────────────────────────
def balance_grid(key, payout, verbose=True):
    """
    Search (0, b1, b2) starting bonuses.
    Player 0 = 1× TW (reference), player 1 = 2× TW, player 2 = 3× TW.
    Returns sorted list of (bonus, wins, deviation_from_equal).
    """
    hp = dict(DEFAULTS)
    hp["num_episodes"] = BALANCE_EPISODES
    hp["log_interval"] = BALANCE_EPISODES  # suppress per-step printing

    results = []
    for b2 in range(5):          # 0..4
        for b1 in range(b2 + 1): # b1 <= b2 always (player 2 is most disadvantaged)
            bonus = (0, b1, b2)
            r = train(payout, hp, start_scores=bonus, verbose=False)
            wr = r["wins"]
            dev = sum(abs(w - 1/3) for w in wr)
            results.append((bonus, wr, dev))
            if verbose:
                print(f"  {key}  bonus=(0,{b1:+d},{b2:+d})"
                      f"  wins={np.round(wr,3)}  dev={dev:.3f}")

    results.sort(key=lambda x: x[2])
    return results


# ── pretty-print helpers ───────────────────────────────────────────────────────
PHASE_LABELS = ["Early (dates 1-3)", "Late  (dates 4-6)"]
POS_LABELS   = ["Ahead ", "Tied  ", "Behind"]
ROW_LABELS   = ["Normal", "TW    "]

def print_strategy_table(key, label, mm_rates):
    """Print a 12-cell strategy table for one matrix."""
    print(f"\n  Strategy breakdown — {label} ({key})")
    print(f"  {'':12s}  {'Early (d1-3)':>15}   {'Late  (d4-6)':>15}")
    print(f"  {'':12s}  {'Ahead  Tied  Behind':>21}   {'Ahead  Tied  Behind':>21}")
    for tw in range(2):
        row_label = "Normal  " if tw == 0 else "TW      "
        early = mm_rates[tw, :, 0]
        late  = mm_rates[tw, :, 1]
        def fmt(v): return f"{v:.3f}" if not np.isnan(v) else "  — "
        e_str = "  ".join(fmt(v) for v in early)
        l_str = "  ".join(fmt(v) for v in late)
        print(f"  {row_label}    {e_str}      {l_str}")


def print_matrix_diff(m1, m2, label1, label2):
    """Show where two (2,3,2) MM-rate arrays differ by > 0.02."""
    diff = m2 - m1
    print(f"\n  Δ MM-rate  ({label2} minus {label1})")
    for tw in range(2):
        role = "Normal" if tw == 0 else "TW    "
        for ph in range(2):
            phase_str = "Early" if ph == 0 else "Late "
            parts = []
            for pos, pos_label in enumerate(["Ahead", "Tied ", "Behind"]):
                d = diff[tw, pos, ph]
                if abs(d) >= 0.02:
                    parts.append(f"{pos_label}:{d:+.3f}")
            if parts:
                print(f"    {role} {phase_str}:  " + "  ".join(parts))


# ── plotting ───────────────────────────────────────────────────────────────────
def plot_all(results_map, out_dir="results"):
    os.makedirs(out_dir, exist_ok=True)

    # 1. Win-rate convergence curves (3 subplots, one per matrix)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4), sharey=True)
    for ax, (key, res) in zip(axes, results_map.items()):
        wh = res["win_history"]
        for i, color in enumerate(["tab:blue", "tab:orange", "tab:green"]):
            tw_label = ["1× TW", "2× TW", "3× TW"][i]
            ax.plot(wh[i], color=color, label=tw_label)
        ax.axhline(1/3, color="gray", ls="--", lw=0.8)
        ax.set_title(MATRICES[key]["label"])
        ax.set_xlabel("Log interval")
        ax.set_ylabel("Win rate")
        ax.legend(fontsize=8)
    fig.tight_layout()
    path = os.path.join(out_dir, "deep_win_curves.png")
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"\n  Saved: {path}")

    # 2. Strategy heatmap — MM rate across 12 bins for each matrix
    keys = list(results_map.keys())
    fig, axes = plt.subplots(2, 3, figsize=(14, 6))
    for col, key in enumerate(keys):
        mm = results_map[key]["mm_rates"]   # (2,3,2)
        for row in range(2):
            ax = axes[row, col]
            data = mm[row]   # (3,2): rows=pos (A/T/B), cols=phase (E/L)
            im = ax.imshow(data, vmin=0, vmax=1, cmap="RdYlGn", aspect="auto")
            ax.set_xticks([0, 1]); ax.set_xticklabels(["Early", "Late"], fontsize=8)
            ax.set_yticks([0, 1, 2]); ax.set_yticklabels(["Ahead", "Tied", "Behind"], fontsize=8)
            for r in range(3):
                for c in range(2):
                    v = data[r, c]
                    txt = f"{v:.2f}" if not np.isnan(v) else "—"
                    ax.text(c, r, txt, ha="center", va="center",
                            fontsize=9, color="black" if 0.25 < v < 0.75 else "white")
            role = "Normal" if row == 0 else "TW"
            ax.set_title(f"{MATRICES[key]['label']}\n({role})", fontsize=9)
        plt.colorbar(im, ax=axes[:, col], shrink=0.7)
    fig.suptitle("MM Rates by Role × Score Position × Game Phase", fontsize=11)
    fig.tight_layout()
    path = os.path.join(out_dir, "deep_strategy_heatmap.png")
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"  Saved: {path}")

    # 3. Balance grid — deviation from equal wins vs b2 for each matrix
    # (only if balance data exists)
    if any("balance" in v for v in results_map.values()):
        fig, axes = plt.subplots(1, 3, figsize=(15, 4), sharey=True)
        for ax, (key, res) in zip(axes, results_map.items()):
            if "balance" not in res:
                continue
            bal = res["balance"]  # list of (bonus, wins, dev)
            for b1_val in sorted(set(b[0][1] for b in bal)):
                pts = [(b[0][2], b[2]) for b in bal if b[0][1] == b1_val]
                pts.sort()
                xs, ys = zip(*pts)
                ax.plot(xs, ys, marker="o", label=f"b1={b1_val}")
            ax.axhline(0, color="gray", ls="--", lw=0.8)
            ax.set_title(MATRICES[key]["label"])
            ax.set_xlabel("b2 (3× TW bonus)")
            ax.set_ylabel("Total deviation from 33% each")
            ax.legend(fontsize=8)
        fig.suptitle("Balance Grid Search: Win-Rate Deviation vs Starting Bonus", fontsize=11)
        fig.tight_layout()
        path = os.path.join(out_dir, "deep_balance_grid.png")
        fig.savefig(path, dpi=120)
        plt.close(fig)
        print(f"  Saved: {path}")


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick",      action="store_true", help="Use 2^13 episodes (smoke test)")
    parser.add_argument("--no-balance", action="store_true", help="Skip balance grid")
    parser.add_argument("--matrix",     default="all", help="Which matrix (old/new/test/all)")
    args = parser.parse_args()

    hp = dict(DEFAULTS)
    if args.quick:
        hp["num_episodes"]  = 2**13
        hp["log_interval"]  = 512
        global BALANCE_EPISODES
        BALANCE_EPISODES = 2**11

    keys = list(MATRICES.keys()) if args.matrix == "all" else [args.matrix]
    os.makedirs("results", exist_ok=True)

    results_map = {}

    # ── Phase 1: main training ─────────────────────────────────────────────────
    print("=" * 70)
    print(f"PHASE 1 — Main training  ({hp['num_episodes']:,} episodes each)")
    print("=" * 70)

    for key in keys:
        cfg = MATRICES[key]
        print(f"\n── {cfg['label']} ({key}) ──")
        t0 = time.time()
        res = train(cfg["matrix"], hp, verbose=True, tag=f"[{key}] ")
        elapsed = time.time() - t0
        results_map[key] = res
        print(f"  Done in {elapsed:.0f}s  final wins={np.round(res['wins'], 3)}")
        print_strategy_table(key, cfg["label"], res["mm_rates"])

    # ── Phase 2: cross-matrix comparisons ────────────────────────────────────
    if len(keys) > 1:
        print("\n" + "=" * 70)
        print("PHASE 2 — Cross-matrix strategy differences")
        print("=" * 70)
        matrix_list = [(k, results_map[k]["mm_rates"]) for k in keys]
        for i in range(len(matrix_list)):
            for j in range(i+1, len(matrix_list)):
                k1, m1 = matrix_list[i]
                k2, m2 = matrix_list[j]
                print_matrix_diff(m1, m2, k1, k2)

    # ── Phase 3: summary table ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SUMMARY TABLE (no starting bonus)")
    print("=" * 70)
    print(f"{'Matrix':<10}  {'Wins(1×/2×/3× TW)':>22}  "
          f"{'MM_TW':>7}  {'MM_Nor':>7}  "
          f"{'Early_TW':>9}  {'Late_TW':>8}  "
          f"{'Ahead_N':>8}  {'Behind_N':>9}")
    for key in keys:
        r  = results_map[key]
        mm = r["mm_rates"]  # (2,3,2)
        wins_str = "/".join(f"{w:.2f}" for w in r["wins"])
        mm_tw_overall  = float(np.nanmean(mm[1]))
        mm_nor_overall = float(np.nanmean(mm[0]))
        early_tw = float(np.nanmean(mm[1, :, 0]))
        late_tw  = float(np.nanmean(mm[1, :, 1]))
        ahead_n  = float(mm[0, 0, :].mean())    # normal, ahead
        behind_n = float(mm[0, 2, :].mean())    # normal, behind
        print(f"  {key:<8}  {wins_str:>22}  "
              f"{mm_tw_overall:>7.3f}  {mm_nor_overall:>7.3f}  "
              f"{early_tw:>9.3f}  {late_tw:>8.3f}  "
              f"{ahead_n:>8.3f}  {behind_n:>9.3f}")

    # ── Phase 4: balance grid search ─────────────────────────────────────────
    if not args.no_balance:
        print("\n" + "=" * 70)
        print(f"PHASE 4 — Balance grid search  ({BALANCE_EPISODES:,} episodes per point)")
        print("  b0 fixed at 0 (1× TW player), searching b1 (2× TW) and b2 (3× TW)")
        print("=" * 70)

        for key in keys:
            cfg = MATRICES[key]
            print(f"\n── {cfg['label']} ({key}) ──")
            bal = balance_grid(key, cfg["matrix"], verbose=True)
            results_map[key]["balance"] = bal

            print(f"\n  Top-5 most balanced starting bonuses for [{key}]:")
            print(f"  {'Bonus (0,b1,b2)':>18}  {'Wins':>26}  {'Deviation':>10}")
            for bonus, wr, dev in bal[:5]:
                wr_str = "/".join(f"{w:.3f}" for w in wr)
                print(f"  (0, {bonus[1]:+d}, {bonus[2]:+d})             {wr_str:>26}  {dev:>10.4f}")

    # ── Phase 5: save plots ───────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("PHASE 5 — Saving plots")
    print("=" * 70)
    plot_all(results_map)

    # Save JSON summary
    summary = {}
    for key in keys:
        r = results_map[key]
        bal = r.get("balance", [])
        summary[key] = {
            "wins":      r["wins"],
            "mm_rates":  r["mm_rates"].tolist(),
            "best_bonus": bal[0][0] if bal else None,
            "best_wins":  bal[0][1] if bal else None,
            "best_dev":   bal[0][2] if bal else None,
            "top5_balance": [
                {"bonus": list(b[0]), "wins": list(b[1]), "dev": b[2]}
                for b in bal[:5]
            ] if bal else [],
        }
    with open("results/deep_dive_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("  Saved: results/deep_dive_summary.json")
    print("\nDone.")


if __name__ == "__main__":
    main()
