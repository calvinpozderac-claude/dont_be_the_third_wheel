# Reinforcement Learning for Don't Be The Third Wheel

AlphaZero-style self-play training. Completely standalone — no Flask dependency.

## Files

| File | Purpose |
|------|---------|
| `engine.py` | Pure-Python game simulator (rules, actions, scoring) |
| `network.py` | PyTorch policy + value network |
| `mcts.py` | Monte Carlo Tree Search (3-player PUCT) |
| `train.py` | Self-play training loop |
| `evaluate.py` | Diversity/balance analysis |

## Quick start

```bash
pip install torch numpy

# Train (default: 30 iters, 20 games/iter, 100 sims/move)
python train.py

# Quick smoke-test
python train.py --iters 3 --games 5 --sims 30

# Evaluate trained model
python evaluate.py --model checkpoints/final.pt --games 500

# Baseline: random play
python evaluate.py --random --games 2000
```

## Training parameters

| Flag | Default | Meaning |
|------|---------|---------|
| `--iters` | 30 | Training iterations |
| `--games` | 20 | Self-play games per iteration |
| `--sims` | 100 | MCTS simulations per move |
| `--batch` | 256 | SGD batch size |
| `--train-steps` | 10 | Gradient steps per iteration |
| `--lr` | 0.001 | Learning rate |
| `--hidden` | 256 | Hidden layer width |
| `--layers` | 4 | Trunk depth |
| `--resume` | — | Resume from checkpoint path |

## Architecture

### Game engine (`engine.py`)

Full game simulation in ~400 lines. Action space of **149 indices**:

| Range | Type | Description |
|-------|------|-------------|
| 0–95 | `place` | card (1-6) × dest (6 piles or 2 new) × use_action |
| 96–110 | `switch` | 15 pile-pair choices for card 1/4 action |
| 111–128 | `swap` | 18 pile×position-pair choices for card 2/5 action |
| 129–146 | `deact` | 18 pile×play targets for card 3/6 action |
| 147 | `MM` | Make a Move (date phase) |
| 148 | `PS` | Play it Safe (date phase) |

Sub-phases (`action_switch`, `action_swap`, `action_deact`) are handled as
sequential MCTS decisions within the same player's turn.

### Neural network (`network.py`)

Shared MLP trunk (4 × Linear-LayerNorm-ReLU, 256 units) feeding into:
- **Policy head** → 149 logits, masked to legal actions, log-softmax
- **Value head** → 3 values (expected score per player), tanh × 20

Input features (240 dimensions):
- Phase one-hot [4] + current player [3]
- Cards played per player [18]
- Running total scores [3]
- 6 pile slots × 34 features (fill ratio + 3 plays × 11 features each)
- Date phase info [8] (date index, card 2 bonus, card 5 debuff flags)

### MCTS (`mcts.py`)

PUCT selection with 3-player Q-values. Each node stores W (total value)
and Q (mean value) as length-3 vectors. Selection uses the **moving player's**
Q-value for UCB comparison. Dirichlet noise (α=0.3, frac=0.25) is added at
the root during self-play to encourage exploration.

### Training (`train.py`)

1. **Self-play**: Generate games using MCTS. Each (state, MCTS-policy, outcome)
   tuple is stored in a replay buffer (50k–80k examples).
2. **Training**: Sample random mini-batches from replay. Minimize:
   - Policy loss: cross-entropy between MCTS visit-count policy and network logits
   - Value loss: MSE between network value predictions and actual final scores
3. **LR schedule**: StepLR decay every 5 iterations.

## Evaluation metrics (`evaluate.py`)

Run after training to check strategic quality:

1. **Score spread** — mean/std/win% per player (expect ~balanced win%)
2. **Positional advantage** — avg score by pile index for TW vs normals
   (random play shows ~−1 gap; trained AI should exploit positions better)
3. **Card placement diversity** — Shannon entropy of which card goes to which pile
   (2.58 bits = maximum, random baseline; trained AI should show lower entropy
   reflecting strategic card placement)
4. **MM/PS rates by context** — does the agent MM more as a normal player?
   Does it PS more when it's the third wheel? Does having a bonus change behaviour?
5. **Action use rate** — how often does the agent use vs. skip card actions?

## Known training issue: all-PS Nash equilibrium trap

All-PS is a valid Nash equilibrium:
- TW: PS gives 0; MM gives -2 (if normals PS) or +2 (if exactly one normal MMs) — risky
- Normal: PS gives +1; MM alone gives -1 — so MM is only good if the other normal also MMs

In vanilla self-play all three agents converge to PS/PS/PS and never explore
coordinated MM. Two fixes are applied:

1. **Epsilon-greedy date exploration** (`eps_date=0.35` in `train.py`): with 35%
   probability during self-play, a random MM or PS is chosen instead of MCTS.
   This forces the agent to experience MM outcomes and learn their value.

2. **Rollout blending in MCTS** (`mcts.py`): leaf evaluations blend 70% network
   value + 30% random rollout. The random rollout picks random MM/PS moves, so
   the tree sees realistic MM outcomes even before the network has learned them.

## What to expect after training

A well-trained agent should show:
- **Lower card placement entropy** than random (2.58b): Card 2 concentrates at
  pile 4 (2nd-arrival-is-TW dates), Card 4 shifts to late piles to protect its
  -1 end-game debuff from opponent card-3 activation
- **MM/PS context sensitivity**: higher MM rate as normal (>55%) vs as TW (<35%),
  and higher MM rate when card 2 bonus is active (TW safe in MM+MM+MM)
- **Balanced pile advantage**: TW-Normal gap uniform across all 6 pile positions
- **Balanced win rates**: ~33% per player
- **Action skipping pattern**: bonus cards 2/3 kept (modifier preserved);
  debuff cards 4/6 skipped (debuff avoided); card 1/3 actions used heavily
