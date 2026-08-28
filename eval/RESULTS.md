# Score ledger

Every eval run gets a row. Never claim an improvement that is not written here.

`TechnicalScore = 0.50 x HR@10 + 0.30 x MRR + 0.20 x Efficiency`

| # | Date | Commit | Change | TS | HR@10 | MRR | MTTC | Eff | Buying HR | Browsing HR | Override HR | Boundary HR | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 2026-08-28 | 3407835 | Untouched BM25 starter (baseline) | **0.10671** | 0.1250 | 0.0680 | 9.81 | 0.119 | 0.2375 | 0.0250 | 0.1333 | 0.0000 | BASELINE |

## Run 0 notes — where the losses are

200 sessions: buying 80 / browsing 80 / intent_override 30 / boundary 10.
25 of 200 sessions hit. 175 miss, and each miss is worth 0.55 of a session's score.

| Scenario | n | HR@10 | Hits | Why |
|---|---|---|---|---|
| buying | 80 | 0.2375 | 19 | Opening message contains `hard_constraints[0]` verbatim, so BM25 has real signal |
| intent_override | 30 | 0.1333 | 4 | Gets a constraint only at the turn-3/4 override message |
| browsing | 80 | 0.0250 | 2 | Opening message is `"I'm looking for {category}, but I'm still exploring."` — **zero constraints** |
| boundary | 10 | 0.0000 | 0 | Same starvation plus a wasted first question |

**Root cause is one line.** `starter/agent.py:99` sends `"ask_attribute": None` every turn, and
`customer_reply` (L170-171) answers a null attribute with "Ask me about one specific attribute"
and reveals nothing. The starter therefore reruns the same query ten times on whatever the
opening message happened to contain. Browsing openings contain nothing, hence 0.025.

**Biggest single prize:** the 78 missed browsing sessions. Converting them is worth
78 x 0.55 / 200 = **+0.21 TechnicalScore** at full conversion; even half is +0.10.
