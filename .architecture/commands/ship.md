---
description: Pre-submission checklist - run before the 31 Aug 18:00 SGT freeze
---
Run the full submission gate. Report PASS/FAIL per item, no code changes unless a FAIL is
trivially fixable.

Code
- [ ] `make check` clean (lint, types, unit tests)
- [ ] `make eval` full run recorded in `eval/RESULTS.md` with commit hash
- [ ] Zero crashed sessions
- [ ] Fresh-clone reproduction works with **no API key set** (the non-LLM path must score)
- [ ] `respond()` and `reset()` cannot raise - verify the try/except wrapping
- [ ] Turn 10 always returns 10 unique valid parent_asins
- [ ] No external vector DB, no server, no fixed port; evaluator imports us locally
- [ ] `data/catalog.jsonl` unmodified - verify against the published SHA256SUMS
- [ ] `.env` gitignored; `git log -p | grep -iE "api[_-]?key|secret|sk-"` returns nothing

Deliverables (delegate to `scribe`)
- [ ] README: overview, setup, reproduction, limitations, team contributions
- [ ] Devpost: problem fit, dev tools, APIs, libraries/frameworks, datasets/assets, plus
      model choice, estimated cost, token usage, latency
- [ ] Demo video script ready; video public on YouTube and linked in Devpost
- [ ] Architecture writeup maps to all four pillars with a real trace excerpt
- [ ] Repo flipped public

Finish with the single biggest remaining risk and the time cost of fixing it.
