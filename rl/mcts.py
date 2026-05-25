"""
MCTS for 3-player Don't Be The Third Wheel.

Uses PUCT (AlphaZero style):
  U(s,a) = Q(s,a) + c_puct * P(s,a) * sqrt(N(s)) / (1 + N(s,a))

For 3 players we track expected value per player at each node.
Backprop updates Q for the player who moved, but stores all 3 values
so the parent node can use the right player's Q.
"""

import math
import random
import numpy as np

from engine import GameState, get_legal_actions, apply_action, featurize, random_rollout

C_PUCT      = 1.5
DIRICHLET_A = 0.3
DIR_FRAC    = 0.25      # fraction of Dirichlet noise at root


class MCTSNode:
    __slots__ = ("state", "parent", "action", "children",
                 "N", "W", "Q", "P", "legal", "expanded")

    def __init__(self, state: GameState, parent=None, action=None, prior=0.0):
        self.state    = state
        self.parent   = parent
        self.action   = action          # action that led here
        self.children = {}              # action_idx → MCTSNode
        self.N        = 0               # visit count
        self.W        = np.zeros(3)     # total value per player
        self.Q        = np.zeros(3)     # mean value per player
        self.P        = prior           # prior probability
        self.legal    = None            # legal actions (set on first expansion)
        self.expanded = False

    @property
    def is_leaf(self):
        return not self.expanded


class MCTS:
    def __init__(self, net, n_simulations=200, c_puct=C_PUCT):
        self.net   = net
        self.n_sim = n_simulations
        self.c     = c_puct

    def search(self, root_state: GameState, add_noise=True):
        """Run n_simulations from root_state. Returns action probability distribution."""
        root = MCTSNode(root_state)
        self._expand(root)

        if add_noise and root.legal:
            noise = np.random.dirichlet([DIRICHLET_A] * len(root.legal))
            for i, a in enumerate(root.legal):
                child_prior = root.children[a].P
                root.children[a].P = (1 - DIR_FRAC) * child_prior + DIR_FRAC * noise[i]

        for _ in range(self.n_sim):
            node = root
            path = [node]

            # Selection
            while not node.is_leaf and not node.state.is_terminal():
                node = self._select_child(node)
                path.append(node)

            # Expansion + evaluation
            if node.state.is_terminal():
                values = np.array(node.state.final_scores(), dtype=float)
            elif node.is_leaf:
                self._expand(node)
                values = self._evaluate(node)
            else:
                values = self._evaluate(node)

            # Backpropagation
            for n in reversed(path):
                n.N += 1
                n.W += values
                n.Q  = n.W / n.N

        # Build policy: visit counts normalised
        total = sum(c.N for c in root.children.values())
        if total == 0:
            total = 1
        policy = {a: c.N / total for a, c in root.children.items()}
        return policy

    def best_action(self, state: GameState, temperature=1.0, add_noise=False):
        """Return (action_idx, policy_dict)."""
        policy = self.search(state, add_noise=add_noise)
        if not policy:
            legal = get_legal_actions(state)
            return random.choice(legal), {}
        if temperature < 0.01:
            best = max(policy, key=policy.get)
            return best, policy
        actions = list(policy.keys())
        probs   = np.array([policy[a] for a in actions])
        probs   = probs ** (1.0 / temperature)
        probs  /= probs.sum()
        chosen  = np.random.choice(len(actions), p=probs)
        return actions[chosen], policy

    # ── private ──────────────────────────────────────────────────────────────

    def _expand(self, node: MCTSNode):
        if node.state.is_terminal():
            node.expanded = True
            node.legal    = []
            return

        legal = get_legal_actions(node.state)
        node.legal = legal

        if not legal:
            node.expanded = True
            return

        feat     = featurize(node.state)
        policy, _value = self.net.predict(feat, legal)

        for a in legal:
            child_state = apply_action(node.state, a)
            child = MCTSNode(child_state, parent=node, action=a, prior=policy.get(a, 1.0/len(legal)))
            node.children[a] = child

        node.expanded = True

    def _select_child(self, node: MCTSNode):
        """PUCT selection."""
        sqrt_N = math.sqrt(node.N + 1)
        mover  = node.state.current_player_idx

        best_val  = -float("inf")
        best_node = None
        for a, child in node.children.items():
            q_val  = child.Q[mover]
            u_val  = self.c * child.P * sqrt_N / (1 + child.N)
            score  = q_val + u_val
            if score > best_val:
                best_val  = score
                best_node = child
        return best_node

    def _evaluate(self, node: MCTSNode):
        """NN value evaluation (+ random rollout blend for early training)."""
        if node.state.is_terminal():
            return np.array(node.state.final_scores(), dtype=float)
        feat     = featurize(node.state)
        _, value = self.net.predict(feat, node.legal or [])
        return np.array(value, dtype=float)
