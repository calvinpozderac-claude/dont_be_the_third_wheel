"""
Policy + Value network for Don't Be The Third Wheel.

Architecture: shared MLP trunk → policy head + value head.
  Policy head: logits over N_ACTIONS (149) — masked to legal actions.
  Value head: expected final score for each of 3 players.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

from engine import N_ACTIONS, STATE_DIM


class PolicyValueNet(nn.Module):
    def __init__(self, hidden=256, layers=4):
        super().__init__()
        trunk = []
        in_dim = STATE_DIM
        for _ in range(layers):
            trunk.extend([nn.Linear(in_dim, hidden), nn.LayerNorm(hidden), nn.ReLU()])
            in_dim = hidden
        self.trunk       = nn.Sequential(*trunk)
        self.policy_head = nn.Linear(hidden, N_ACTIONS)
        self.value_head  = nn.Linear(hidden, 3)   # one value per player

    def forward(self, x):
        """
        x: (batch, STATE_DIM) float tensor
        Returns:
          log_pi : (batch, N_ACTIONS)  log-softmax policy
          v      : (batch, 3)          expected scores (tanh scaled)
        """
        h      = self.trunk(x)
        log_pi = F.log_softmax(self.policy_head(h), dim=-1)
        v      = torch.tanh(self.value_head(h)) * 20.0  # scores typically -12..+12
        return log_pi, v

    @torch.no_grad()
    def predict(self, feat_np, legal_actions):
        """
        feat_np      : numpy (STATE_DIM,) float32
        legal_actions: list of int action indices

        Returns:
          policy : dict {action_idx: probability}
          value  : list of 3 floats
        """
        x      = torch.from_numpy(feat_np).unsqueeze(0)
        log_pi, v = self(x)
        log_pi = log_pi.squeeze(0)

        # Mask illegal actions
        mask = torch.full((N_ACTIONS,), float("-inf"))
        for a in legal_actions:
            mask[a] = 0.0
        masked_log_pi = log_pi + mask
        probs = torch.softmax(masked_log_pi, dim=-1)

        policy = {a: probs[a].item() for a in legal_actions}
        value  = v.squeeze(0).tolist()
        return policy, value


def save(net, path):
    torch.save(net.state_dict(), path)


def load(path, hidden=256, layers=4, device="cpu"):
    net = PolicyValueNet(hidden=hidden, layers=layers)
    net.load_state_dict(torch.load(path, map_location=device))
    net.eval()
    return net
