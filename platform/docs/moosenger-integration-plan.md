# MOOSEnger integration: issue draft and PR plan

Working notes, not for commit. Part 1 is the GitHub issue to file on
`idaholab/prometheus_core`. Part 2 is how to split the branch into PRs.

---

## Part 1: issue draft

**Template:** Feature or Enhancement Request
**Title:** Integrate MOOSEnger as an optional platform agent, with results stored in DeepLynx
**Component:** platform (agents / orchestrator), platform (backend), platform (frontend), sdk (prometheus_api)

### Motivation

MOOSEnger is a MOOSE assistant that answers MOOSE documentation questions,
generates and validates MOOSE input files, and runs them. It works well on its
own, and we want the same capabilities from the Prometheus chat: ask a MOOSE
question, get an input file, review the plan and the run, and keep the
results.

Running it on the platform exposed several gaps that are not MOOSEnger-specific:

- The orchestrator rewrites the user's request before handing it to a skill.
  For MOOSEnger the rewrite pushed a plain question into input-authoring plan
  mode, and after a form, the skill received only the user's last word ("yes").
- A generic approval card shows only the skill name and the user's own words,
  so there is nothing to approve.
- Large results (input files, logs, run archives) fill the chat message; there
  is no way for an agent to put an artifact in the side panel only.
- Answering a paused task's form resumes the same task id, and the SDK then
  fails to create its DeepLynx execution record a second time
  (`unique_record_original_id`), so the answer never reaches the agent.
- After a run, MOOSEnger reports only what the solver log says (converged, the
  residual, the run time). It never reads the results, so the report can't
  say what the solution looks like: whether the temperature profile is the
  expected linear one, the range of a field, a postprocessor's final value.
  Users have to download the Exodus or CSV file and check it themselves.
- A MOOSEnger session's work (draft, plan, precheck reports, repair log, MOOSE
  outputs) lives only in the agent container's volume and on the host running
  MOOSE. Nobody can open it from the chat, and it is not kept with the rest of
  the platform's data in DeepLynx.

### Design

**Platform (this repo)**

1. **Optional MOOSEnger agent** under `platform/agents/optional/moosenger-agent/`
   (opt in with `OPTIONAL_AGENTS=true`), built from a MOOSEnger checkout
   (`MOOSENGER_SRC`). MOOSE runs out of process through MOOSEnger's MCP server
   (host or an optional sidecar). The agent card ships with the agent so
   `make check` can find its registered name.
2. **Pass the user's words to a skill unchanged.** A skill marks a top-level
   input field with `"x-prometheus-source": "user_message"`. The orchestrator
   hides that field from its model and fills it with the user's own words for
   the request in progress: typed messages and form answers since the last
   successful call to a marked skill.
3. **Side-panel artifacts.** A new `ArtifactMeta.display` (`inline` | `panel`),
   set by the producing agent through `metadata.display`. The frontend leaves
   `panel` artifacts out of the message, lists them in the side panel, opens a
   new one there, and shows it in the matching viewer (code, markdown, JSON,
   table, image, PDF). Default `inline`, so other agents are unchanged.
4. **Orchestrator answer style.** The final answer names the main result
   instead of listing every file and pasting logs.

**SDK (`prometheus_api`)**

5. **Reuse the execution record on resume.** When creating the per-task
   DeepLynx execution record fails, look up the record this task already has
   (by `original_id`) and reuse it; any other failure still raises.

**DeepLynx integration**

6. **Store MOOSEnger results in DeepLynx.** Every MOOSEnger result is uploaded
   to the requesting user's DeepLynx project with the SDK's
   `send_deeplynx_artifact`, using their just-in-time `deeplynx` credential,
   and reaches the chat as a `deeplynx://` artifact in the side panel:
   - the verified input (`current.i`) and the solver logs, one item each;
   - a zip of the session's run directory (draft, plan, plan contract,
     precheck reports, repair log, run logs) after every generation or
     execution turn, blocked or successful, so a failed run can still be
     inspected;
   - the MOOSE run outputs (Exodus, CSV, JSON), published by the MCP server to
     `deeplynx://moosenger/outputs/<run>/` and registered as artifacts; Exodus
     opens in the mesh viewer.

   Items carry `json_type`s (`moosenger.verified_input.v1`,
   `moosenger.solver_log.v1`, `moosenger.run_archive.v1`,
   `moosenger.exodus_output.v1`, ...), tags (`moosenger`, `moose-input`,
   `moose-output`, `moosenger-run`), and the skill, conversation id, task id
   and verification status, so they can be found without the chat. Uploads
   are size-limited (50 MB per file, 200 MB per archive by default); a failed
   upload logs a warning and never costs the user the answer.
   `MOOSENGER_DEEPLYNX_ARCHIVE=false` turns uploading off. The backend already
   resolves `deeplynx://` URIs for download, so the platform needs no change
   for this beyond the side-panel display (item 3).
7. **Execution records on resumed tasks** (item 5) keep the platform's
   per-request DeepLynx audit trail working when the user answers a form.

**Result postprocessing (MOOSEnger)**

8. **Read the results after a run.** Add simple read-only postprocessing tools
   to MOOSEnger, so the after-run report describes the solution, not just the
   solver log:
   - **CSV** (postprocessor and vector-postprocessor output): columns, final
     and min/max values per postprocessor, and how they change over time for
     transient runs.
   - **Exodus** (`*.e`, including split `*.e-s*` files): mesh size, blocks and
     sidesets; the nodal and elemental variables; time steps; per-variable
     min/max/mean at the last (or a chosen) step; values sampled at points or
     along a line.
   - **A short summary for the agent and the user**: a compact,
     text-only result (no plots in the first version) the model can quote,
     with the file and step it came from, so claims are traceable.

   The agent calls these after a successful full run, and the final answer
   gains a "Results" section (for the heat example: T spans 0 to 1 and is
   linear in x to within 1e-6). The tools read the outputs MOOSEnger already
   stages into the session (the same files published to DeepLynx), work on
   files of any size by reading only the requested variables and steps, and
   report clearly when a file is missing or unreadable rather than guessing.
   Exodus reading needs a netCDF reader (`netCDF4`, or `meshio`), added as an
   optional dependency; CSV needs none beyond the standard library.

**MOOSEnger repo (tracked here, merged there)**

9. Platform adapter: plan review (approve / improve / reject, the plan as an
   artifact), run approval with the input diff, docs Q&A without plan mode,
   plan checks that follow the plan's workflow, the unverified draft on a
   blocked run, the DeepLynx uploads above, a changed input returned on reruns.
10. MCP server: publish run outputs to DeepLynx (`--output-root-uri`, or
   `mcp_server.output_root_uri`), a `mcp_server:` section in `.moosenger.yaml`
   (DeepLynx base URL, organization and project; credentials stay in the
   environment), a startup probe that works against DeepLynx, and fixes for
   tool-approval interrupts that never fired (sanitized tool names,
   dict-shaped interrupts).

### Impact

- **Wire contract:** `ArtifactMeta` gains `display` (API snapshot updated,
  mirrored in `frontend/src/lib/api/types.ts`, documented in
  `backend/CONTRACT.md`). Additive; unknown values mean `inline`.
- **Agent cards:** new optional input-schema extension
  `x-prometheus-source: user_message`. Agents that don't use it are unchanged.
- **SDK:** behavior change in the executor on resumed tasks. Needs a version
  bump and an update to REQ-009-16..19 in
  `sdk/specs/009-agent-execution-events-and-workers.md`.
- **Deployment:** a new optional agent, only with `OPTIONAL_AGENTS=true`; new
  `MOOSENGER_*` / `MOOSE_MCP_*` variables in `.env.example`. No change to the
  default stack.
- **DeepLynx:** with the MOOSEnger agent enabled, each generation or execution
  turn writes several items to the user's DeepLynx project (inputs, logs, a
  run archive, run outputs). Projects need room for them, and teams may want a
  retention policy. The MCP server needs DeepLynx credentials in its
  environment to publish run outputs. A container-side `DEEPLYNX_BASE_URL`
  must reach DeepLynx from inside Docker (`host.docker.internal` for a
  DeepLynx on the host, not `localhost`).
- **Postprocessing:** MOOSEnger-only; no platform change. The agent image gains
  an optional netCDF reader for Exodus files. Reading results adds a little
  time after each successful run; the after-run report gets longer by one
  "Results" section.

### Tasks

Platform (prometheus_core):

- [ ] Add the optional MOOSEnger agent (#PR-1)
- [ ] Ship the MOOSEnger agent card with the agent (#PR-1)
- [ ] SDK: reuse a resumed task's execution record (#PR-2)
- [ ] Orchestrator: pass the user's words to marked skill fields (#PR-3)
- [ ] Backend + frontend: side-panel artifacts (`display`) (#PR-4)
- [ ] Orchestrator: shorter final answers (#PR-5)
- [ ] Document the MOOSEnger agent end to end (#PR-1 README, plus updates)

MOOSEnger (idaholab/moosenger, separate MRs):

- [ ] Platform adapter: plan review, docs Q&A without plan mode, workflow-aware plan checks
- [ ] Platform adapter: run approval with the input diff, stale-approval handling
- [ ] Fix tool-approval interrupts (sanitized names, dict payloads)
- [ ] Unverified draft on blocked runs; changed input returned on reruns
- [ ] DeepLynx: store the verified input and solver logs as items
- [ ] DeepLynx: archive the session's run directory after every turn
- [ ] DeepLynx: publish MOOSE run outputs from the MCP server and register them as artifacts
- [ ] DeepLynx: fix the MCP startup probe (extensionless probe file could not be deleted)
- [ ] MCP server: `mcp_server:` config section (incl. the DeepLynx connection)
- [ ] Postprocessing: CSV reader tool (columns, final and min/max values, time history)
- [ ] Postprocessing: Exodus reader tool (mesh, variables, steps, field statistics, point and line sampling)
- [ ] Postprocessing: call the readers after a successful run and add a "Results" section to the report
- [ ] Postprocessing: optional netCDF dependency; clear errors for missing or unreadable files

Follow-ups (not started):

- [ ] Infra test for the optional agent (healthcheck, registration)
- [ ] Decide whether to block `ask_user` before calls to marked skills
- [ ] Postprocessing: plots (line plots, contour snapshots) as image artifacts
- [ ] Postprocessing: check results against expectations the user states (e.g. "T should be linear")
- [ ] DeepLynx: decide a retention policy for MOOSEnger run archives and outputs
- [ ] DeepLynx: link run archives and outputs to the request's execution record
- [ ] Clean up test records left in the local DeepLynx project

---

## Part 2: PR plan for prometheus_core

Current state of `MOOSEnger2platform` (base: PR #371, `12428c84`):

| Commit | Summary | Files |
|---|---|---|
| `ea2d187` | feat(agents): add the optional MOOSEnger agent | 15 |
| `2e5810f` | fix(moosenger-agent): make the MOOSE checkout opt-in | 4 |
| `c479023` | feat(moosenger-agent): MOOSENGER_VECTOR_DIR | 4 |
| `f001ed4` | feat(moosenger-agent): MOOSENGER_KNOWLEDGE_DIR | 4 |
| `91edae0` | feat(moosenger-agent): one knowledge path | 4 |

Plus uncommitted work (about 530 lines across 19 files).

### Proposed PRs

Each PR branches from `upstream/devel` unless it says it is stacked.

**PR-1: `feat/moosenger-agent`, Add the optional MOOSEnger agent**

- Squash the five commits above into one, plus the new
  `agents/optional/moosenger-agent/agent_card.json`.
- About 760 lines; most is the agent's README, Dockerfiles and compose. It is
  one piece (a new agent), so say so in the description.
- No dependency on the other PRs. With the old card, the orchestrator still
  rewrites requests; that is fixed by PR-3 together with the card's
  `x-prometheus-source` marker.

**PR-2: `fix/sdk-resumed-execution-record`, Reuse a resumed task's execution record**

- `sdk/prometheus_api/agent/base.py`, `sdk/tests/test_agent.py` (about 115 lines).
- Independent and generic: any agent that pauses with INPUT_REQUIRED hits it.
  Land it first.
- Before opening: bump the SDK version (`make bump-patch` → 0.15.1), update
  REQ-009-16..19 and reference the IDs in the new tests.

**PR-3: `feat/orchestrator-verbatim-user-fields`, Pass the user's words to marked skill fields**

- `client-agent/orchestrator/agent_loop.py`, `client-agent/tests/test_agent_loop.py` (about 185 lines).
- Independent. Document `x-prometheus-source` in `docs/docs/dev-docs/adding-agents.md`.

**PR-4: `feat/artifact-display-panel`, Show agent-chosen artifacts only in the side panel**

- Backend: `app/contract.py`, `app/services/artifacts.py`, `tests/test_artifacts.py`,
  `tests/snapshots/api_surface.json`, `CONTRACT.md`.
- Frontend: `lib/api/types.ts`, `lib/elements/artifactAdapter.ts`,
  `lib/elements/artifactDisplay.test.ts`, `components/Chat.tsx`,
  `components/dashboard/StatusPanel.tsx`, `AGENT-CONTRACT.md`.
- Docs: `docs/docs/dev-docs/artifacts.md`.
- About 230 lines. Backend and frontend together, since the guide says to
  update both sides of the wire contract together. Add a screenshot.

**PR-5: `feat/orchestrator-concise-answers`, Name the main result in final answers**

- `client-agent/prompts/orchestrator_system.txt` (2 lines). Could fold into
  PR-3; kept separate because it is a prompt change reviewers may want to
  weigh on its own.

**Leave out:** the root `.gitignore` line for `.vscode/settings.json`. It is a
local editor preference; put it in `.git/info/exclude` instead.

### Suggested order

1. PR-2 (SDK) and PR-3 (orchestrator): independent, open in parallel.
2. PR-4 (display): independent, open in parallel.
3. PR-1 (agent): last, so its card can rely on PR-3. If it opens before PR-3
   merges, note the dependency in its description.
4. PR-5: any time.

No stacking is needed: the PRs touch disjoint files.

### Checks per PR (from CONTRIBUTING.md)

- **SDK:** `make check` and `make test` in `sdk/`; run tests in a clean
  environment (exported `DEEPLYNX_*` variables break 4 config tests).
- **Backend:** `uv run ruff check . && uv run ruff format --check .` and `uv run pytest`.
- **Frontend:** `make check` (eslint, tsc) and `npm test`.
- **Platform:** `make test-static` and `make check-markings`. New files
  (`artifactDisplay.test.ts`) need the copyright marking:
  `python3 platform/scripts/add_markings.py` after `git add`.
- **Client-agent:** `uv run pytest` in `agents/client-agent/`.
- Tick the AI-assistance box in each PR and say what it was used for.
