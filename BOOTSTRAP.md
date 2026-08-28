# Bootstrap — run once, in your own terminal (PowerShell or WSL)

Target layout: this folder (`E:\TikTok Hackaton 2026`) **becomes** the repo root, so
`CLAUDE.md` and `.claude/` sit at the top level where Claude Code will find them.

## 1. Pull the organiser repo into this folder

The folder is not empty, so a plain `git clone` will refuse. Attach a remote instead:

```powershell
cd "E:\TikTok Hackaton 2026"
git init
git remote add origin https://github.com/TechJam2026/techjam-conversational-search.git
git fetch origin
git checkout -t origin/main
```

`git init` makes this folder a repo. `remote add` + `fetch` downloads the history without
touching your existing files. `checkout -t` lays the starter code down beside them and sets
up tracking so `git pull` works later.

If the checkout complains that a file would be overwritten, the starter ships its own copy
of that file. Rename yours (`mv CLAUDE.md CLAUDE.mine.md`), redo the checkout, then merge.

## 2. Get the catalog (no `gh` needed)

Only two of the three release assets matter. `techjam-participant-kit.zip` is the same catalog
bundled with the starter code you already have from git — skip it.

**PowerShell:**

```powershell
cd "E:\TikTok Hackaton 2026"
$base = "https://github.com/TechJam2026/techjam-conversational-search/releases/download/participant-kit"
curl.exe -L -o catalog.jsonl.gz "$base/catalog.jsonl.gz"
curl.exe -L -o SHA256SUMS       "$base/SHA256SUMS"
```

Use `curl.exe`, not `curl`. In PowerShell bare `curl` is an alias for `Invoke-WebRequest`, which
does not understand `-L -o` and will fail confusingly. `-L` follows redirects — GitHub redirects
release downloads to a CDN, so without it you get a short HTML stub instead of 19 MB of data.

**Verify before decompressing:**

```powershell
$want = (Get-Content SHA256SUMS | Select-String 'catalog.jsonl.gz').ToString().Split()[0]
$got  = (Get-FileHash catalog.jsonl.gz -Algorithm SHA256).Hash.ToLower()
if ($want -eq $got) { "OK" } else { "MISMATCH"; $want; $got }
```

The catalog is the fixed universe. A truncated download still decompresses to something that
looks like a catalog, and every score you measure this weekend would be quietly wrong.

**Decompress** (Windows has no `gzip`, so use the Python you already have):

```powershell
python -c "import gzip,shutil; shutil.copyfileobj(gzip.open('catalog.jsonl.gz','rb'), open('data/catalog.jsonl','wb'))"
```

Sanity check — expect 50000:

```powershell
python -c "print(sum(1 for _ in open('data/catalog.jsonl',encoding='utf-8')))"
```

**In WSL or Git Bash instead?** The plain Unix tools all work:

```bash
base="https://github.com/TechJam2026/techjam-conversational-search/releases/download/participant-kit"
curl -L -O "$base/catalog.jsonl.gz" && curl -L -O "$base/SHA256SUMS"
sha256sum -c SHA256SUMS
gzip -dk catalog.jsonl.gz && mv catalog.jsonl data/catalog.jsonl
```

**No terminal downloads at all?** Open the release page in a browser, click the two assets, and
move them into the repo folder. Then run the verify and decompress steps above.
https://github.com/TechJam2026/techjam-conversational-search/releases/tag/participant-kit

## 3. Environment

```powershell
python3 -m venv .venv
.\.venv\Scripts\Activate.ps1        # WSL/macOS: source .venv/bin/activate
python -m pip install -U pip
pip install numpy faiss-cpu rank-bm25 scikit-learn sentence-transformers
```

The starter is pure stdlib, so the venv exists for what *you* add. Everything must run
in-process and in-memory — `faiss-cpu`, never a vector DB server.

## 4. Record the baseline before writing a single line of code

```powershell
python3 -m evaluator.local_evaluator
```

Writes `results.json`. Published reference: HR@10 0.125, MRR 0.068034, MTTC 9.81
(TechnicalScore ~ 0.107). **Confirm you reproduce it locally.** If your run disagrees with
`docs/baseline_results.json`, your data or environment is wrong and nothing you measure
afterwards means anything. Copy the number into `eval/RESULTS.md` as run zero.

## 5. Protect against the disqualifying mistakes

```powershell
git checkout -b dev
echo ".venv/`n.env`ndata/index/`ndata/catalog.jsonl`n__pycache__/`nresults.json" >> .gitignore
git add .gitignore CLAUDE.md BOOTSTRAP.md .claude
git commit -m "chore: agent workspace + bootstrap"
```

Never commit `.env` or a key — secrets anywhere in git history are disqualifying, including
in a commit you later reverted. Keep the repo **private** until you flip it public at
submission.

## 6. First session with Claude Code

```powershell
cd "E:\TikTok Hackaton 2026"
claude
```

Then, in order:

```
/spec                    # spec-oracle answers the six open questions from evaluator source
/pillars                 # audit what the starter already has vs the four required pillars
/eval                    # referee records the baseline in eval/RESULTS.md
```

Run `/spec` first. Question 4 — whether `ask_attribute` changes what the simulated customer
reveals — decides whether attribute selection is the highest-leverage code in the repo or
decorative. Do not design the dialogue policy before you know.

## 7. Then parallelise

Disjoint file ownership means these run at the same time without collisions:

```
> Use the retrieval agent to build the BM25 + dense + RRF pipeline in src/retrieval.py.
> Use the dialog agent to implement slot accumulation with override erasure in src/state.py.
> Use the orchestrator agent to design the turn trace schema in src/trace.py.
```

You integrate their work in `src/agent.py` yourself — that file has one owner, always — then
run `/eval` once on the combined state.

## Daily rhythm

| When | Do |
|---|---|
| Start of session | `/eval` to re-establish where you are |
| After each change | `make eval-fast`, then `/eval` to get a verdict |
| Every kept change | commit with the score in the message |
| End of day | `scribe` updates the README from what actually shipped |
| 31 Aug 18:00 | `/ship` |
