# THREE'S A CROWD

**A card game of romance, rivalry, and reading the room**
*3 players · Ages 12+ · 20–40 minutes*

---

## Overview

Three's a Crowd is a strategic card game for exactly three players. Over two phases — a **card-playing phase** and a **date phase** — you compete to pair up on six romantic encounters. Land in a cozy pair and you score big. Show up as the third wheel and you pay the price. The key is knowing when to make your move, and whether your rivals will do the same.

---

## Components

- **18 Cards** — three identical sets of cards numbered 1–6, one set per player
- Players are identified by color: **Red**, **Blue**, and **Green**

Each card has two modes of play:

| Card | Type   | Action *(triggers immediately when used)* | Modifier *(passive effect when action is skipped)* |
|:----:|--------|------------------------------------------|---------------------------------------------------|
| 1 | Bonus  | Switch any two date piles | **+1 point** at end of game |
| 2 | Bonus  | Swap the arrival order of any two cards in a pile | **Multiply your score × 2** this date |
| 3 | Bonus  | Deactivate an opponent's active bonus, or activate an opponent's avoided debuff | **Peek** at one player's submitted move before choosing your own |
| 4 | Debuff | Switch any two date piles | **−1 point** at end of game |
| 5 | Debuff | Swap the arrival order of any two cards in a pile | **Divide your score ÷ 2** (round down) this date |
| 6 | Debuff | Deactivate an opponent's active bonus, or activate an opponent's avoided debuff | **Reveal** your move to one player before they decide |

---

## Card-Playing Phase

Players take turns placing cards in **snake draft order**: 1 → 2 → 3 → 3 → 2 → 1, repeating until all 18 cards are placed. Each player takes exactly 6 turns.

### On Your Turn

1. **Select a card** from your unplayed hand (1–6).
2. **Choose a destination:**
   - Place it on an existing pile you have not yet played to, **or**
   - Start a **new pile** at the far left or far right of the row. The game always ends with exactly 6 piles.
3. **Decide how to play it:**
   - **Use the Action** — the action triggers immediately. Bonus card: you forfeit the modifier. Debuff card: the debuff is activated.
   - **Skip the Action** — nothing happens now. Bonus card: you keep the modifier. Debuff card: you avoid the debuff.

> **Arrival Order:** Cards are stacked in the order they are placed. The first card in a pile arrives 1st, the second arrives 2nd, the third arrives 3rd. This order determines who is the Third Wheel on each date.

---

## Date Phase

Once all 18 cards are placed, the six piles resolve as **dates**, left to right.

### The Third Wheel

Each date has exactly one Third Wheel, determined by arrival position:

| Piles | Third Wheel is… |
|-------|-----------------|
| Piles 1–2 | 3rd to arrive |
| Piles 3–4 | 2nd to arrive |
| Piles 5–6 | 1st to arrive |

The remaining two players are the **Normals**.

### Making Your Move

Each player simultaneously and secretly chooses one of two options:

- **MM** — *Make a Move* — bold, assertive, romantic
- **PS** — *Play it Safe* — cautious, reserved, hands-off

All choices are then revealed at once.

### Scoring

*TW = Third Wheel · N1 & N2 = Normals (order between normals is interchangeable)*

| TW | N1 | N2 | TW | N1 | N2 |
|:--:|:--:|:--:|:--:|:--:|:--:|
| PS | PS | PS | 0 | +1 | +1 |
| PS | MM | PS | 0 | −1 | +1 |
| PS | PS | MM | 0 | +1 | −1 |
| PS | MM | MM | 0 | +2 | +2 |
| MM | PS | PS | −2 | +1 | +1 |
| MM | MM | PS | +2 | +1 | 0 |
| MM | PS | MM | +2 | 0 | +1 |
| MM | MM | MM | −2 | +1 | +1 |

**Key dynamics:**
- The Third Wheel only scores by making a move while *exactly one* Normal reciprocates.
- A lone Normal who makes a move is penalized.
- When both Normals make a move, they each score +2 — but the Third Wheel cannot benefit regardless.

---

## Active Modifiers

Before tallying scores for a date, apply any active modifier cards found in that pile:

| Card | Condition for Activation | Effect |
|------|--------------------------|--------|
| 1 — Bonus | Action was **skipped** | **+1** added to player's final total at game end |
| 2 — Bonus | Action was **skipped** | Multiply **that player's** date score × 2 |
| 3 — Bonus | Action was **skipped** | Player sees one already-submitted move before choosing |
| 4 — Debuff | Action was **used** | **−1** subtracted from player's final total at game end |
| 5 — Debuff | Action was **used** | Divide **that player's** date score ÷ 2 (round down) |
| 6 — Debuff | Action was **used** | Player must reveal their move to one opponent before that opponent chooses |

Cards 2 and 5 affect only the card holder's score. A player who scores +2 with an active Card 2 scores +4. A player who scores −2 with an active Card 5 scores −1.

### Cards 3 & 6 — Submission Order

Card modifiers that affect information change the **order in which players submit** their MM/PS choice:

1. The player with an **active Card 6** (Show debuff) submits **first** and must reveal their choice to one player of their choosing before that player submits.
2. The player with an **active Card 3** (Peek bonus) submits **last** and may look at one already-submitted choice before deciding.
3. All other players submit in arrival order.

### Cards 3 & 6 — Deactivate / Activate

When Cards 3 or 6 are played **with** their action, target any opponent's card played **without** its action:
- **Target a Bonus card** → **deactivate** it. The opponent loses that modifier.
- **Target a Debuff card** → **activate** it. The opponent now suffers the debuff despite having skipped it.

---

## Final Scoring

After all six dates resolve:

1. Sum each player's per-date scores.
2. Apply Card 1 bonuses: **+1** for each active Card 1.
3. Apply Card 4 debuffs: **−1** for each active Card 4.

The player with the **highest total wins**. Ties are broken by the player who scored highest on the latest date.

---

## Strategy Notes

- **The Third Wheel gamble:** Making a move as the Third Wheel is high-risk. You win big if exactly one Normal reciprocates, but lose if both do or neither does.
- **Card 2 timing:** Best saved for a date where you expect to score positively — doubling a loss makes it worse.
- **Card 5 as a weapon:** Even if you place Card 5 without triggering the debuff, an opponent's Card 3 or 6 action can activate it against you.
- **Snake draft pressure:** Picking last in a round means you pick twice in a row. Use this to respond to what others have built.
- **Pile positioning matters:** The Third Wheel rule rotates across piles. Placing your card 1st in Pile 5 makes you the Third Wheel there — choose wisely.

---

## Digital Version

```bash
pip install flask
python3 app.py
```

Open `http://localhost:5000`. Supports local play (pass the device between players), online multiplayer via room codes, and AI opponents.

To play with remote players:
```bash
ngrok http 5000
```
