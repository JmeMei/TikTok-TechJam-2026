# Demo video script — Shopping Copilot, Track 4

**Target length: 3:00–3:30.** Backend walkthrough, no UI.
`docs/final_evaluation_faq.md` §7: a UI is optional and **not separately assessed**, but the demo
**must show at least one complete multi-turn session**. Section 3 below is therefore the one
non-negotiable part.

**Record:** terminal (large font, dark theme) + voiceover. Have every command already run once
so nothing is slow or fails live. Keep `eval/RESULTS.md` and `README.md` open in tabs.

---

## 0:00–0:25 — The problem, and the number

> "Track 4 gives you a simulated customer with a hidden target product, a 50,000-item Amazon
> clothing catalog, and ten turns to surface it. The provided baseline scores 0.107.
> Ours scores **0.815** — with no LLM, no API key, and no network access at inference."

**Screen:** `README.md` results table.

> "And the reason the baseline scores 0.107 is one line of code."

---

## 0:25–1:00 — The insight the whole system is built on

**Screen:** `evaluator/local_evaluator.py`, scroll to `customer_reply` (~line 166).

> "The baseline sends `ask_attribute: null` every turn. The simulator answers a null attribute
> with 'ask me about one specific attribute' — and reveals nothing. So the baseline re-runs the
> same query ten times, against a customer who is perfectly willing to answer questions it
> never asks.
>
> This isn't a search-relevance problem with a chat wrapper. It's an **information-acquisition**
> problem."

**Screen:** highlight the matching line — `attribute == "other" or classify_constraint(value) == attribute`.

> "The left side short-circuits. `"other"` is a legal wildcard that matches *any* undisclosed
> constraint — and the customer only holds four. Two questions drain them completely.
> That single change took us from 0.107 to 0.750."

---

## 1:00–2:00 — Three scenarios, one command *(the required section)*

**Screen:** run it live. This prints the literal conversation, not the decision trace.

```powershell
python scripts\dump_dialogue.py --sample public_0009,public_0004,public_0011
```

```
public_0009  [buying]
CUSTOMER  Handbags & Wallets Totes. A key requirement is: leather.
AGENT     So far I have material: leather. I'm mostly seeing black, blue or
          pink. Any preference on colour?
CUSTOMER  For that, what matters is: Soft Washed PU Leather...; Imported.
  >>> FOUND IT at rank 1, turn 2

public_0004  [intent_override]
AGENT     So far I have style: Long torso camisole...; material: polyester...
CUSTOMER  Actually, ignore my earlier preference. What I need is: polyester.
AGENT     So far I have material: polyester, Polyester,Cotton,Spandex.
  >>> FOUND IT at rank 1, turn 3
```

Narrate as a progression — three different customers, three hidden products:

> "**Buying.** The customer names one requirement. The agent reads the candidates that
> survived, works out that colour separates them best, and asks about colour — offering
> black, blue or pink, which are the actual colours in that result set, not a canned list.
> Rank 1 on turn 2.
>
> **Intent override.** Watch the summary line. Turn 2 it says *style: long torso camisole,
> material: polyester*. The customer then retracts the camisole detail — and on turn 3 the
> style slot is **gone**. We erase contradicted constraints instead of accumulating them.
> Rank 1.
>
> **Browsing.** Starts with nothing at all. One material question, and rank 1 by turn 2."

**The point to land — and it is the counter-intuitive one:**

> "The question is chosen by expected information gain over the surviving candidates:
> coverage times entropy, recomputed every turn. But look at what goes on the wire."

**Screen:** `python scripts\dump_trace.py --sample public_0009 --print`

> "`ask_attribute` is `other`. The simulator reads only that field, never the prose — and
> `other` is a wildcard that returns two constraints a turn where a specific bucket returns
> less. Asking specifically as the *structured* field costs about 0.05. Asking specifically
> in *prose* costs nothing. So the specificity goes where the customer looks and the yield
> goes where the evaluator looks."

---

## 2:00–2:40 — Why erasure matters, and the finding we're proudest of

> "Before erasure, this is what the ranker was reading on override turns."

**Screen (slide or terminal):**

```
'Dresses. color: pink | 100% polyester | cotton'
   ^^^^^^^^^^^^ retracted decoy, leading      ^^^^^^ the real constraint, last
```

> "BM25 tolerated that — it dilutes one bad term across dozens. But a cross-encoder concentrates
> on it and confidently ranks the wrong intent. Override sessions were scoring 0.596 against
> 0.760 for plain keyword search. Erasing the decoy and leading with the replacement fixed it."

> "Then the finding that surprised us most."

**Screen:** `eval/RESULTS.md`, run 7 table.

> "We assumed our reranker was too small. A **12.6x larger** model bought **+0.004**.
> Then we found the actual bug: we were showing the model a product's title, price and first two
> features — but **not its `details` field** — while the simulator draws its constraints *from*
> that field. We were asking the model to match text the document didn't contain.
>
> Adding one field: **+0.037**. Nine times the gain of the bigger model. In fact the cheap 22M
> model on a correct input beat the 278M model on a broken one."

---

## 2:40–3:10 — Rigour, and honest limits

**Screen:** `eval/RESULTS.md`, scroll the ledger.

> "Eleven runs, every one with four scenario breakdowns — **including five negative results we
> kept**. Our dense retrieval track loses; it ships disabled and documented. Deterministic
> constraint matching should have been free and perfect — it lost by 0.044, because
> '100% cotton' is shared by dozens of near-identical products.
>
> And the one we are least comfortable with: adding specialised questions first measured a
> 0.05 **loss**, and we spent hours blaming the question policy. It was three unrelated bugs —
> a duplicated ranker query, a query cleanup that shifted every BM25 rank, and a one-off
> customer refusal we treated as permanent. Moving to GPU cut a 32-minute experiment to 40
> seconds, and the bisect took twenty minutes."
>
> And we report that our dialogue gain has a paired 95% confidence interval of
> minus-0.014 to plus-0.047 — **not significant at n=200**. We'd rather say that than claim it."

**Screen:** degradation table in `README.md`.

> "It degrades instead of failing: 0.815 with full setup, 0.787 on a plain clone, 0.761 with the
> models deleted entirely. We verified that by deleting them."

---

## 3:10–3:30 — Close

**Screen:** run the real thing.

```bash
python -m evaluator.local_evaluator
```

> "Unmodified official evaluator. **0.815121.** Zero dollars, zero tokens, no network.
>
> We know our ceiling too — a perfect reranker over the candidates we already retrieve would
> score 0.944, which tells us retrieval is basically solved and the remaining 0.13 is pure rank
> quality. That's a listwise ranking problem, and it's exactly where we'd go next."

---

## Shot checklist

- [ ] Terminal font ≥ 18pt, dark theme, window wide enough that trace lines don't wrap
- [ ] All commands pre-run once (models loaded, FTS5 index warm)
- [ ] All three sessions legible, `public_0004`'s vanishing `style:` slot especially
- [ ] `$env:PYTHONIOENCODING = "utf-8"` set — some catalog text is non-ASCII and mangles without it
- [ ] `eval/RESULTS.md` open in a tab
- [ ] Final `recommended_technical_score: 0.815121` clearly on screen
- [ ] Audio checked — no clipping
- [ ] Uploaded to YouTube, **set to Public** (not Unlisted), link pasted into Devpost

## Cuts if you run long

Drop 2:40–3:10 (rigour section) first — it's the most compressible. If you need more, cut the
buying and browsing sessions and keep only `public_0004`:

```powershell
python scripts\dump_dialogue.py --sample public_0004
```

Never cut section 3 entirely; one complete multi-turn session is the one explicitly required
deliverable (`docs/final_evaluation_faq.md` §7).
