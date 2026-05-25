"""
AlphaZero-style self-play training loop for Don't Be The Third Wheel.

Usage:
  python train.py                      # train from scratch
  python train.py --resume model.pt    # resume from checkpoint
  python train.py --games 50 --iters 5 --sims 50   # quick smoke-test
"""

import argparse
import os
import random
import time
from collections import deque

import numpy as np
import torch
import torch.optim as optim
import torch.nn.functional as F

from engine import (new_game, get_legal_actions, apply_action, featurize,
                    N_ACTIONS, STATE_DIM)
from network import PolicyValueNet, save, load
from mcts import MCTS


# ── self-play data generation ─────────────────────────────────────────────────

def self_play_game(mcts: MCTS, temperature_cutoff=18, eps_date=0.35):
    """
    Play one game via MCTS self-play.
    eps_date: probability of using random MM/PS instead of MCTS in date phase,
              to break the all-PS Nash equilibrium trap.
    Returns list of (feat, pi, player_idx) for each decision step.
    'pi' is a length-N_ACTIONS float32 array (zero for illegal actions).
    """
    state   = new_game()
    records = []
    step    = 0

    while not state.is_terminal():
        legal = get_legal_actions(state)
        if not legal:
            break

        # Epsilon-greedy for date phase to prevent all-PS collapse
        if state.phase == "date_move" and random.random() < eps_date:
            action  = random.choice(legal)
            policy  = {a: 1.0 / len(legal) for a in legal}
        else:
            temp = 1.0 if step < temperature_cutoff else 0.25
            action, policy = mcts.best_action(state, temperature=temp, add_noise=(step < 4))

        # Store (features, full policy vector, which player moves)
        feat = featurize(state)
        pi   = np.zeros(N_ACTIONS, dtype=np.float32)
        for a, p in policy.items():
            pi[a] = p

        records.append((feat, pi, state.current_player_idx))
        state = apply_action(state, action)
        step += 1

    final = state.final_scores() or [0, 0, 0]

    # Attach outcome for each step
    examples = []
    for feat, pi, pidx in records:
        examples.append((feat, pi, final))

    return examples, final


# ── training step ─────────────────────────────────────────────────────────────

def train_step(net, optimizer, batch, device):
    feats, pis, outcomes, player_idxs = batch

    feats    = feats.to(device)
    pis      = pis.to(device)
    outcomes = outcomes.to(device)

    log_pi, v = net(feats)

    # Policy loss: cross-entropy between MCTS pi and network pi
    policy_loss = -(pis * log_pi).sum(dim=-1).mean()

    # Value loss: MSE between network value (for the moving player) and actual outcome
    # outcomes shape: (batch, 3), player_idxs shape: (batch,)
    v_target = outcomes                              # (batch, 3)
    value_loss = F.mse_loss(v, v_target)

    loss = policy_loss + value_loss
    optimizer.zero_grad()
    loss.backward()
    torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
    optimizer.step()

    return loss.item(), policy_loss.item(), value_loss.item()


def collate(examples):
    """examples: list of (feat, pi, final_scores)"""
    feats    = torch.from_numpy(np.stack([e[0] for e in examples]))
    pis      = torch.from_numpy(np.stack([e[1] for e in examples]))
    outcomes = torch.tensor([[float(s) / 10.0 for s in e[2]] for e in examples],
                             dtype=torch.float32)
    player_idxs = torch.zeros(len(examples), dtype=torch.long)  # not needed but kept
    return feats, pis, outcomes, player_idxs


# ── main training loop ────────────────────────────────────────────────────────

def train(args):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    if args.resume and os.path.exists(args.resume):
        print(f"Resuming from {args.resume}")
        net = load(args.resume, device=str(device))
        net = net.to(device)
    else:
        net = PolicyValueNet(hidden=args.hidden, layers=args.layers).to(device)

    optimizer = optim.Adam(net.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=5, gamma=0.8)

    replay = deque(maxlen=args.buffer)
    mcts   = MCTS(net, n_simulations=args.sims)

    best_loss = float("inf")
    os.makedirs(args.out_dir, exist_ok=True)

    for iteration in range(1, args.iters + 1):
        t0 = time.time()

        # ── self-play ──────────────────────────────────────────────────────────
        net.eval()
        game_scores = []
        for g in range(args.games):
            examples, final = self_play_game(mcts, temperature_cutoff=18)
            replay.extend(examples)
            game_scores.append(final)
        avg_scores = np.mean(game_scores, axis=0)

        # ── training ───────────────────────────────────────────────────────────
        net.train()
        total_loss = 0.0
        batches    = 0
        if len(replay) >= args.batch:
            for _ in range(args.train_steps):
                batch_raw = random.sample(replay, min(args.batch, len(replay)))
                batch = collate(batch_raw)
                loss, pl, vl = train_step(net, optimizer, batch, device)
                total_loss += loss
                batches    += 1
        scheduler.step()

        elapsed = time.time() - t0
        avg_loss = total_loss / max(batches, 1)
        print(
            f"Iter {iteration:3d}/{args.iters} | "
            f"loss={avg_loss:.4f} | "
            f"replay={len(replay)} | "
            f"scores={[f'{s:.1f}' for s in avg_scores]} | "
            f"time={elapsed:.1f}s"
        )

        if avg_loss < best_loss and batches > 0:
            best_loss = avg_loss
            save(net, os.path.join(args.out_dir, "best.pt"))

        if iteration % args.save_every == 0:
            save(net, os.path.join(args.out_dir, f"iter_{iteration:04d}.pt"))

    save(net, os.path.join(args.out_dir, "final.pt"))
    print(f"Done. Best loss={best_loss:.4f}")
    print(f"Model saved to {args.out_dir}/final.pt")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--resume",      default=None,          help="resume from checkpoint")
    p.add_argument("--out-dir",     default="checkpoints", help="where to save models")
    p.add_argument("--iters",       type=int, default=30,  help="training iterations")
    p.add_argument("--games",       type=int, default=20,  help="self-play games per iter")
    p.add_argument("--sims",        type=int, default=100, help="MCTS simulations per move")
    p.add_argument("--batch",       type=int, default=256, help="training batch size")
    p.add_argument("--train-steps", type=int, default=10,  help="gradient steps per iter")
    p.add_argument("--buffer",      type=int, default=50000)
    p.add_argument("--lr",          type=float, default=1e-3)
    p.add_argument("--hidden",      type=int, default=256)
    p.add_argument("--layers",      type=int, default=4)
    p.add_argument("--save-every",  type=int, default=5)
    args = p.parse_args()
    train(args)
