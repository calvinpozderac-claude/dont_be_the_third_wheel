"""
Evaluate a trained model for strategic diversity.

Metrics:
  1. Card order diversity — do players vary which cards go to which positions?
  2. Positional advantage — is there a pile position consistently beneficial?
  3. MM/PS rates — does MM/PS usage correlate with active bonuses/debuffs?
  4. Score spread — is the game balanced (no player dominates)?

Usage:
  python evaluate.py --model checkpoints/final.pt --games 500
  python evaluate.py --random --games 500        # baseline: random play
"""

import argparse
import random
from collections import defaultdict

import numpy as np

from engine import (new_game, get_legal_actions, apply_action, featurize,
                    _tw_pos, N_ACTIONS)
from mcts import MCTS
from network import PolicyValueNet, load


# ── game playing ──────────────────────────────────────────────────────────────

def play_game_mcts(mcts: MCTS, temperature=0.3):
    """Play one game using MCTS at given temperature. Return GameState at end."""
    state = new_game()
    while not state.is_terminal():
        legal = get_legal_actions(state)
        if not legal:
            break
        action, _ = mcts.best_action(state, temperature=temperature, add_noise=False)
        state = apply_action(state, action)
    return state


def play_game_random():
    state = new_game()
    while not state.is_terminal():
        legal = get_legal_actions(state)
        if not legal:
            break
        state = apply_action(state, random.choice(legal))
    return state


# ── analysis ──────────────────────────────────────────────────────────────────

def analyze_games(states, label="Agent"):
    print(f"\n{'='*60}")
    print(f"Analysis: {label}  ({len(states)} games)")
    print(f"{'='*60}")

    # ── 1. Score spread ──────────────────────────────────────────────────────
    all_scores = [s.final_scores() for s in states]
    all_scores_np = np.array(all_scores, dtype=float)  # (G, 3)
    means = all_scores_np.mean(axis=0)
    stds  = all_scores_np.std(axis=0)
    wins  = (all_scores_np == all_scores_np.max(axis=1, keepdims=True)).sum(axis=0)
    win_pct = wins / len(states) * 100

    print("\n[1] Score spread")
    for i in range(3):
        print(f"  P{i}: mean={means[i]:+.2f}  std={stds[i]:.2f}  win%={win_pct[i]:.1f}%")
    print(f"  Avg |score_range|: {np.mean(all_scores_np.max(axis=1) - all_scores_np.min(axis=1)):.2f}")

    # ── 2. Positional advantage (pile index → avg score) ────────────────────
    pile_tw_scores  = defaultdict(list)   # pile_idx → TW's score at that date
    pile_n_scores   = defaultdict(list)   # pile_idx → avg Normal score at that date
    for s in states:
        for di, stack in enumerate(s.stacks):
            if not stack.date_resolved:
                continue
            twp    = _tw_pos(di)
            tw_p   = stack.plays[twp].player_idx
            n_ps   = [stack.plays[i].player_idx for i in range(3) if i != twp]
            tw_sc  = stack.date_scores.get(tw_p, 0)
            n_sc   = np.mean([stack.date_scores.get(p, 0) for p in n_ps])
            pile_tw_scores[di].append(tw_sc)
            pile_n_scores[di].append(n_sc)

    print("\n[2] Positional advantage (avg score by pile index)")
    print(f"  {'Pile':>5}  {'TW avg':>8}  {'Normal avg':>10}  {'TW-N gap':>9}")
    for di in range(6):
        tw_avg = np.mean(pile_tw_scores[di]) if pile_tw_scores[di] else 0
        n_avg  = np.mean(pile_n_scores[di])  if pile_n_scores[di]  else 0
        print(f"  Pile {di+1}:  {tw_avg:+7.2f}   {n_avg:+9.2f}   {tw_avg-n_avg:+8.2f}")

    # ── 3. Card order diversity ──────────────────────────────────────────────
    # For each player, which card went to which pile?
    card_pile_counts = defaultdict(lambda: defaultdict(int))  # card → pile_idx → count
    for s in states:
        for di, stack in enumerate(s.stacks):
            for play in stack.plays:
                card_pile_counts[play.card_num][di] += 1

    print("\n[3] Card placement diversity (card → pile distribution)")
    print(f"  {'Card':>5}  {'Pile 1':>7}  {'Pile 2':>7}  {'Pile 3':>7}  {'Pile 4':>7}  {'Pile 5':>7}  {'Pile 6':>7}  {'Entropy':>8}")
    for card in range(1, 7):
        counts = np.array([card_pile_counts[card][di] for di in range(6)], dtype=float)
        total  = counts.sum()
        if total == 0:
            continue
        probs  = counts / total
        # Shannon entropy (bits), max = log2(6) ≈ 2.58
        entropy = -np.sum(probs * np.log2(probs + 1e-9))
        pcts    = (probs * 100).astype(int)
        print(f"  C{card}:    " + "  ".join(f"{p:6d}%" for p in pcts) + f"   {entropy:.2f}b")

    # ── 4. MM/PS rates by modifier state ────────────────────────────────────
    # Collect: for each date move, record MM/PS and whether the mover
    # had an active bonus (card 1/2/3) or active debuff (card 4/5/6) in that date
    mm_with_bonus   = 0; ps_with_bonus  = 0
    mm_with_debuff  = 0; ps_with_debuff = 0
    mm_neutral      = 0; ps_neutral     = 0

    mm_as_tw = 0; mm_as_normal = 0
    ps_as_tw = 0; ps_as_normal = 0

    for s in states:
        for di, stack in enumerate(s.stacks):
            if not stack.date_resolved:
                continue
            twp   = _tw_pos(di)
            plays = stack.plays
            for pi, play in enumerate(plays):
                pidx   = play.player_idx
                move   = stack.date_moves.get(pidx)
                if move is None:
                    continue
                is_tw  = (pi == twp)
                # Check active modifiers for this player in this pile
                has_bonus  = (play.modifier_active and
                              play.card_num in (1, 2, 3))
                has_debuff = (play.modifier_active and
                              play.card_num in (4, 5, 6))

                if move == "MM":
                    if has_bonus:    mm_with_bonus  += 1
                    elif has_debuff: mm_with_debuff += 1
                    else:            mm_neutral     += 1
                    if is_tw: mm_as_tw     += 1
                    else:     mm_as_normal += 1
                else:
                    if has_bonus:    ps_with_bonus  += 1
                    elif has_debuff: ps_with_debuff += 1
                    else:            ps_neutral     += 1
                    if is_tw: ps_as_tw     += 1
                    else:     ps_as_normal += 1

    def _rate(mm, ps):
        total = mm + ps
        return mm / total * 100 if total > 0 else 0

    print("\n[4] MM/PS rates by context")
    print(f"  With active bonus  : MM={_rate(mm_with_bonus,  ps_with_bonus):.1f}%"
          f"  PS={_rate(ps_with_bonus,  mm_with_bonus):.1f}%")
    print(f"  With active debuff : MM={_rate(mm_with_debuff, ps_with_debuff):.1f}%"
          f"  PS={_rate(ps_with_debuff, mm_with_debuff):.1f}%")
    print(f"  Neutral            : MM={_rate(mm_neutral,     ps_neutral):.1f}%"
          f"  PS={_rate(ps_neutral,     mm_neutral):.1f}%")
    print(f"  As third wheel     : MM={_rate(mm_as_tw,       ps_as_tw):.1f}%"
          f"  PS={_rate(ps_as_tw,       mm_as_tw):.1f}%")
    print(f"  As normal player   : MM={_rate(mm_as_normal,   ps_as_normal):.1f}%"
          f"  PS={_rate(ps_as_normal,   mm_as_normal):.1f}%")

    # ── 5. Action skipping rate ──────────────────────────────────────────────
    action_used = defaultdict(int)
    action_skipped = defaultdict(int)
    for s in states:
        for stack in s.stacks:
            for play in stack.plays:
                if play.used_action:
                    action_used[play.card_num] += 1
                else:
                    action_skipped[play.card_num] += 1

    print("\n[5] Action use rate per card")
    for c in range(1, 7):
        used   = action_used[c]
        skipped = action_skipped[c]
        total  = used + skipped
        rate   = used / total * 100 if total > 0 else 0
        print(f"  Card {c}: used={rate:.1f}%  skipped={100-rate:.1f}%  (type={'bonus' if c<=3 else 'debuff'})")

    print()


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model",  default=None,   help="model checkpoint path")
    p.add_argument("--random", action="store_true", help="use random play as baseline")
    p.add_argument("--games",  type=int, default=500)
    p.add_argument("--sims",   type=int, default=50, help="MCTS simulations per move")
    p.add_argument("--temp",   type=float, default=0.5, help="move temperature")
    args = p.parse_args()

    print(f"Running {args.games} games...")

    if args.random:
        states = []
        for i in range(args.games):
            if i % 100 == 0:
                print(f"  game {i}/{args.games}", flush=True)
            states.append(play_game_random())
        analyze_games(states, label="Random baseline")
    else:
        if args.model is None:
            print("Specify --model <path> or --random")
            return
        net  = load(args.model)
        mcts = MCTS(net, n_simulations=args.sims)
        states = []
        for i in range(args.games):
            if i % 50 == 0:
                print(f"  game {i}/{args.games}", flush=True)
            states.append(play_game_mcts(mcts, temperature=args.temp))
        analyze_games(states, label=f"Trained model: {args.model}")


if __name__ == "__main__":
    main()
