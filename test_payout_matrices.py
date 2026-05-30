"""
Payout matrix tester for "Don't Be the Third Wheel".

Runs RL training (REINFORCE) for each candidate payout matrix and reports:
  - overall MM rate per agent
  - MM rate when acting as TW vs normal
  - MM rate by date index (0-5)
  - MM rate by score position (ahead / tied / behind)
  - win rates over time
  - whether win rates balance when TW-handicapped players get a score bonus

Usage:
    python test_payout_matrices.py [--episodes 16384] [--quick]
"""

import argparse
import json
import os
import warnings

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.distributions import Categorical

warnings.filterwarnings("ignore")
os.makedirs("results", exist_ok=True)

# ── constants ──────────────────────────────────────────────────────────────────
ACTION_PS = 0
ACTION_MM = 1

# tw_schedule: player 0 is TW 1×, player 1 is TW 2×, player 2 is TW 3×
BASE_TW_SCHEDULE = np.array([0, 1, 1, 2, 2, 2])

# Candidate matrices  key → (label, matrix)
# Each matrix entry: (tw_act, n1_act, n2_act) → (tw_reward, n1_reward, n2_reward)
MATRICES = {
    "canonical": {
        "label": "Canonical (app.py)",
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
    "softer_crash": {
        "label": "Softer crash (TW MM alone = -1)",
        "matrix": {
            (0, 0, 0): ( 0,  1,  1),
            (0, 1, 0): ( 0, -1,  1),
            (0, 0, 1): ( 0,  1, -1),
            (0, 1, 1): ( 0,  2,  2),
            (1, 0, 0): (-1,  1,  1),   # softer
            (1, 1, 0): ( 2,  2,  0),
            (1, 0, 1): ( 2,  0,  2),
            (1, 1, 1): (-2,  2,  2),
        },
    },
    "risky_normals": {
        "label": "Risky normals (PS-PS = 0 for all)",
        "matrix": {
            (0, 0, 0): ( 0,  0,  0),   # all-PS is neutral
            (0, 1, 0): ( 0, -1,  1),
            (0, 0, 1): ( 0,  1, -1),
            (0, 1, 1): ( 0,  2,  2),
            (1, 0, 0): (-2,  1,  1),
            (1, 1, 0): ( 2,  2,  0),
            (1, 0, 1): ( 2,  0,  2),
            (1, 1, 1): (-2,  2,  2),
        },
    },
    "tw_brave": {
        "label": "TW rewarded for brave solo MM",
        "matrix": {
            (0, 0, 0): ( 0,  1,  1),
            (0, 1, 0): ( 0, -1,  1),
            (0, 0, 1): ( 0,  1, -1),
            (0, 1, 1): ( 0,  2,  2),
            (1, 0, 0): (-2,  1,  1),
            (1, 1, 0): ( 2,  2,  0),
            (1, 0, 1): ( 2,  0,  2),
            (1, 1, 1): ( 0,  1,  1),   # TW MM+MM+MM breaks even (not punished)
        },
    },
    "punish_loner_mm": {
        "label": "Lone MM is punished (normal solo MM = -2)",
        "matrix": {
            (0, 0, 0): ( 0,  1,  1),
            (0, 1, 0): ( 0, -2,  1),   # harsher for lone normal MM
            (0, 0, 1): ( 0,  1, -2),
            (0, 1, 1): ( 0,  2,  2),
            (1, 0, 0): (-2,  1,  1),
            (1, 1, 0): ( 2,  2,  0),
            (1, 0, 1): ( 2,  0,  2),
            (1, 1, 1): (-2,  2,  2),
        },
    },
    "scaled_up": {
        "label": "Scaled ×1.5 payoffs",
        "matrix": {
            (0, 0, 0): ( 0,   1,   1),
            (0, 1, 0): ( 0,  -2,   1),
            (0, 0, 1): ( 0,   1,  -2),
            (0, 1, 1): ( 0,   3,   3),
            (1, 0, 0): (-3,   1,   1),
            (1, 1, 0): ( 3,   3,   0),
            (1, 0, 1): ( 3,   0,   3),
            (1, 1, 1): (-3,   3,   3),
        },
    },
}

# Hyper-parameters
DEFAULTS = dict(
    num_episodes=2**14,
    lr=0.002,
    hidden_size=64,
    entropy_beta=0.10,
    batch_size=64,
    epsilon=0.0,
    log_interval=1000,
)


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
        if np.random.rand() < epsilon:
            action = torch.tensor(np.random.randint(2))
        else:
            action = m.sample()
        self.saved_log_probs.append(m.log_prob(action))
        self.saved_entropies.append(m.entropy())
        return action.item()


# ── environment ────────────────────────────────────────────────────────────────
class MakeAMoveEnv:
    def __init__(self, payout_matrix):
        self.payout_matrix = payout_matrix
        self.reset()

    def reset(self):
        self.history = np.zeros((3, 6))
        self.scores = np.zeros(3)
        self.current_date = 0
        rng = np.random.default_rng()
        self.tw_schedule = BASE_TW_SCHEDULE.copy()
        rng.shuffle(self.tw_schedule)

    def get_ego_state(self, player_idx):
        tw_flags = (self.tw_schedule == player_idx).astype(float)
        p1 = (player_idx + 1) % 3
        p2 = (player_idx + 2) % 3
        ego_history = np.concatenate([
            self.history[player_idx],
            self.history[p1],
            self.history[p2],
        ])
        ego_scores = np.array([self.scores[player_idx], self.scores[p1], self.scores[p2]])
        return np.concatenate([tw_flags, ego_history, ego_scores])

    def step(self, actions):
        tw = self.tw_schedule[self.current_date]
        normals = [i for i in range(3) if i != tw]
        n0, n1 = normals[0], normals[1]

        key = (actions[tw], actions[n0], actions[n1])
        payouts = self.payout_matrix[key]

        self.scores[tw] += payouts[0]
        self.scores[n0] += payouts[1]
        self.scores[n1] += payouts[2]

        for i, act in enumerate(actions):
            self.history[i, self.current_date] = 1 if act == ACTION_MM else -1

        self.current_date += 1
        return self.current_date >= 6


# ── training ───────────────────────────────────────────────────────────────────
def train_one_matrix(matrix_key, matrix_cfg, hp):
    payout = matrix_cfg["matrix"]
    env = MakeAMoveEnv(payout)
    agents = [Agent(hp["hidden_size"]) for _ in range(3)]
    optimizers = [optim.Adam(a.parameters(), lr=hp["lr"]) for a in agents]

    stats = {
        "win_rates":      [[], [], []],
        "mm_rates":       [[], [], []],
        # positional
        "mm_when_ahead":  [],
        "mm_when_behind": [],
        "mm_when_tied":   [],
        # TW vs normal
        "mm_as_tw":       [],   # per-episode proportion
        "mm_as_normal":   [],
        # by date index
        "mm_by_date":     [[] for _ in range(6)],
    }

    recent_wins         = np.zeros(3)
    recent_mm_counts    = np.zeros(3)
    recent_action_counts= np.zeros(3)

    for opt in optimizers:
        opt.zero_grad()

    for episode in range(hp["num_episodes"]):
        env.reset()

        pos_mm  = {"ahead": 0, "behind": 0, "tied": 0}
        pos_tot = {"ahead": 0, "behind": 0, "tied": 0}
        tw_mm_sum, tw_tot   = 0, 0
        nor_mm_sum, nor_tot = 0, 0
        date_mm  = np.zeros(6)
        date_tot = np.zeros(6)

        done = False
        while not done:
            d = env.current_date
            actions = []
            max_score = np.max(env.scores)

            for i, agent in enumerate(agents):
                state  = env.get_ego_state(i)
                action = agent.select_action(state, hp["epsilon"])
                actions.append(action)

                recent_mm_counts[i]     += action
                recent_action_counts[i] += 1

                # position tracking
                is_tied_for_lead = np.sum(env.scores == max_score) > 1
                if env.scores[i] == max_score and not is_tied_for_lead:
                    pos_tot["ahead"] += 1
                    pos_mm["ahead"]  += action
                elif env.scores[i] == max_score:
                    pos_tot["tied"]  += 1
                    pos_mm["tied"]   += action
                else:
                    pos_tot["behind"] += 1
                    pos_mm["behind"]  += action

                # TW vs normal tracking
                if env.tw_schedule[d] == i:
                    tw_tot   += 1
                    tw_mm_sum += action
                else:
                    nor_tot   += 1
                    nor_mm_sum += action

                # date tracking
                date_mm[d]  += action
                date_tot[d] += 1

            done = env.step(actions)

        # REINFORCE
        for i, agent in enumerate(agents):
            opp_scores = [env.scores[j] for j in range(3) if j != i]
            reward = (2 * (env.scores[i] > max(opp_scores))
                      + (env.scores[i] == max(opp_scores))
                      - 1)
            policy_loss  = [-lp * reward for lp in agent.saved_log_probs]
            entropy_loss = list(agent.saved_entropies)
            loss = (torch.cat(policy_loss).sum()
                    - hp["entropy_beta"] * torch.cat(entropy_loss).sum()) / hp["batch_size"]
            loss.backward()
            del agent.saved_log_probs[:]
            del agent.saved_entropies[:]

        max_score = np.max(env.scores)
        is_tied   = np.sum(env.scores == max_score) > 1
        for i in range(3):
            if env.scores[i] == max_score:
                recent_wins[i] += 0.5 + 0.5 * (not is_tied)

        if (episode + 1) % hp["batch_size"] == 0:
            for opt in optimizers:
                opt.step()
                opt.zero_grad()

        # per-episode stats
        for k in ("ahead", "behind", "tied"):
            if pos_tot[k] > 0:
                stats[f"mm_when_{k}"].append(pos_mm[k] / pos_tot[k])
        if tw_tot > 0:
            stats["mm_as_tw"].append(tw_mm_sum / tw_tot)
        if nor_tot > 0:
            stats["mm_as_normal"].append(nor_mm_sum / nor_tot)
        for d in range(6):
            if date_tot[d] > 0:
                stats["mm_by_date"][d].append(date_mm[d] / date_tot[d])

        if (episode + 1) % hp["log_interval"] == 0:
            wr = recent_wins / hp["log_interval"]
            mr = recent_mm_counts / recent_action_counts
            for i in range(3):
                stats["win_rates"][i].append(float(wr[i]))
                stats["mm_rates"][i].append(float(mr[i]))

            tw_rate  = np.mean(stats["mm_as_tw"][-hp["log_interval"]:])  if stats["mm_as_tw"]  else 0
            nor_rate = np.mean(stats["mm_as_normal"][-hp["log_interval"]:]) if stats["mm_as_normal"] else 0
            date_rates = [
                np.mean(stats["mm_by_date"][d][-hp["log_interval"]:]) if stats["mm_by_date"][d] else 0
                for d in range(6)
            ]
            print(f"  ep {episode+1:>6} | wins {np.round(wr,2)} | MM {np.round(mr,2)} "
                  f"| MM_TW {tw_rate:.2f} MM_NOR {nor_rate:.2f} "
                  f"| by_date {np.round(date_rates,2)}")

            recent_wins.fill(0)
            recent_mm_counts.fill(0)
            recent_action_counts.fill(0)

    return stats, agents


def smooth(y, w=500):
    if len(y) < w:
        return np.array(y)
    return np.convolve(y, np.ones(w) / w, mode="valid")


def summarize(matrix_key, matrix_cfg, stats):
    """Return a dict of scalar summaries from the final quarter of training."""
    N = len(stats["mm_as_tw"])
    q = max(1, N // 4)

    def tail_mean(lst):
        return float(np.mean(lst[-q:])) if len(lst) >= q else (float(np.mean(lst)) if lst else 0.0)

    win_rates_final = [tail_mean(stats["win_rates"][i]) for i in range(3)]
    mm_final        = [tail_mean(stats["mm_rates"][i])  for i in range(3)]
    mm_tw_final     = tail_mean(stats["mm_as_tw"])
    mm_nor_final    = tail_mean(stats["mm_as_normal"])
    mm_ahead_final  = tail_mean(stats["mm_when_ahead"])
    mm_behind_final = tail_mean(stats["mm_when_behind"])
    mm_tied_final   = tail_mean(stats["mm_when_tied"])
    mm_by_date_final= [tail_mean(stats["mm_by_date"][d]) for d in range(6)]

    # "richness" = spread of conditional MM rates (higher = more varied behavior)
    cond = [mm_tw_final, mm_nor_final, mm_ahead_final, mm_behind_final, mm_tied_final]
    richness = float(np.std(cond))

    # collapse = overall MM rate too low/high, or any per-agent rate degenerate,
    # or any early-date rate very low (agents learned to always PS early)
    all_rates = [mm_tw_final, mm_nor_final] + mm_final
    early_date_rates = mm_by_date_final[:3]  # dates 1-3
    collapsed = (
        any(r < 0.20 or r > 0.85 for r in all_rates)
        or any(r < 0.15 for r in early_date_rates)
    )

    return {
        "key":             matrix_key,
        "label":           matrix_cfg["label"],
        "win_rates":       win_rates_final,
        "mm_overall":      mm_final,
        "mm_as_tw":        mm_tw_final,
        "mm_as_normal":    mm_nor_final,
        "mm_when_ahead":   mm_ahead_final,
        "mm_when_behind":  mm_behind_final,
        "mm_when_tied":    mm_tied_final,
        "mm_by_date":      mm_by_date_final,
        "richness":        richness,
        "collapsed":       collapsed,
    }


def plot_matrix(matrix_key, matrix_cfg, stats, hp):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axs = plt.subplots(2, 3, figsize=(18, 10))
    fig.suptitle(f"{matrix_cfg['label']} — {hp['num_episodes']} episodes", fontsize=13)

    # row 0
    for i in range(3):
        axs[0, 0].plot(stats["mm_rates"][i], label=f"Agent {i+1}")
    axs[0, 0].set_title('Overall MM Rate')
    axs[0, 0].set_xlabel("Episodes (×1000)")
    axs[0, 0].set_ylabel("P(MM)")
    axs[0, 0].legend()

    axs[0, 1].plot(smooth(stats["mm_when_ahead"]),  label="Ahead",  color="green")
    axs[0, 1].plot(smooth(stats["mm_when_tied"]),   label="Tied",   color="gray")
    axs[0, 1].plot(smooth(stats["mm_when_behind"]), label="Behind", color="red")
    axs[0, 1].set_title("Conditional MM Rate (by score position)")
    axs[0, 1].set_xlabel("Game turns")
    axs[0, 1].set_ylabel("P(MM)")
    axs[0, 1].legend()

    for i in range(3):
        axs[0, 2].plot(stats["win_rates"][i], label=f"Agent {i+1}")
    axs[0, 2].set_title("Outright Win Rate")
    axs[0, 2].set_xlabel("Episodes (×1000)")
    axs[0, 2].set_ylabel("Win %")
    axs[0, 2].legend()

    # row 1
    axs[1, 0].plot(smooth(stats["mm_as_tw"]),     label="As TW",     color="purple")
    axs[1, 0].plot(smooth(stats["mm_as_normal"]), label="As Normal",  color="orange")
    axs[1, 0].set_title("MM Rate: TW vs Normal")
    axs[1, 0].set_xlabel("Game turns")
    axs[1, 0].set_ylabel("P(MM)")
    axs[1, 0].legend()

    colors = plt.cm.viridis(np.linspace(0, 1, 6))
    for d in range(6):
        if stats["mm_by_date"][d]:
            axs[1, 1].plot(smooth(stats["mm_by_date"][d]),
                           label=f"Date {d+1}", color=colors[d])
    axs[1, 1].set_title("MM Rate by Date Index")
    axs[1, 1].set_xlabel("Game turns")
    axs[1, 1].set_ylabel("P(MM)")
    axs[1, 1].legend(fontsize=8)

    # bar: final MM by date
    N = len(stats["mm_by_date"][0])
    q = max(1, N // 4)
    final_by_date = [
        float(np.mean(stats["mm_by_date"][d][-q:])) if len(stats["mm_by_date"][d]) >= q else 0.0
        for d in range(6)
    ]
    axs[1, 2].bar(range(1, 7), final_by_date, color=colors)
    axs[1, 2].set_title("Final MM Rate by Date (mean, last 25%)")
    axs[1, 2].set_xlabel("Date index")
    axs[1, 2].set_ylabel("P(MM)")
    axs[1, 2].set_ylim(0, 1)

    plt.tight_layout()
    path = f"results/{matrix_key}.png"
    plt.savefig(path, dpi=100)
    plt.close()
    print(f"  Saved plot → {path}")


# ── balance test ───────────────────────────────────────────────────────────────
def test_balance(matrix_key, matrix_cfg, hp, bonus_schedule=(0, 0, 0)):
    """
    Re-run training with agents starting at bonus_schedule scores.
    Returns final win rates.
    """

    class BonusEnv(MakeAMoveEnv):
        def reset(self):
            super().reset()
            self.scores = np.array(bonus_schedule, dtype=float)

    payout = matrix_cfg["matrix"]
    env    = BonusEnv(payout)
    agents = [Agent(hp["hidden_size"]) for _ in range(3)]
    optimizers = [optim.Adam(a.parameters(), lr=hp["lr"]) for a in agents]

    recent_wins = np.zeros(3)
    win_history = [[], [], []]

    for opt in optimizers:
        opt.zero_grad()

    for episode in range(hp["num_episodes"]):
        env.reset()
        done = False
        while not done:
            actions = [a.select_action(env.get_ego_state(i), hp["epsilon"])
                       for i, a in enumerate(agents)]
            done = env.step(actions)

        for i, agent in enumerate(agents):
            opp = [env.scores[j] for j in range(3) if j != i]
            reward = (2 * (env.scores[i] > max(opp))
                      + (env.scores[i] == max(opp))
                      - 1)
            loss = (sum(-lp * reward for lp in agent.saved_log_probs)
                    - hp["entropy_beta"] * sum(agent.saved_entropies)) / hp["batch_size"]
            loss.backward()
            del agent.saved_log_probs[:]
            del agent.saved_entropies[:]

        ms = np.max(env.scores)
        is_tied = np.sum(env.scores == ms) > 1
        for i in range(3):
            if env.scores[i] == ms:
                recent_wins[i] += 0.5 + 0.5 * (not is_tied)

        if (episode + 1) % hp["batch_size"] == 0:
            for opt in optimizers:
                opt.step()
                opt.zero_grad()

        if (episode + 1) % hp["log_interval"] == 0:
            wr = recent_wins / hp["log_interval"]
            for i in range(3):
                win_history[i].append(float(wr[i]))
            recent_wins.fill(0)

    q = max(1, len(win_history[0]) // 4)
    return [float(np.mean(win_history[i][-q:])) for i in range(3)]


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=DEFAULTS["num_episodes"])
    parser.add_argument("--quick",    action="store_true",
                        help="Run fewer episodes (2048) for a fast smoke test")
    parser.add_argument("--keys",     nargs="*", default=list(MATRICES.keys()),
                        help="Which matrix keys to run (default: all)")
    parser.add_argument("--balance",  action="store_true",
                        help="Also run balance test for the best matrix")
    args = parser.parse_args()

    hp = dict(DEFAULTS)
    if args.quick:
        hp["num_episodes"] = 2048
        hp["log_interval"] = 256
    else:
        hp["num_episodes"] = args.episodes

    summaries = []
    for key in args.keys:
        if key not in MATRICES:
            print(f"Unknown key '{key}', skipping.")
            continue
        cfg = MATRICES[key]
        print(f"\n{'='*60}")
        print(f"Training: {cfg['label']}")
        print('='*60)
        stats, _ = train_one_matrix(key, cfg, hp)
        s = summarize(key, cfg, stats)
        summaries.append(s)
        plot_matrix(key, cfg, stats, hp)

    # Print summary table
    print(f"\n{'='*60}")
    print("SUMMARY")
    print('='*60)
    header = (f"{'Matrix':<22} {'Win0':>5} {'Win1':>5} {'Win2':>5} "
              f"{'MM_TW':>6} {'MM_NOR':>7} {'Ahead':>6} {'Behind':>7} "
              f"{'Rich':>6} {'Coll':>5}")
    print(header)
    print('-' * len(header))
    for s in summaries:
        wr = s["win_rates"]
        print(f"{s['key']:<22} "
              f"{wr[0]:>5.3f} {wr[1]:>5.3f} {wr[2]:>5.3f} "
              f"{s['mm_as_tw']:>6.3f} {s['mm_as_normal']:>7.3f} "
              f"{s['mm_when_ahead']:>6.3f} {s['mm_when_behind']:>7.3f} "
              f"{s['richness']:>6.3f} {'YES' if s['collapsed'] else 'no':>5}")

    print("\nMM by date (final):")
    print(f"{'Matrix':<22} " + " ".join(f"D{d+1:1d}" for d in range(6)))
    print("-" * 45)
    for s in summaries:
        row = "  ".join(f"{v:.2f}" for v in s["mm_by_date"])
        print(f"{s['key']:<22} {row}")

    # Save summaries as JSON
    with open("results/summaries.json", "w") as f:
        json.dump(summaries, f, indent=2)
    print("\nSummaries saved to results/summaries.json")

    # Balance test: pick non-collapsed matrix with highest richness
    # Bonuses are starting scores. Player order: 0=TW 1×, 1=TW 2×, 2=TW 3×.
    # We give the MOST-TW player the largest bonus to offset their disadvantage.
    if args.balance:
        healthy = [s for s in summaries if not s["collapsed"]]
        if not healthy:
            print("\nAll matrices collapsed — skipping balance test.")
        else:
            best = max(healthy, key=lambda s: s["richness"])
            print(f"\nBalance test on '{best['key']}'")
            print("  Player 0 = TW 1×, Player 1 = TW 2×, Player 2 = TW 3×")
            print(f"  Baseline wins (no bonus): {np.round(best['win_rates'], 3)}")
            for bonus in [(0,0,0), (0,0,2), (0,1,2), (0,0,4), (0,2,4), (-1,0,2)]:
                wr = test_balance(best["key"], MATRICES[best["key"]], hp,
                                  bonus_schedule=bonus)
                print(f"  bonus(p0={bonus[0]:+d},p1={bonus[1]:+d},p2={bonus[2]:+d})"
                      f"  wins={np.round(wr, 3)}")


if __name__ == "__main__":
    main()
