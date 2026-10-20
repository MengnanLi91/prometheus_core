# Parity eval harness

Runs the OSTI research agent over a fixed question set on **both engines**
(`pipeline` and `deepagent`) and scores the results, so a change to answer
quality is visible before it ships and the default `deepagent` engine can be
compared against the `pipeline` baseline.

## Layout

| File | What |
|------|------|
| `questions.yaml` | 20 questions (core / scholarly / web / cache / adversarial) with optional expectations |
| `run.py` | runs each question through each engine, writes transcripts, scores |
| `scoring.py` | pure, offline metrics (unit-tested in `tests/test_eval_scoring.py`) |

## Running

Needs LiteLLM creds in the env (`REGISTRY_URL` / `LITELLM_MASTER_KEY`), network
for OSTI/OpenAlex/arXiv, and — for the two `cache` questions to actually hit
memory — a live Redis (`REDIS_URL`).

```
uv run python -m evals.run --engines pipeline deepagent \
  --redis-db-base 14 --report evals/report.json
```

Useful flags: `--limit N` (first N questions), `--no-redis` (rule out cache),
`--questions <path>`, `--workdir <dir>` (default `evals/out`). For LangSmith
traces, set `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY` first — the deep
engine's `astream` is already instrumented.

Multi-engine runs require `--redis-db-base N`. The harness assigns database
`N` to the first engine and `N+1` to the second, and refuses to run if either
database already contains keys. Reserve two empty databases for the gate; the
harness never clears Redis, so historical keys are not deleted.

Output: `evals/report.json` (per-question records + per-engine aggregate) and,
under `evals/out/{engine}/{qid}/`, a `result.json` + `progress.log` per run for
the manual spot-check.

## Scoring

Three layers — only the first is automatic:

1. **Offline metrics (`scoring.py`, automatic).** Per question:
   - `status_match` — status equals `expect_status` (when the spec pins one).
   - `provenance_ok` — a `completed` answer cites at least one source
     (`no_results`/`error`/`timeout` are exempt).
   - `citation_link_rate` — fraction of `Paper N` mentions that are real
     markdown links, not bare text.
   - `source_hit_rate` — fraction of `expected_source_ids` found in the
     provenance (when the spec lists any).
   - `fact_match` — fraction of `expected_facts` substrings present in the
     answer (when the spec lists any).
   - `wall_s` — wall-clock latency (measured directly by `run.py`).
   `aggregate()` rolls these into per-engine medians, a p90 latency, and pass
   counts.

   **Cost/tokens are not in `FinalResult`** and so aren't scored here — read
   them per run from LiteLLM's spend logs (or the LangSmith trace) and compare
   against the cost bar below by hand.

2. **LLM-judged rubric (manual, separate).** Feed each `result.json` to a judge
   model for groundedness and completeness on a 1–5 scale. Kept out of `run.py`
   so the harness stays deterministic and needs no judge creds.

3. **Manual spot-check (manual, separate).** Read 5 transcripts end-to-end —
   include the adversarial ones (`unobtainium-42` should be `no_results`; "Is it
   safe?" should ask to disambiguate, not fabricate) — and confirm the answer
   matches the cited sources.

## Acceptance criteria for the default engine

`deepagent` ships as default when, over this set, it:

- matches or beats `pipeline` on median `source_hit_rate` and `fact_match`
  (and, from the LLM rubric, on groundedness and completeness),
- has `provenance_ok` on every `completed` answer and ≥ 90% citation-link
  correctness (`median_citation_link_rate`),
- handles all 5 adversarial questions correctly (no fabricated citations; the
  no-answer question returns `no_results`; the figure-only question is answered
  — which the pipeline structurally cannot do),
- keeps `p90_wall_s` ≤ 15 min hard / ≤ pipeline + 25% soft, and
- costs ≤ 2× the pipeline per question (read from the spend logs, see above).

Set the bar before running, not after. Latency may regress somewhat versus the
fixed pipeline — the deep engine trades some speed for coverage — as long as it
stays under the p90 ceiling and quality wins.
