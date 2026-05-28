from flask import Flask, session, request, jsonify, render_template
import random

app = Flask(__name__)
app.secret_key = "dtbtw-v2-2024"

# ── shared room storage ────────────────────────────────────────────────────────

_rooms = {}

def _gen_code():
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(random.choices(chars, k=6))
        if code not in _rooms:
            return code

# ── card definitions ───────────────────────────────────────────────────────────

CARD_INFO = {
    1: {"type": "bonus",  "action": "Switch any two date piles",                                                          "modifier": "+1 point at end of game",                                    "modifier_type": "global_plus"},
    2: {"type": "bonus",  "action": "Swap the arrival order of any two cards in a date pile",                             "modifier": "If third wheel in MM+MM+MM: score 0 instead of −2",          "modifier_type": "tw_protect"},
    3: {"type": "bonus",  "action": "Deactivate an opponent's active bonus, or activate an opponent's avoided debuff",    "modifier": "Peek at one other player's move before choosing yours this date", "modifier_type": "peek"},
    4: {"type": "debuff", "action": "Switch any two date piles",                                                          "modifier": "−1 point at end of game",                                    "modifier_type": "global_minus"},
    5: {"type": "debuff", "action": "Swap the arrival order of any two cards in a date pile",                             "modifier": "If third wheel in MM+MM+MM: score −4 instead of −2",         "modifier_type": "tw_double"},
    6: {"type": "debuff", "action": "Deactivate an opponent's active bonus, or activate an opponent's avoided debuff",    "modifier": "Must show your move to one player before they choose this date", "modifier_type": "show"},
}

PLAYER_COLORS = ["#E05555", "#5578E0", "#3DC470"]
PLAYER_DARK   = ["#8B2020", "#203070", "#1A7035"]
PLAYER_LIGHT  = ["#FFCCCC", "#CCE0FF", "#C8FFDC"]

# Snake draft: 0,1,2,2,1,0 repeating → each player gets 6 turns over 18 picks
SNAKE_ORDER = [0, 1, 2, 2, 1, 0]

# ── state helpers ──────────────────────────────────────────────────────────────

def _new_state(room_code):
    return {
        "room_code":             room_code,
        "version":               0,
        "game_mode":             "local",   # "local" | "online"
        "player_types":          ["human", "human", "human"],
        "player_joined":         [True, True, True],
        "phase":                 "select",  # select|lobby|card_playing|date_phase|end
        "players":               [],
        "stacks":                [],
        "next_stack_id":         0,
        "current_player_idx":    0,
        "pending_action":        None,
        "action_ctx":            {},
        "action_message":        None,
        "date_phase_idx":        0,
        "date_submission_order": [],
        "date_submitted_count":  0,
        "date_peek_info":        None,
        "date_show_info":        None,
        "total_scores":          [0, 0, 0],
        "game_over":             False,
        "move_log":              [],
    }

def _gs():
    code = session.get("room_code")
    if code:
        s = _rooms.get(code)
        if s:
            return s
    code = _gen_code()
    s = _new_state(code)
    _rooms[code] = s
    session["room_code"]     = code
    session["my_player_idx"] = -1
    session.modified = True
    return s

def _save(s):
    s["version"] = s.get("version", 0) + 1
    _rooms[s["room_code"]] = s
    session.modified = True

def _my_pidx():
    return session.get("my_player_idx", -1)

def _card_type(n):
    return "bonus" if n <= 3 else "debuff"

def _modifier_initial(card_num, used_action):
    if _card_type(card_num) == "bonus":
        return not used_action
    else:
        return used_action

def _get_played_cards(state, pidx):
    return [p["card_num"]
            for s in state["stacks"]
            for p in s["plays"]
            if p["player_idx"] == pidx]

def _get_stack_by_id(state, sid):
    for s in state["stacks"]:
        if s["id"] == sid:
            return s
    return None

def _pile_index(state, stack_id):
    return next((i for i, s in enumerate(state["stacks"]) if s["id"] == stack_id), -1)

def _tw_pos(date_idx):
    if date_idx < 2: return 2
    if date_idx < 4: return 1
    return 0

def _all_played(state):
    return (sum(len(s["plays"]) for s in state["stacks"]) == 18
            and len(state["stacks"]) == 6)

# ── AI helpers ─────────────────────────────────────────────────────────────────

def _ai_resolve_action(state):
    action = state["pending_action"]
    actor  = state["action_ctx"].get("actor", 0)
    aname  = state["players"][actor]["name"]
    msg    = None

    if action == "switch_piles" and len(state["stacks"]) >= 2:
        i1, i2 = random.sample(range(len(state["stacks"])), 2)
        state["stacks"][i1], state["stacks"][i2] = state["stacks"][i2], state["stacks"][i1]
        msg = f"Switched pile positions {i1 + 1} and {i2 + 1}!"

    elif action == "swap_order":
        valid = [i for i, s in enumerate(state["stacks"]) if len(s["plays"]) >= 2]
        if valid:
            si = random.choice(valid)
            plays = state["stacks"][si]["plays"]
            p1, p2 = random.sample(range(len(plays)), 2)
            plays[p1], plays[p2] = plays[p2], plays[p1]
            msg = f"Swapped arrival order in pile {si + 1}!"

    elif action == "deact_act":
        targets = [
            (si, pi)
            for si, s in enumerate(state["stacks"])
            for pi, p in enumerate(s["plays"])
            if p["player_idx"] != actor and not p["used_action"]
        ]
        if targets:
            si, pi = random.choice(targets)
            tp = state["stacks"][si]["plays"][pi]
            tp["modifier_active"] = not tp["modifier_active"]
            tname = state["players"][tp["player_idx"]]["name"]
            ct    = _card_type(tp["card_num"])
            if ct == "bonus":
                msg = f"Deactivated {tname}'s Card {tp['card_num']} bonus!"
            else:
                msg = f"Activated {tname}'s Card {tp['card_num']} debuff!"

    if msg:
        _log(state, actor, f"{aname}: {msg}")
    state["pending_action"] = None
    state["action_ctx"]     = {}
    state["action_message"] = None

def _ai_play_one(state):
    """Play one card for the current AI player. Returns True if a turn was taken."""
    pidx = state["current_player_idx"]
    if state["player_types"][pidx] != "ai":
        return False

    player       = state["players"][pidx]
    played_cards = _get_played_cards(state, pidx)
    avail_cards  = [c for c in range(1, 7) if c not in played_cards]
    if not avail_cards:
        return False

    card_num     = random.choice(avail_cards)
    played_to    = set(player["cards_played_to"])
    avail_stacks = [s["id"] for s in state["stacks"] if s["id"] not in played_to]
    new_opts     = ["new_left", "new_right"] if len(state["stacks"]) < 6 else []
    all_opts     = avail_stacks + new_opts
    if not all_opts:
        return False

    target     = random.choice(all_opts)
    use_action = random.choice([True, False])

    # resolve destination
    if target == "new_left":
        sid = state["next_stack_id"]
        state["next_stack_id"] += 1
        state["stacks"].insert(0, {"id": sid, "plays": [], "date_resolved": False, "date_moves": {}, "date_scores": {}})
        actual_sid = sid
    elif target == "new_right":
        sid = state["next_stack_id"]
        state["next_stack_id"] += 1
        state["stacks"].append({"id": sid, "plays": [], "date_resolved": False, "date_moves": {}, "date_scores": {}})
        actual_sid = sid
    else:
        actual_sid = target

    stack = _get_stack_by_id(state, actual_sid)
    if stack is None:
        return False

    play = {
        "player_idx":      pidx,
        "card_num":        card_num,
        "used_action":     use_action,
        "modifier_active": _modifier_initial(card_num, use_action),
    }
    stack["plays"].append(play)
    player["cards_played_to"].append(actual_sid)

    pile_label = "a new pile" if target in ("new_left", "new_right") else f"Pile {_pile_index(state, actual_sid) + 1}"
    card_info  = CARD_INFO[card_num]
    if use_action:
        mod_note = f"⚡ Used {card_info['action'][:40]}"
    else:
        if card_info["type"] == "bonus":
            mod_note = f"✓ Keeping bonus: {card_info['modifier']}"
        else:
            mod_note = f"○ Avoided debuff: {card_info['modifier']}"
    _log(state, pidx, f"{player['name']}: Card {card_num} → {pile_label}", mod_note)

    # pending action resolution
    pending = None
    if use_action:
        if card_num in (1, 4) and len(state["stacks"]) >= 2:
            pending = "switch_piles"
        elif card_num in (2, 5) and any(len(s["plays"]) >= 2 for s in state["stacks"]):
            pending = "swap_order"
        elif card_num in (3, 6):
            has_target = any(
                p["player_idx"] != pidx and not p["used_action"]
                for s in state["stacks"] for p in s["plays"]
            )
            if has_target:
                pending = "deact_act"

    if pending:
        state["pending_action"] = pending
        state["action_ctx"]     = {"actor": pidx}
        _ai_resolve_action(state)

    _advance_card_turn(state)
    return True

def _ai_date_one(state):
    """Submit one AI move in the date phase. Returns True if a submission was made."""
    di    = state["date_phase_idx"]
    if di >= len(state["stacks"]):
        return False
    stack = state["stacks"][di]
    if stack.get("date_resolved"):
        return False
    count = state["date_submitted_count"]
    order = state["date_submission_order"]
    if count >= len(order):
        return False

    submitter = order[count]
    if state["player_types"][submitter] != "ai":
        return False

    # show debuff
    show = state["date_show_info"]
    if show and show["player"] == submitter and show["reveal_to"] is None:
        others = [i for i in range(3) if i != submitter]
        state["date_show_info"]["reveal_to"] = random.choice(others)

    # peek bonus
    peek = state["date_peek_info"]
    if peek and peek["player"] == submitter and peek["peek_target"] is None:
        already = order[:count]
        state["date_peek_info"]["peek_target"] = random.choice(already) if already else -1

    move = random.choice(["MM", "PS"])
    stack["date_moves"][str(submitter)] = move
    state["date_submitted_count"] += 1

    if state["date_submitted_count"] >= 3:
        _finish_date(state, di)

    return True

def _finish_date(state, di):
    stack  = state["stacks"][di]
    scores = _score_date(state, di)
    stack["date_scores"]   = {str(k): v for k, v in scores.items()}
    stack["date_resolved"] = True
    for pi, sc in scores.items():
        state["total_scores"][pi] += sc

    plays   = stack["plays"]
    tw_pidx = plays[_tw_pos(di)]["player_idx"]
    moves   = stack["date_moves"]
    parts   = " · ".join(
        f"{state['players'][pi]['name']} {moves.get(str(pi),'?')} "
        f"{'+' if sc >= 0 else ''}{sc}"
        for pi, sc in scores.items()
    )
    _log(state, -1, f"Date {di + 1}: {parts}",
         f"3W: {state['players'][tw_pidx]['name']}")

    next_di = di + 1
    if next_di >= 6:
        state["phase"]     = "end"
        state["game_over"] = True
    else:
        _setup_date(state, next_di)

def _process_ai(state):
    """Drive all consecutive AI turns to completion after a human action."""
    if state["phase"] == "card_playing":
        while state["phase"] == "card_playing" and not state["pending_action"]:
            if not _ai_play_one(state):
                break
    if state["phase"] == "date_phase":
        while state["phase"] == "date_phase":
            if not _ai_date_one(state):
                break

# ── game logic ─────────────────────────────────────────────────────────────────

def _score_date(state, di):
    stack = state["stacks"][di]
    plays = stack["plays"]
    if len(plays) != 3:
        return {}

    twp = _tw_pos(di)
    tw  = plays[twp]["player_idx"]
    normals = [plays[i]["player_idx"] for i in range(3) if i != twp]
    n0, n1 = normals[0], normals[1]

    m  = stack["date_moves"]
    tm = m.get(str(tw), "PS")
    m0 = m.get(str(n0),  "PS")
    m1 = m.get(str(n1),  "PS")

    if tm == "PS":
        if   m0 == "PS" and m1 == "PS": base = {tw: 0, n0:  1, n1:  1}
        elif m0 == "MM" and m1 == "PS": base = {tw: 0, n0: -1, n1:  1}
        elif m0 == "PS" and m1 == "MM": base = {tw: 0, n0:  1, n1: -1}
        else:                           base = {tw: 0, n0:  2, n1:  2}
    else:
        if   m0 == "PS" and m1 == "PS": base = {tw: -2, n0: 1, n1: 1}
        elif m0 == "MM" and m1 == "PS": base = {tw:  2, n0: 1, n1: 0}
        elif m0 == "PS" and m1 == "MM": base = {tw:  2, n0: 0, n1: 1}
        else:
            tw_score = -2
            for play in plays:
                if play["player_idx"] == tw and play["modifier_active"]:
                    if   play["card_num"] == 2: tw_score = 0
                    elif play["card_num"] == 5: tw_score = -4
            base = {tw: tw_score, n0: 1, n1: 1}

    return base

def _calc_final_scores(state):
    scores = list(state["total_scores"])
    for stack in state["stacks"]:
        for play in stack["plays"]:
            if play["modifier_active"]:
                pi = play["player_idx"]
                if   play["card_num"] == 1: scores[pi] += 1
                elif play["card_num"] == 4: scores[pi] -= 1
    return scores

def _setup_date(state, di):
    if di >= len(state["stacks"]):
        state["phase"]     = "end"
        state["game_over"] = True
        return

    stack = state["stacks"][di]
    plays = stack["plays"]
    twp   = _tw_pos(di)

    peek_player = None
    show_player = None
    for play in plays:
        if play["modifier_active"]:
            if   play["card_num"] == 3: peek_player = play["player_idx"]
            elif play["card_num"] == 6: show_player = play["player_idx"]

    order = [plays[0]["player_idx"], plays[1]["player_idx"], plays[2]["player_idx"]]
    if show_player is not None and show_player in order:
        order.remove(show_player)
        order.insert(0, show_player)
    if peek_player is not None and peek_player in order:
        order.remove(peek_player)
        order.append(peek_player)

    state["date_phase_idx"]        = di
    state["date_submission_order"] = order
    state["date_submitted_count"]  = 0
    stack["date_moves"]            = {}
    stack["date_scores"]           = {}
    state["date_peek_info"] = {"player": peek_player, "peek_target": None} if peek_player is not None else None
    state["date_show_info"] = {"player": show_player, "reveal_to":   None} if show_player is not None else None

def _advance_card_turn(state):
    if _all_played(state):
        state["phase"] = "date_phase"
        _setup_date(state, 0)
    else:
        total_played = sum(len(s["plays"]) for s in state["stacks"])
        state["current_player_idx"] = SNAKE_ORDER[total_played % len(SNAKE_ORDER)]
    state["pending_action"] = None
    state["action_ctx"]     = {}

def _enrich(state):
    s = dict(state)
    s["card_info"]      = CARD_INFO
    s["player_colors"]  = PLAYER_COLORS
    s["player_dark"]    = PLAYER_DARK
    s["player_light"]   = PLAYER_LIGHT
    s["my_player_idx"]  = _my_pidx()
    if state["phase"] == "end":
        s["final_scores"] = _calc_final_scores(state)
    if state["phase"] == "date_phase":
        di = state["date_phase_idx"]
        if di < len(state["stacks"]):
            plays = state["stacks"][di]["plays"]
            if len(plays) == 3:
                twp = _tw_pos(di)
                s["current_date_tw"]      = plays[twp]["player_idx"]
                s["current_date_normals"] = [plays[i]["player_idx"] for i in range(3) if i != twp]
    return s

def _reset_game_fields(state):
    state["stacks"]               = []
    state["next_stack_id"]        = 0
    state["current_player_idx"]   = 0
    state["pending_action"]       = None
    state["action_ctx"]           = {}
    state["action_message"]       = None
    state["date_phase_idx"]       = 0
    state["date_submission_order"] = []
    state["date_submitted_count"] = 0
    state["date_peek_info"]       = None
    state["date_show_info"]       = None
    state["total_scores"]         = [0, 0, 0]
    state["game_over"]            = False
    state["move_log"]             = []
    for p in state["players"]:
        p["cards_played_to"] = []

def _log(state, player_idx, text, sub=None):
    state["move_log"].append({
        "player_idx": player_idx,
        "text": text,
        "sub":  sub,
    })
    # cap at 100 entries
    if len(state["move_log"]) > 100:
        state["move_log"] = state["move_log"][-100:]

# ── routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/state")
def api_state():
    return jsonify(_enrich(_gs()))

# ── local game setup ───────────────────────────────────────────────────────────

@app.route("/api/setup", methods=["POST"])
def api_setup():
    data     = request.json or {}
    names    = data.get("names", [])
    ai_slots = [int(i) for i in data.get("ai_slots", [])]

    state = _gs()
    state["game_mode"]    = "local"
    state["player_joined"] = [True, True, True]
    state["player_types"] = []
    state["players"]      = []

    for i in range(3):
        if i in ai_slots:
            state["player_types"].append("ai")
            state["players"].append({"name": "AI", "cards_played_to": []})
        else:
            raw  = names[i] if i < len(names) else ""
            name = str(raw).strip() or f"Player {i + 1}"
            state["player_types"].append("human")
            state["players"].append({"name": name, "cards_played_to": []})

    state["phase"] = "card_playing"
    _reset_game_fields(state)

    _process_ai(state)
    _save(state)
    return jsonify(_enrich(state))

# ── online room management ─────────────────────────────────────────────────────

@app.route("/api/create_room", methods=["POST"])
def api_create_room():
    data     = request.json or {}
    name     = str(data.get("name", "")).strip() or "Player 1"
    ai_slots = [int(i) for i in data.get("ai_slots", [])]

    code  = _gen_code()
    state = _new_state(code)
    state["game_mode"]    = "online"
    state["phase"]        = "lobby"
    state["player_types"] = []
    state["players"]      = []
    state["player_joined"] = []

    for i in range(3):
        if i in ai_slots:
            state["player_types"].append("ai")
            state["players"].append({"name": "AI", "cards_played_to": []})
            state["player_joined"].append(True)
        elif i == 0:
            state["player_types"].append("human")
            state["players"].append({"name": name, "cards_played_to": []})
            state["player_joined"].append(True)
        else:
            state["player_types"].append("human")
            state["players"].append({"name": "Waiting…", "cards_played_to": []})
            state["player_joined"].append(False)

    _rooms[code] = state
    session["room_code"]     = code
    session["my_player_idx"] = 0
    session.modified = True
    return jsonify(_enrich(state))

@app.route("/api/join_room", methods=["POST"])
def api_join_room():
    data  = request.json or {}
    code  = str(data.get("room_code", "")).upper().strip()
    name  = str(data.get("name", "")).strip() or "Player"

    state = _rooms.get(code)
    if not state:
        return jsonify({"error": "Room not found. Check the code and try again."}), 404
    if state["phase"] != "lobby":
        return jsonify({"error": "This game has already started."}), 400

    slot = next(
        (i for i in range(3)
         if state["player_types"][i] == "human" and not state["player_joined"][i]),
        None,
    )
    if slot is None:
        return jsonify({"error": "This room is full."}), 400

    state["players"][slot]["name"] = name
    state["player_joined"][slot]   = True
    _rooms[code] = state

    session["room_code"]     = code
    session["my_player_idx"] = slot
    session.modified = True
    return jsonify({"player_idx": slot, **_enrich(state)})

@app.route("/api/start_game", methods=["POST"])
def api_start_game():
    code  = session.get("room_code")
    pidx  = session.get("my_player_idx", -1)
    state = _rooms.get(code)

    if not state:
        return jsonify({"error": "Room not found"}), 404
    if pidx != 0:
        return jsonify({"error": "Only the host can start the game"}), 403
    if not all(state["player_joined"]):
        return jsonify({"error": "Waiting for more players to join"}), 400

    state["phase"] = "card_playing"
    _reset_game_fields(state)
    _process_ai(state)
    _save(state)
    return jsonify(_enrich(state))

# ── game actions ───────────────────────────────────────────────────────────────

@app.route("/api/play_card", methods=["POST"])
def api_play_card():
    data  = request.json or {}
    state = _gs()
    if state["phase"] != "card_playing" or state["pending_action"]:
        return jsonify({"error": "Cannot play a card right now"}), 400

    pidx    = state["current_player_idx"]
    my_pidx = _my_pidx()
    if my_pidx != -1 and my_pidx != pidx:
        return jsonify({"error": "It's not your turn"}), 403

    player   = state["players"][pidx]
    card_num = int(data.get("card_num", 0))
    target   = data.get("stack_target")
    use_act  = bool(data.get("use_action", False))

    played = _get_played_cards(state, pidx)
    if card_num < 1 or card_num > 6 or card_num in played:
        return jsonify({"error": "Invalid card selection"}), 400

    if target == "new_left":
        if len(state["stacks"]) >= 6:
            return jsonify({"error": "Maximum 6 stacks already exist"}), 400
        sid = state["next_stack_id"]
        state["next_stack_id"] += 1
        state["stacks"].insert(0, {"id": sid, "plays": [], "date_resolved": False, "date_moves": {}, "date_scores": {}})
        actual_sid = sid
    elif target == "new_right":
        if len(state["stacks"]) >= 6:
            return jsonify({"error": "Maximum 6 stacks already exist"}), 400
        sid = state["next_stack_id"]
        state["next_stack_id"] += 1
        state["stacks"].append({"id": sid, "plays": [], "date_resolved": False, "date_moves": {}, "date_scores": {}})
        actual_sid = sid
    else:
        actual_sid = int(target)
        if not any(s["id"] == actual_sid for s in state["stacks"]):
            return jsonify({"error": "Stack not found"}), 400

    if actual_sid in player["cards_played_to"]:
        return jsonify({"error": "Already played to this stack"}), 400

    stack = _get_stack_by_id(state, actual_sid)
    if stack is None:
        return jsonify({"error": "Stack not found"}), 400

    play = {
        "player_idx":      pidx,
        "card_num":        card_num,
        "used_action":     use_act,
        "modifier_active": _modifier_initial(card_num, use_act),
    }
    stack["plays"].append(play)
    player["cards_played_to"].append(actual_sid)

    # ── move log ──────────────────────────────────────────────────────────────
    pile_label = "a new pile" if target in ("new_left", "new_right") else f"Pile {_pile_index(state, actual_sid) + 1}"
    card_info  = CARD_INFO[card_num]
    if use_act:
        modifier_note = f"⚡ Used {card_info['action'][:40]}"
    else:
        if card_info["type"] == "bonus":
            modifier_note = f"✓ Keeping bonus: {card_info['modifier']}"
        else:
            modifier_note = f"○ Avoided debuff: {card_info['modifier']}"
    _log(state, pidx, f"{player['name']}: Card {card_num} → {pile_label}", modifier_note)
    # ─────────────────────────────────────────────────────────────────────────

    pending = None
    msg     = None
    ctx     = {"actor": pidx, "log_idx": len(state["move_log"]) - 1}

    if use_act:
        if card_num in (1, 4):
            if len(state["stacks"]) >= 2:
                pending = "switch_piles"
                msg     = (f"Card {card_num} — Switch Piles: "
                           "Select any two date piles to swap their positions.")
        elif card_num in (2, 5):
            if any(len(s["plays"]) >= 2 for s in state["stacks"]):
                pending = "swap_order"
                msg     = (f"Card {card_num} — Swap Order: "
                           "Select a pile with 2+ cards, then pick two cards to swap.")
        elif card_num in (3, 6):
            has_target = any(
                p["player_idx"] != pidx and not p["used_action"]
                for s in state["stacks"] for p in s["plays"]
            )
            if has_target:
                pending = "deact_act"
                msg     = (f"Card {card_num} — Deactivate/Activate: "
                           "Select an opponent's card that was played WITHOUT action.")

    if pending:
        state["pending_action"] = pending
        state["action_ctx"]     = ctx
        state["action_message"] = msg
    else:
        state["action_message"] = None
        _advance_card_turn(state)
        _process_ai(state)

    _save(state)
    return jsonify(_enrich(state))

@app.route("/api/resolve_action", methods=["POST"])
def api_resolve_action():
    data   = request.json or {}
    state  = _gs()
    action = state["pending_action"]
    if not action:
        return jsonify({"error": "No pending action"}), 400

    actor = state["action_ctx"].get("actor", 0)
    my_pidx = _my_pidx()
    if my_pidx != -1 and my_pidx != actor:
        return jsonify({"error": "Not your action to resolve"}), 403

    err = None
    msg = None

    if action == "switch_piles":
        id1 = int(data.get("stack1_id", -1))
        id2 = int(data.get("stack2_id", -1))
        i1  = next((i for i, s in enumerate(state["stacks"]) if s["id"] == id1), -1)
        i2  = next((i for i, s in enumerate(state["stacks"]) if s["id"] == id2), -1)
        if i1 == -1 or i2 == -1:
            err = "Stack not found."
        elif i1 == i2:
            err = "Must select two different piles."
        else:
            state["stacks"][i1], state["stacks"][i2] = state["stacks"][i2], state["stacks"][i1]
            msg = f"Switched pile positions {i1 + 1} and {i2 + 1}!"

    elif action == "swap_order":
        si  = int(data.get("stack_idx",  -1))
        pi1 = int(data.get("play_idx1", -1))
        pi2 = int(data.get("play_idx2", -1))
        if si < 0 or si >= len(state["stacks"]):
            err = "Invalid pile."
        else:
            plays = state["stacks"][si]["plays"]
            if not (0 <= pi1 < len(plays) and 0 <= pi2 < len(plays) and pi1 != pi2):
                err = "Invalid card indices."
            else:
                plays[pi1], plays[pi2] = plays[pi2], plays[pi1]
                msg = f"Swapped arrival order in pile {si + 1}!"

    elif action == "deact_act":
        si = int(data.get("stack_idx", -1))
        pi = int(data.get("play_idx",  -1))
        if si < 0 or si >= len(state["stacks"]):
            err = "Invalid pile."
        else:
            plays = state["stacks"][si]["plays"]
            if pi < 0 or pi >= len(plays):
                err = "Invalid card."
            else:
                tp = plays[pi]
                if tp["player_idx"] == actor:
                    err = "Cannot target your own card."
                elif tp["used_action"]:
                    err = "Target must be a card played WITHOUT action."
                else:
                    tp["modifier_active"] = not tp["modifier_active"]
                    tname = state["players"][tp["player_idx"]]["name"]
                    ct    = _card_type(tp["card_num"])
                    if ct == "bonus":
                        msg = f"Deactivated {tname}'s Card {tp['card_num']} bonus!"
                    else:
                        msg = f"Activated {tname}'s Card {tp['card_num']} debuff!"

    if err:
        return jsonify({"error": err, **_enrich(state)})

    if msg:
        _log(state, actor, f"{state['players'][actor]['name']}: {msg}")
    state["action_message"] = msg
    _advance_card_turn(state)
    _process_ai(state)
    _save(state)
    return jsonify(_enrich(state))

@app.route("/api/cancel_action", methods=["POST"])
def api_cancel_action():
    state = _gs()
    actor = state["action_ctx"].get("actor", state["current_player_idx"])
    _log(state, actor, f"{state['players'][actor]['name']}: skipped action")
    state["action_message"] = "Action skipped."
    _advance_card_turn(state)
    _process_ai(state)
    _save(state)
    return jsonify(_enrich(state))

@app.route("/api/set_show_target", methods=["POST"])
def api_set_show_target():
    data  = request.json or {}
    state = _gs()
    if state["phase"] != "date_phase" or not state["date_show_info"]:
        return jsonify({"error": "No show modifier active"}), 400
    state["date_show_info"]["reveal_to"] = int(data.get("target_player", -1))
    _save(state)
    return jsonify(_enrich(state))

@app.route("/api/set_peek_target", methods=["POST"])
def api_set_peek_target():
    data  = request.json or {}
    state = _gs()
    if state["phase"] != "date_phase" or not state["date_peek_info"]:
        return jsonify({"error": "No peek modifier active"}), 400
    state["date_peek_info"]["peek_target"] = int(data.get("target_player", -1))
    _save(state)
    return jsonify(_enrich(state))

@app.route("/api/submit_move", methods=["POST"])
def api_submit_move():
    data  = request.json or {}
    state = _gs()
    if state["phase"] != "date_phase":
        return jsonify({"error": "Not in date phase"}), 400

    di    = state["date_phase_idx"]
    stack = state["stacks"][di]
    order = state["date_submission_order"]
    count = state["date_submitted_count"]

    if count >= len(order):
        return jsonify({"error": "All moves already submitted"}), 400

    expected = order[count]
    pidx     = int(data.get("player_idx", -1))

    my_pidx = _my_pidx()
    if my_pidx != -1 and my_pidx != pidx:
        return jsonify({"error": "Not your turn to submit"}), 403

    if state.get("game_mode") == "local" and state.get("player_types", [])[pidx:pidx+1] == ["ai"]:
        return jsonify({"error": "AI players move automatically"}), 400

    if pidx != expected:
        return jsonify({
            "error": f"It is {state['players'][expected]['name']}'s turn to submit"
        }), 400

    move = str(data.get("move", "")).upper()
    if move not in ("MM", "PS"):
        return jsonify({"error": "Move must be MM or PS"}), 400

    stack["date_moves"][str(pidx)] = move
    state["date_submitted_count"] += 1

    if state["date_submitted_count"] >= 3:
        _finish_date(state, di)
    _process_ai(state)

    _save(state)
    return jsonify(_enrich(state))

@app.route("/api/reset", methods=["POST"])
def api_reset():
    state = _gs()
    # preserve room identity and player config for online rooms
    game_mode    = state.get("game_mode", "local")
    player_types = state.get("player_types", ["human", "human", "human"])
    player_joined = state.get("player_joined", [True, True, True])
    players      = state.get("players", [])

    new_s = _new_state(state["room_code"])
    new_s["game_mode"]     = game_mode
    new_s["player_types"]  = player_types
    new_s["player_joined"] = player_joined
    new_s["players"]       = players
    # Clear card state on players
    for p in new_s["players"]:
        p["cards_played_to"] = []

    if game_mode == "online":
        # Go back to lobby so host can restart; preserve names
        new_s["phase"] = "lobby"
    else:
        new_s["phase"] = "select"

    _rooms[new_s["room_code"]] = new_s
    session.modified = True
    return jsonify(_enrich(new_s))

if __name__ == "__main__":
    app.run(debug=True, port=5000)
