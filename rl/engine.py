"""
Game engine for Don't Be The Third Wheel — standalone, no Flask dependency.

Action index space (149 total):
  0-95   : place  = card(0-5) * 16 + dest(0-7) * 2 + use_action(0-1)
  96-110 : switch = pair(i,j), i<j, piles 0-5 → 15 combos
  111-128: swap   = pile(0-5)*3 + pair_idx(0-2),  pairs=(0,1),(0,2),(1,2)
  129-146: deact  = pile(0-5)*3 + play_pos(0-2)
  147    : date move MM
  148    : date move PS
"""

import copy
import random
from dataclasses import dataclass, field

# ── constants ─────────────────────────────────────────────────────────────────

N_ACTIONS   = 149
SNAKE_ORDER = [0, 1, 2, 2, 1, 0] * 3  # 18 turns, snake draft
CARD_TYPE   = {1:"bonus",2:"bonus",3:"bonus",4:"debuff",5:"debuff",6:"debuff"}
SWITCH_PAIRS = [(i,j) for i in range(6) for j in range(i+1,6)]  # 15 pairs
SWAP_PAIRS   = [(0,1),(0,2),(1,2)]                                 # 3 pairs

def _tw_pos(pile_idx):
    """Arrival index (0/1/2) that is third wheel for a pile at position pile_idx."""
    if pile_idx < 2: return 2
    if pile_idx < 4: return 1
    return 0

def _score_date(tw_m, n1_m, n2_m, c2=False, c5=False):
    """Return (tw, n1, n2) scores given moves and active modifiers."""
    tbl = {
        ("PS","PS","PS"): ( 0, 1, 1),
        ("PS","MM","PS"): ( 0,-1, 1),
        ("PS","PS","MM"): ( 0, 1,-1),
        ("PS","MM","MM"): ( 0, 2, 2),
        ("MM","PS","PS"): (-2, 1, 1),
        ("MM","MM","PS"): ( 2, 2, 0),
        ("MM","PS","MM"): ( 2, 0, 2),
        ("MM","MM","MM"): (-2, 2, 2),
    }
    tw, n1, n2 = tbl[(tw_m, n1_m, n2_m)]
    if tw_m == "MM" == n1_m == n2_m:
        if c2:  tw = 0
        elif c5: tw = -4
    return tw, n1, n2

# ── action encoding ───────────────────────────────────────────────────────────

def encode_place(card_num, dest, use_action):
    """card_num 1-6, dest 0-7 (6=new_left, 7=new_right), use_action bool."""
    return (card_num - 1) * 16 + dest * 2 + int(use_action)

def decode_place(idx):
    card_num = idx // 16 + 1
    rem      = idx % 16
    dest     = rem // 2
    use_action = bool(rem % 2)
    return card_num, dest, use_action

def encode_switch(i, j):
    if i > j: i, j = j, i
    return 96 + SWITCH_PAIRS.index((i, j))

def decode_switch(idx):
    return SWITCH_PAIRS[idx - 96]

def encode_swap(pile, pa, pb):
    pair = (min(pa,pb), max(pa,pb))
    return 111 + pile * 3 + SWAP_PAIRS.index(pair)

def decode_swap(idx):
    rem  = idx - 111
    pile = rem // 3
    pair = SWAP_PAIRS[rem % 3]
    return pile, pair[0], pair[1]

def encode_deact(pile, play_pos):
    return 129 + pile * 3 + play_pos

def decode_deact(idx):
    rem = idx - 129
    return rem // 3, rem % 3

# ── data classes ──────────────────────────────────────────────────────────────

@dataclass
class Play:
    player_idx:     int
    card_num:       int
    used_action:    bool
    modifier_active: bool  # True = bonus kept / debuff taken

@dataclass
class Stack:
    plays:          list = field(default_factory=list)
    date_resolved:  bool = False
    date_moves:     dict = field(default_factory=dict)   # {pidx: "MM"|"PS"}
    date_scores:    dict = field(default_factory=dict)   # {pidx: int}

@dataclass
class GameState:
    # per-player: {"score": int, "cards_played": set, "piles_played_to": set}
    players:              list
    stacks:               list       # list of Stack
    phase:                str        # card_playing | action_switch | action_swap | action_deact | date_move | end
    current_player_idx:   int
    total_scores:         list       # running date scores [p0, p1, p2]

    # action sub-phase context
    action_pile_idx:      int = -1   # pile most recently placed on

    # date phase
    date_idx:             int = 0
    date_submission_order: list = field(default_factory=lambda: [0,1,2])
    date_submitted_count: int = 0
    date_moves_current:   dict = field(default_factory=dict)

    def copy(self):
        return copy.deepcopy(self)

    def is_terminal(self):
        return self.phase == "end"

    def final_scores(self):
        if not self.is_terminal():
            return None
        scores = list(self.total_scores)
        for stack in self.stacks:
            for play in stack.plays:
                if play.card_num == 1 and play.modifier_active:
                    scores[play.player_idx] += 1
                elif play.card_num == 4 and play.modifier_active:
                    scores[play.player_idx] -= 1
        return scores

# ── game creation ─────────────────────────────────────────────────────────────

def new_game():
    players = [
        {"score": 0, "cards_played": set(), "piles_played_to": set()}
        for _ in range(3)
    ]
    return GameState(
        players=players,
        stacks=[],
        phase="card_playing",
        current_player_idx=0,
        total_scores=[0, 0, 0],
    )

# ── legal actions ─────────────────────────────────────────────────────────────

def get_legal_actions(state: GameState):
    """Return list of valid action indices."""
    if state.is_terminal():
        return []

    if state.phase == "card_playing":
        return _legal_place(state)

    if state.phase == "action_switch":
        return _legal_switch(state)

    if state.phase == "action_swap":
        return _legal_swap(state)

    if state.phase == "action_deact":
        return _legal_deact(state)

    if state.phase == "date_move":
        return [147, 148]  # MM, PS

    return []

def _legal_place(state):
    pidx  = state.current_player_idx
    p     = state.players[pidx]
    cards_left = [c for c in range(1, 7) if c not in p["cards_played"]]
    piles_available = [si for si, s in enumerate(state.stacks)
                       if si not in p["piles_played_to"]]
    can_new = len(state.stacks) < 6

    actions = []
    for card in cards_left:
        dests = list(piles_available)
        if can_new:
            dests += [6, 7]  # new_left, new_right
        for dest in dests:
            for use in (False, True):
                # Can only use action if card has one AND target exists
                if use and dest in (6, 7) and card in (2, 5):
                    # swap_order action on a new pile with 0 plays: no valid swap
                    # skip this (action has nothing to swap yet)
                    continue
                actions.append(encode_place(card, dest, use))
    return actions

def _legal_switch(state):
    n = len(state.stacks)
    if n < 2:
        return []
    return [encode_switch(i, j) for i in range(n) for j in range(i+1, n)]

def _legal_swap(state):
    """Any pile with >= 2 plays can be swapped."""
    actions = []
    for si, stack in enumerate(state.stacks):
        if len(stack.plays) < 2:
            continue
        avail_positions = list(range(len(stack.plays)))
        for pa, pb in SWAP_PAIRS:
            if pa < len(stack.plays) and pb < len(stack.plays):
                actions.append(encode_swap(si, pa, pb))
    return actions

def _legal_deact(state):
    """Target any opponent's play that was skipped (modifier_active for bonus, or not active for debuff)."""
    pidx = state.current_player_idx
    actions = []
    for si, stack in enumerate(state.stacks):
        for pi, play in enumerate(stack.plays):
            if play.player_idx == pidx:
                continue
            ctype = CARD_TYPE[play.card_num]
            # bonus: can deactivate (modifier_active=True → set to False)
            # debuff: can activate (modifier_active=False → set to True)
            # cards 1 and 4 are end-game; targeting them still valid
            if ctype == "bonus" and play.modifier_active:
                actions.append(encode_deact(si, pi))
            elif ctype == "debuff" and not play.modifier_active:
                actions.append(encode_deact(si, pi))
    return actions

# ── apply action ──────────────────────────────────────────────────────────────

def apply_action(state: GameState, action_idx: int) -> GameState:
    """Return new state after applying action. Mutates a copy."""
    s = state.copy()

    if s.phase == "card_playing":
        _apply_place(s, action_idx)
    elif s.phase == "action_switch":
        _apply_switch(s, action_idx)
        _finish_card_turn(s)
    elif s.phase == "action_swap":
        _apply_swap(s, action_idx)
        _finish_card_turn(s)
    elif s.phase == "action_deact":
        _apply_deact(s, action_idx)
        _finish_card_turn(s)
    elif s.phase == "date_move":
        _apply_date_move(s, action_idx)

    return s

def _apply_place(state, action_idx):
    card_num, dest, use_action = decode_place(action_idx)
    pidx = state.current_player_idx
    p    = state.players[pidx]

    # Create or select pile
    if dest == 6:  # new_left
        stack = Stack()
        state.stacks.insert(0, stack)
        si = 0
    elif dest == 7:  # new_right
        stack = Stack()
        state.stacks.append(stack)
        si = len(state.stacks) - 1
    else:
        si    = dest
        stack = state.stacks[si]

    ctype = CARD_TYPE[card_num]
    modifier_active = (ctype == "debuff") if use_action else (ctype == "bonus")

    play = Play(
        player_idx=pidx,
        card_num=card_num,
        used_action=use_action,
        modifier_active=modifier_active,
    )
    stack.plays.append(play)

    p["cards_played"].add(card_num)
    p["piles_played_to"].add(si)
    # After inserting at front, all pile indices shift by 1 — rebuild from truth
    if dest == 6:
        _rebuild_piles_played_to(state)

    state.action_pile_idx = si

    if use_action:
        if card_num in (1, 4):   # switch piles
            if len(state.stacks) >= 2:
                state.phase = "action_switch"
            else:
                _finish_card_turn(state)
        elif card_num in (2, 5): # swap order
            if any(len(state.stacks[i].plays) >= 2 for i in range(len(state.stacks))):
                state.phase = "action_swap"
            else:
                _finish_card_turn(state)
        elif card_num in (3, 6): # deact/act
            has_targets = bool(_legal_deact(state))
            if has_targets:
                state.phase = "action_deact"
            else:
                _finish_card_turn(state)
    else:
        _finish_card_turn(state)

def _rebuild_piles_played_to(state):
    """Recompute piles_played_to for all players from actual stack data."""
    for i, player in enumerate(state.players):
        player["piles_played_to"] = set()
    for si, stack in enumerate(state.stacks):
        for play in stack.plays:
            state.players[play.player_idx]["piles_played_to"].add(si)

def _apply_switch(state, action_idx):
    i, j = decode_switch(action_idx)
    if i < len(state.stacks) and j < len(state.stacks):
        state.stacks[i], state.stacks[j] = state.stacks[j], state.stacks[i]
        _rebuild_piles_played_to(state)

def _apply_swap(state, action_idx):
    pile, pa, pb = decode_swap(action_idx)
    if pile < len(state.stacks):
        plays = state.stacks[pile].plays
        if pa < len(plays) and pb < len(plays):
            plays[pa], plays[pb] = plays[pb], plays[pa]

def _apply_deact(state, action_idx):
    pile, pos = decode_deact(action_idx)
    if pile < len(state.stacks):
        plays = state.stacks[pile].plays
        if pos < len(plays):
            play  = plays[pos]
            ctype = CARD_TYPE[play.card_num]
            if ctype == "bonus":
                play.modifier_active = False   # deactivate bonus
            else:
                play.modifier_active = True    # activate debuff

def _finish_card_turn(state):
    state.phase = "card_playing"
    state.action_pile_idx = -1
    total_played = sum(len(p["cards_played"]) for p in state.players)
    if total_played == 18:
        _setup_date_phase(state)
    else:
        state.current_player_idx = SNAKE_ORDER[total_played]

def _setup_date_phase(state):
    state.phase = "date_move"
    state.date_idx = 0
    state.date_submission_order = _submission_order(state, 0)
    state.date_submitted_count  = 0
    state.date_moves_current    = {}
    state.current_player_idx    = state.date_submission_order[0]

def _submission_order(state, di):
    """Submission order for date di: accounts for card 3 (peek, goes last) and card 6 (show, goes first)."""
    order = [0, 1, 2]
    if di >= len(state.stacks):
        return order
    plays = state.stacks[di].plays
    show_player = None
    peek_player = None
    for play in plays:
        if play.card_num == 6 and play.modifier_active:
            show_player = play.player_idx
        if play.card_num == 3 and play.modifier_active:
            peek_player = play.player_idx
    if show_player is not None:
        order.remove(show_player)
        order.insert(0, show_player)
    if peek_player is not None:
        order.remove(peek_player)
        order.append(peek_player)
    return order

def _apply_date_move(state, action_idx):
    move = "MM" if action_idx == 147 else "PS"
    pidx = state.current_player_idx
    state.date_moves_current[pidx] = move
    state.date_submitted_count += 1

    if state.date_submitted_count >= 3:
        _resolve_date(state)
        _advance_date(state)
    else:
        next_submitter = state.date_submission_order[state.date_submitted_count]
        state.current_player_idx = next_submitter

def _resolve_date(state):
    di    = state.date_idx
    stack = state.stacks[di]
    plays = stack.plays
    twp   = _tw_pos(di)

    tw_pidx = plays[twp].player_idx
    normals = [plays[i].player_idx for i in range(3) if i != twp]
    n1_pidx, n2_pidx = normals

    tw_m  = state.date_moves_current.get(tw_pidx, "PS")
    n1_m  = state.date_moves_current.get(n1_pidx, "PS")
    n2_m  = state.date_moves_current.get(n2_pidx, "PS")

    c2 = any(p.card_num == 2 and p.modifier_active for p in plays)
    c5 = any(p.card_num == 5 and p.modifier_active for p in plays)

    tw_s, n1_s, n2_s = _score_date(tw_m, n1_m, n2_m, c2, c5)

    stack.date_moves  = dict(state.date_moves_current)
    stack.date_scores = {tw_pidx: tw_s, n1_pidx: n1_s, n2_pidx: n2_s}
    stack.date_resolved = True

    state.total_scores[tw_pidx]  += tw_s
    state.total_scores[n1_pidx]  += n1_s
    state.total_scores[n2_pidx]  += n2_s

def _advance_date(state):
    state.date_idx += 1
    if state.date_idx >= len(state.stacks):
        state.phase = "end"
        return
    state.date_submission_order = _submission_order(state, state.date_idx)
    state.date_submitted_count  = 0
    state.date_moves_current    = {}
    state.current_player_idx    = state.date_submission_order[0]

# ── state featurization ───────────────────────────────────────────────────────

STATE_DIM = 4 + 3 + 18 + 3 + 6*34 + 8  # 240

def featurize(state: GameState):
    """Return 1-D numpy float32 array of length STATE_DIM."""
    import numpy as np
    f = []

    # Phase one-hot [4]
    phase_map = {"card_playing": 0, "action_switch": 1, "action_swap": 2,
                 "action_deact": 3, "date_move": 3}
    ph = [0.0] * 4
    ph[phase_map.get(state.phase, 0)] = 1.0
    f.extend(ph)

    # Current player [3]
    cp = [0.0] * 3
    cp[state.current_player_idx] = 1.0
    f.extend(cp)

    # Cards played per player [3*6=18]
    for p in state.players:
        for c in range(1, 7):
            f.append(1.0 if c in p["cards_played"] else 0.0)

    # Normalised total scores [3]
    for sc in state.total_scores:
        f.append(sc / 10.0)

    # Stacks [6 * 34 = 204]
    for si in range(6):
        if si < len(state.stacks):
            stack = state.stacks[si]
            f.append(len(stack.plays) / 3.0)   # fill ratio [1]
            for pi in range(3):
                if pi < len(stack.plays):
                    play = stack.plays[pi]
                    one_p = [0.0]*3; one_p[play.player_idx] = 1.0
                    one_c = [0.0]*6; one_c[play.card_num-1] = 1.0
                    f.extend(one_p)             # player [3]
                    f.extend(one_c)             # card   [6]
                    f.append(float(play.used_action))      # [1]
                    f.append(float(play.modifier_active))  # [1]
                else:
                    f.extend([0.0] * 11)
        else:
            f.extend([0.0] * 34)

    # Date phase info [8]
    if state.phase == "date_move" and state.date_idx < len(state.stacks):
        plays = state.stacks[state.date_idx].plays
        one_d = [0.0]*6; one_d[state.date_idx] = 1.0
        f.extend(one_d)
        f.append(1.0 if any(p.card_num==2 and p.modifier_active for p in plays) else 0.0)
        f.append(1.0 if any(p.card_num==5 and p.modifier_active for p in plays) else 0.0)
    else:
        f.extend([0.0] * 8)

    return np.array(f, dtype=np.float32)

# ── rollout (random playout) ──────────────────────────────────────────────────

def random_rollout(state: GameState):
    """Play random moves to terminal. Returns final scores list."""
    s = state.copy()
    while not s.is_terminal():
        actions = get_legal_actions(s)
        if not actions:
            break
        s = apply_action(s, random.choice(actions))
    return s.final_scores() or [0, 0, 0]
