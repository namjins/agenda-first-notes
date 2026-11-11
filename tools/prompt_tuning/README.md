# Prompt tuning (offline, maintainer-only)

Improves the **extraction instructions** in `backend/agenda_first_notes/prompts.py` (`EXTRACT_INSTRUCTIONS`) with
the GEPA optimizer, against a local Ollama model. The output is plain instruction text that a human pastes into
`prompts.py`. Nothing here is part of the app: it is a separate uv project, the app never imports it, and CI never
installs it. `backend/pyproject.toml` is untouched (the backend is an editable path dependency of this project).

## How it works

- **Candidate** = the instruction text after the `TRANSCRIPT>>>` line of `EXTRACT_INSTRUCTIONS` (the part that says
  what to return and how). The agenda/transcript framing before it, the system prompt (`CORE_POLICY` with the twelve
  rules) and the workspace profiles stay fixed, so a candidate cannot drop a placeholder or weaken a rule.
- **Evaluation is the app's own path.** For each meeting, `prompt_tuning/app.py` creates a meeting in a throwaway
  data directory (system temp, outside the repo, deleted at exit), sets and approves the agenda, adds the transcript
  and calls `services.process_meeting` with the app's `OllamaProvider`. A meeting without `agenda.txt` takes the
  app's no-agenda path instead (CLAUDE.md rule 2): transcript first, then `pipeline.propose_agenda` (the same call as
  the "Propose agenda from transcript" button, same provider and settings), and the proposed headings are approved
  unchanged, as a user clicking Approve would. Processing and rendering then run as usual. Only
  `EXTRACT_INSTRUCTIONS` is tuned; `AGENDA_PROPOSAL_INSTRUCTIONS` stays fixed (a possible later tuning target). A
  proposal that fails or returns no headings scores 0 with the error as feedback. That runs segmentation,
  `pipeline.extract_segment` (with `prompts.EXTRACT_INSTRUCTIONS` swapped for the candidate), quote validation,
  dedupe, the deterministic checks, and `services.render_meeting`. The rendered Markdown is scored. The app's
  `think=False` handling comes with the provider. Review rounds default to 0 (`--review-rounds` to change), because
  only extraction is being tuned and each round costs another model call.
- **Member names.** A meeting uses the nearest `members.csv` (or `Members.csv`) in its directory or an ancestor, up
  to and including its `--data` root; no list, no name flow. Each list is read once (`names.parse_members_csv`) and
  imported with `services.import_members` into the shared member store of the throwaway data directory (never the
  app's own data), replaced only when the next meeting uses a different list. After the agenda is approved and the
  transcript saved, the tool plays the user: it fetches `services.name_suggestions` and, for each gold name whose
  heard text is suggested with the gold member among its candidates, calls `services.confirm_name` for that member.
  Nothing else is confirmed. `process_meeting` then reads the confirmations as in the app.
- **Metric** (`prompt_tuning/metric.py`, unit-tested in `tests/test_metric.py`): `recall / (1 + penalties)`, where
  recall is the share of expected facts found under the right agenda item (anywhere, for `"item": null`) plus
  `topics` covered (dividing rather than subtracting keeps
  bad outputs ordered instead of all clamped to 0, which GEPA needs to make progress). Penalties: fact under the wrong item (0.10), false "Not discussed."/"No discussion." on an
  item with expected facts (0.15), a note or "Discussion occurred…" under a `not_discussed` item (0.10), any forbidden
  match (0.20), wrong `[Follow-up]` tag (0.05), HIGH deterministic findings of category name, number, overconfident,
  wrong_placement or unsupported (0.05 each, max 0.30), notes that retell the conversation ("A participant…",
  "Discussion indicated…", "It was noted…": 0.02 each, max 0.10), proposed headings over `max_headings` (0.02 per
  extra heading, max 0.10), detail level off the reference notes (0.05, see `reference_notes`). No rendered notes at all scores 0. Repeated headings are named `Parent > Updates` in feedback. The feedback lines are concrete ("Expected 'reimburs' + 'dec' under 'Reimbursement guidelines' but it
  appeared under 'Activity coordination'.", "False 'Not discussed.' for 'Ward history'…", "HIGH name finding: …").
  Gold `names` count towards recall like expected facts: the member's full or preferred name ("First Last" or in
  full) must appear in at least one rendered note, else "Confirmed name 'Robert Ross' for 'bob boss' never used in
  the notes." A gold name the app did not suggest (so the simulated user could not confirm it) is left out of the
  score: no instruction can fix it. That is reported instead, as counts only, in `score` output and the tune
  report: how often the gold member was the top candidate and how often it was suggested at all. **For data outside
  `data/synthetic/` the name lines never contain a member name**, in feedback (which also goes to the local
  reflection model) or in reports: they say "gold name #2". (The rendered notes shown to the local reflection model
  are the meeting's own notes, names included, as before.)
- **Optimizer route: the `gepa` library's custom-adapter API** (`gepa==0.1.4`, the version dspy 3.4.1 ships with),
  not `dspy.GEPA`. `dspy.GEPA` always builds its own `DspyAdapter`, which runs a `dspy.Module` through a dspy LM and
  builds the reflective dataset from dspy predictor traces; the app's extraction path makes no dspy predictor calls,
  so there would be no traces, and its reflection LM must be a `dspy.LM` (litellm/OpenAI clients). The gepa adapter
  lets `evaluate` run the app itself, and the reflection LM is a plain callable that goes through the same
  `OllamaProvider`. dspy is installed (as approved) and imported only to switch its cache off.
- **Reflection prompt** (`REFLECTION_TEMPLATE` in `cli.py`) is short; each reflective example carries only the agenda
  (≤1,500 chars), the rendered notes (≤2,500 chars), the score and ≤15 feedback lines, three meetings per step.
- **Guards**: a candidate that contains any 8-word run from a loaded transcript scores 0 (instructions only, no
  few-shot material). A candidate that contains a member's name from any loaded list ("first last", or a surname of
  4+ letters that `prompts.py` does not already use) scores 0 the same way; the feedback never says which name.
  After tuning, capitalised words that occur in the loaded meetings but not in `prompts.py` (possible names) are
  listed in the report for review; with real data, words that are member names are only counted, not shown.
- **Several data roots**: `--data data/synthetic ../../sample_meetings` loads both; meeting names are then prefixed by
  the root's directory name (`synthetic/wc_01_person_role`, `sample_meetings/Bishopric/<date>`).
- **Split**: every third meeting (sorted by name over all roots) is the validation set (4 of the 13 synthetic
  meetings); the rest are training. Adding meetings can shift it; it stays the same for the same set.

## Privacy

Everything runs against Ollama on `127.0.0.1`; the app's `OllamaProvider` refuses non-loopback hosts and ignores
proxy settings.

Source audit of what is installed (`dspy 3.4.1`, `gepa 0.1.4`, `openai`, the backend's dependencies), Nov 2026:

- **litellm is not installed.** dspy 3.4.1 lists it as a dependency, but `pyproject.toml` overrides it away
  (`override-dependencies = ["litellm; sys_platform == 'never'"]`); `import dspy` works without it, and nothing here
  calls a dspy or litellm LM. If it ever comes back, `privacy()` sets `litellm.telemetry = False`.
- **dspy**: no telemetry/analytics/sentry/posthog code. Network code exists only in clients this tool never calls
  (Databricks, ColBERT, image/audio URL loaders, vendored `lm15` provider auth). One default fetch:
  `dspy.clients.model_metadata` downloads litellm's model-cost map from `raw.githubusercontent.com` the first time a
  dspy LM computes cost/capabilities; `privacy()` sets `LITELLM_LOCAL_MODEL_COST_MAP=True` to keep it on the bundled
  snapshot (and no dspy LM is ever created). The dspy disk and memory caches are disabled with
  `dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)`; importing dspy creates no cache directory.
- **gepa**: no telemetry. W&B and MLflow logging exist but are opt-in (`use_wandb`/`use_mlflow`, passed `False`);
  litellm is imported only when the reflection LM is a model-name string (here it is a callable).
- **openai** (dspy dependency): an API client, no telemetry; never instantiated here.
- **opentelemetry-api** comes with FastAPI (the app's own dependency): API only, no SDK or exporter, so a no-op.
- `--run-dir` (GEPA resume state) and the report hold scores and feedback derived from the meetings. For data outside
  `data/synthetic/` (including the git-ignored `sample_meetings/`), `tune` refuses to write `--out`, the report or
  `--run-dir` inside the repository; with several `--data` roots this applies when any one of them is not synthetic.
- Member lists are read only at run time, kept in memory and in the throwaway data directory's member store
  (`<tmp>/members.sqlite3`, deleted at exit), and never printed. `data/synthetic/members.csv` is invented (SYNTHETIC
  column) and is the one member CSV the root `.gitignore` lets through.

### Proof: run with all non-loopback network blocked (macOS)

`loopback-only.sb` allows everything except outbound network, which may reach only `localhost` (127.0.0.1, ::1).
DNS is blocked too. Check the block, then run the tool inside it:

```sh
cd tools/prompt_tuning
sandbox-exec -f loopback-only.sb curl -sS --max-time 5 https://pypi.org/simple/ -o /dev/null   # fails (blocked)
sandbox-exec -f loopback-only.sb curl -sS http://127.0.0.1:11434/api/tags -o /dev/null          # works (Ollama)
sandbox-exec -f loopback-only.sb uv run --offline --frozen tune --data data/synthetic \
    --model qwen3.6:35b-a3b --max-metric-calls 30 --out out/smoke.txt
```

`uv sync` needs the network once, outside the sandbox; after that every command works with `--offline --frozen`
inside it.

## Run

```sh
cd tools/prompt_tuning
uv sync                                    # once (network); installs dspy 3.4.1 + gepa 0.1.4, no litellm

# Score the current prompts.py (baseline), then a tuned file, on every meeting:
uv run score --data data/synthetic --model qwen3.6:35b-a3b
uv run score --data data/synthetic --model qwen3.6:35b-a3b --instructions out/best.txt

# Tune (light = 60 meeting runs, medium = 200; --max-metric-calls overrides):
uv run tune --data data/synthetic --model qwen3.5:27b --budget medium --out out/best.txt
```

Overnight run over synthetic and real meetings with the name flow (resumable with the same `--run-dir`; real data,
so `--out` and `--run-dir` live outside the repo; `caffeinate -i` keeps the Mac awake):

```sh
cd tools/prompt_tuning && mkdir -p ~/afn-tuning
caffeinate -i sandbox-exec -f loopback-only.sb uv run --offline --frozen tune \
    --data data/synthetic ../../sample_meetings --model qwen3.5:27b --max-metric-calls 100 \
    --out ~/afn-tuning/best-27b-names.txt --run-dir ~/afn-tuning/run-27b-names \
    2>&1 | tee ~/afn-tuning/tune-27b-names.log
```

Timing on the M2 Ultra: qwen3.6:35b-a3b scores all 13 synthetic meetings in about 7.5 min (14–64 s each, roughly
3.5 s per 1,000 transcript characters). qwen3.5:27b took 115 s for the smallest real meeting (12k characters), about
9–10 s per 1,000 characters. The 24 meetings of `data/synthetic` + `sample_meetings` hold about 590k characters, so
one meeting run averages about 4 min with 3.5:27b (the 75k-character meeting about 12 min), and one reflection call
about 1–2 min, roughly one per 7–8 meeting runs. 100 metric calls is therefore about 7 h, plus up to one validation
pass (8 meetings, ~30 min) that GEPA may run past the budget: about 8 h in all. `light` (60) is about 4–5 h.

Pass `--max-output-tokens` (and `--review-rounds`) matching the app's Settings, so tuning sees what the app does.
The app default is now 12288. At the old 4096, the JSON for an 8–13k-character meeting was sometimes cut off
("EOF while parsing"), and that meeting scored 0.

Noise: the app runs the model at temperature 0.1, so repeat runs of the same prompt differ (single meetings moved by
up to ±0.5 between two baseline runs). Four validation meetings are a small sample, so confirm any winner with
`score` on every meeting (twice if it is close) before pasting it.

Real meetings: `--data` may point at one or more whole trees; a meeting is any directory (any depth, spaces fine)
holding `transcript.txt` and `gold.json`, with `agenda.txt` optional; the member list (`Members.csv`) may sit at the
tree's root. The maintainer's samples live in the git-ignored
`sample_meetings/{Bishopric,Ward Council}/<yyyymmdd>/`; directories without `gold.json` are skipped with a warning.
Write `--out` and `--run-dir` outside the repo (the tool refuses otherwise) and run inside the sandbox command above:

```sh
sandbox-exec -f loopback-only.sb uv run --offline --frozen score --data ../../sample_meetings --model qwen3.6:35b-a3b
```

`tune` writes the best full `EXTRACT_INSTRUCTIONS` value to `--out`, and `<out>.report.txt` with the validation
score per meeting for the baseline (current `prompts.py`) and the best candidate. `score` prints the score and the
feedback per meeting.

## Gold format

`<meeting>/gold.json` next to `transcript.txt` and, usually, `agenda.txt` (a first line starting with `SYNTHETIC` in a
text file is dropped; the repo audit requires that marker on synthetic files). Unknown keys (`_comment`,
`gpt_errors`, …) are ignored.

```json
{"workspace": "ward_council",
 "expected": [{"item": "Reimbursement guidelines", "keywords": [["reimburs"], ["dec", "31"]], "follow_up": false}],
 "not_discussed": ["Ward history"],
 "forbidden": [{"keywords": [["christmas"], ["gluten"]], "why": "gluten-free bread is not about the Christmas program"}]}
```

- A note matches `keywords` when every inner list has at least one alternative as a case-insensitive substring of
  the rendered bullet (tags included). An expected fact is found when a note directly under its item matches.
- `forbidden` entries are checked against every rendered note, wherever it is filed. They target assertions, and
  substrings cannot see negation, so a note that is itself negated or hedged ("not", "no", "never", "pending",
  "unclear", "n't" …) never trips one: "has not yet accepted" does not match `[["accepted"]]`.
- `follow_up`: `true`/`false` checks the `[Follow-up]` tag on the matching note; `null` = don't care.
- `"also": ["Other heading", …]` (next to a non-null `item`): the fact also counts when it appears under one of these
  headings (for an agenda that lists the same thing twice). False "Not discussed." is still checked on `item` only.
- `"item": null`: the fact counts if any note anywhere matches (no misfiling or false-absence checks). Use it for
  no-agenda meetings, whose headings are only known at run time; a fixed heading there is warned about and counts
  only if a proposed heading matches it exactly.
- `topics` (no-agenda meetings): a list of topics, each either a list of alternatives (`["budget", "finance"]`) or
  full keywords (`[["budget"], ["deadline"]]`). A topic counts towards recall when a proposed heading or any note
  matches it; otherwise "Proposed headings missed topic …".
- `max_headings`: more proposed headings (all levels) than this costs 0.02 each (max 0.10). Only the fixed proposal
  prompt sets the headings, so this measures, it does not steer tuning.
- `reference_notes`: file name (in the meeting directory) of earlier reference notes, e.g. `gpt_notes.txt`. Only its
  word count is used, never its content as truth: words in the rendered notes vs the reference, both without
  headings, placeholders, tags and the final follow-up list; a ratio below 0.5 or above 2.0 costs 0.05 with
  "Notes are much shorter/longer than the reference (120 vs 640 words) …".
- Headings match after folding case, Unicode (NFC), curly quotes, dashes and whitespace. A heading that occurs more
  than once (several "Updates") is written `Parent > Updates`. A gold heading that matches no agenda item (or is
  ambiguous) is printed as a WARNING at start and skipped, never silently scored as 0.
- `names` (optional; needs a member list, see "Member names"): `[{"heard": "bob boss", "member": "Ross, Robert
  James"}]`. `heard` is the text as the transcript has it; `member` is the list's Full Name exactly (a name not in
  the list stops the load, without printing it). A heard text that already is the member's full or preferred name
  needs no suggestion and is scored directly; one the app does not suggest is only counted in the suggestion line.
  In `data/synthetic/`, five meetings carry one each (a titled sound-alike, two lowercase "nickname surname"
  garbles, a titled sound-alike shared by similar surnames, and one exact name).

## Applying a result

1. Read `out/best.txt` and `out/best.txt.report.txt`. Check the instructions contain no names, quotes or meeting
   content, and that the policy still reads right.
2. Compare on every meeting: `uv run score --data <dir> --model <model> --instructions out/best.txt`.
3. Replace the `EXTRACT_INSTRUCTIONS` value in `backend/agenda_first_notes/prompts.py` with the file's text (it is
   the full template, braces already escaped), bump `PROMPT_VERSION`, and add a note on what changed.
4. `cd backend && uv run pytest -q && uv run ruff check . && uv run mypy && uv run agenda-first-notes audit`, and
   optionally `AFN_REAL_MODEL=<model> uv run pytest tests/test_real_model.py -s`.

## Develop

```sh
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy
```
