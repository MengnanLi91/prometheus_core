# OSTI Research Agent — architecture

How the OSTI research agent is currently structured. For the platform it
plugs into (LiteLLM gateway, Valkey, A2A), see the repo-root `CLAUDE.md`.

## The boundary contract

The code is split into a **transport-agnostic core** and a **thin A2A shell**:

```
a2a layer (agent.py)                 <- the ONLY file that may import prometheus_api / a2a
  └─ ResearchRunner.run(question)    <- core/runner.py: the event-stream boundary
       yields AgentEvent = ProgressEvent | FinalResult   (core/events.py)
         ├─ engine="pipeline"  -> pipeline.answer_question   (legacy, rollback lever)
         └─ engine="deepagent" -> core/deep_runner.run_deep  (default)
```

- **`research_agent/**` imports nothing from `prometheus_api` or `a2a`.** This is
  enforced by `tests/test_import_boundary.py` (a regex over the whole package).
  The core runs on a laptop with only LiteLLM credentials — see `cli.py`
  (`python -m research_agent "<question>" --engine deepagent --no-redis`).
- **`ResearchRunner`** is the single seam every transport goes through. It
  dispatches on `config.engine`, bridges the engine's progress to an
  `AsyncIterator[AgentEvent]`, and enforces the outer time budget
  (`total_budget_s - 30`), salvaging a `timeout_partial` `FinalResult` from
  whatever findings accumulated if the budget fires.
- **`FinalResult`** (`core/events.py`) is the wire payload. `core/adapter.py`
  serializes it to the exact JSON key set `agent.py` produced pre-refactor
  (`test_adapter.py` golden-checks this), so the frontend contract is unchanged.
- **Model routing** resolves `LLMConfig.from_env()` first and attaches a proxy
  key only when the selected profile has a proxy URL. Deep Agents receives a
  provider-neutral `ChatLiteLLM` model for registry, direct OpenAI-compatible,
  and direct Anthropic profiles, including tool binding. No model temperature
  is forced by the agent; deployed profiles control supported parameters.

## The two engines

Both engines emit the same `AgentEvent` stream and honor the same budget; only
how they produce results differs.

- **`pipeline`** (`pipeline.py` + `llm_steps.py`) — the original fixed sequence
  (search → assess abstracts → download → extract text → synthesize). Kept as
  a rollback option selectable with `AGENT_ENGINE=pipeline`.
- **`deepagent`** (`core/deep_runner.py`) — a `deepagents.create_deep_agent`
  orchestrator (LangGraph under the hood) with a compiled `paper-analyzer`
  task. The task first analyzes one parsed paper, then independently verifies
  the candidate against that paper's artifacts before returning anything to
  the orchestrator. The orchestrator decides what to search, download, and
  analyze; the strategy lives in `prompts/orchestrator.md`.
  This is the **default** (`AGENT_ENGINE` unset ⇒ `deepagent`).

The analyzer and verifier are read-only and receive no search, download, or
parsing tools. Only verified or verifier-corrected findings enter
`RunContext.findings`, final results, timeout partials, or fact storage.
Operational verifier failures produce an error when no other finding was
verified; evidence-based rejections produce `no_results`.

## Tools (deepagent engine)

Tools are thin `@tool` wrappers built per-run over a shared `RunContext`
(`tools/context.py`), which holds the config, the injected `complete`/embed
functions, the Redis-backed caches, and the mutable state a run accumulates
(`search_hits`, `pdf_paths`, `findings`, counters). Budget is enforced **at the
tools** — an over-budget tool returns a refusal string, it never raises (a
raising tool would crash the orchestrator loop; `test_tools.py` pins this).

`tools/__init__.build_tools(ctx)` assembles:

- **`tools/osti.py`** — `search_osti`, `download_paper`, `parse_paper`.
  `download_paper(source_id, url=None)` fetches an arbitrary URL when given one
  (or an `oa_pdf_url` recorded in `search_hits`), else the OSTI purl.
- **`tools/search.py`** — `search_scholarly` (OpenAlex + arXiv, keyless, always
  on) and `search_web` (provider-backed, off unless configured).
- **`tools/memory.py`** — `search_knowledge_base` and `store_facts` over the
  embedding-backed `KnowledgeStore`.

### Adding a tool

1. Add a `@tool async def` inside a `build_*_tools(ctx)` factory (new file or an
   existing one), reading/writing run state through `ctx`. Enforce budget with
   `ctx.budget_reason()` and **return a string on refusal**.
2. Include the factory's output in `tools/__init__.build_tools`.
3. Add a status label in `core/deep_runner._TOOL_STATUS` so the tool call
   renders a human-readable progress line.
4. Document it for the orchestrator in `prompts/orchestrator.md` (when to use
   it, its input/output shape). The model only uses tools the prompt explains.
5. Unit-test it over a mocked `ctx` like the others in `tests/test_tools.py` /
   `tests/test_search_tools.py` — no graph needed.

### Adding a web-search provider

Implement the `WebSearchProvider` protocol in `web_search.py` (one
`async def search(query, k) -> list[dict]` returning
`{title, url, snippet, published, origin}`), wire it into `get_web_provider`
keyed off `config.web_search_provider`, add its config fields to
`core/config.py` (+ `from_env`), and add a respx test like `test_web_search.py`.
Scholarly sources (OpenAlex/arXiv) live in `scholarly.py` and follow the same
shape.

## Parsing and artifact layout

`parsing/pipeline.parse_paper_pipeline` turns a PDF into a **paper directory**
rather than one text blob. Docling runs in an isolated subprocess
(`parsing/docling_worker.py`); on failure/timeout it falls back to PyMuPDF
full-text (`method="pymupdf"`, no tables/figures, warning recorded) so consumers
always have one `paper.md` code path. Optional figure VLM analysis
(`parsing/figures.py`) is off by default (`ANALYZE_FIGURES`).

```
{workdir}/papers/{source_id}/
  paper.md                     # reading-order text with [TABLE ..]/[FIGURE ..] markers
  meta.json                    # the ParsedPaper record (parsing/artifacts.py)
  tables/table_NN.{md,csv}
  figures/fig_NN.png           # + fig_NN.json when the VLM pass ran
```

`ParsedPaper` / `TableArtifact` / `FigureArtifact` (`parsing/artifacts.py`) are
the contract; every consumer reads via them.

## Memory

`knowledge.RedisKnowledgeStore` implements the `KnowledgeStore` protocol.
Retrieval is `SCAN` + in-process cosine over per-atomic-fact embeddings — **no
`KEYS`, no per-lookup LLM call** (the two scaling problems of the original
memory code). Embeddings come from the LiteLLM proxy (`core/llm.LiteLLMEmbedder`,
the `embedding` alias). The cached-answer path keeps the exact question-hash
fast path and adds a near-duplicate (cosine > 0.95) hit. Key prefixes are
byte-compatible with the legacy store; a missing embedding is backfilled lazily
on read (bulk: `scripts/backfill_atomic_fact_embeddings.py`). See `runbook.md` for
the key/TTL table.

Cached answers and facts are retrieval hints, not final evidence. The deep
agent must download, parse, analyze, and verify the referenced source in the
current run before using its claims in a result.

## Tests

Run with the project's canonical command (plain `uv run pytest` is broken — the
miniforge pytest lacks redis):

```
uv run --with pytest --with pytest-asyncio --with respx python -m pytest -q
```

`test_agent.py` skips without `prometheus_api`/`a2a`; golden/slow tests skip
unless `-m slow`.
