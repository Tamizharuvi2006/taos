# Current Architecture

## System Shape
TAOS currently runs as a single FastAPI application with one primary orchestration brain and several supporting subsystems.

Main layers:
- API surface in `apps/api/*`
- orchestration engine in `orchestration/engine.py`
- semantic routing in `core/semantic/*`
- fast-path handling in `core/fast_path/*`
- tools, execution, and DAG support in `core/tools/*` and `core/execution/*`
- document processing and ask flows in `core/documents/*`
- persistence and Firebase adapters in `infra/persistence/*`
- tasks, workflows, chats, and notifications in `core/tasks/*`, `core/workflows/*`, `core/chat/*`, and `services/notification/*`

## Request Paths
User requests currently route into one of several high-level paths:
- `fast_message`
  For tiny-talk, casual chatter, and deterministic edge replies.
- `no_search`
  For definitions and stable explanations that should not use web search or planner.
- `fast_search`
  For quick current lookups that need one live search pass without full deep research.
- `deep_search`, `news_search`, `official_search`, `comparison_search`
  For web-grounded, freshness-sensitive, official-source, or comparison-heavy research requests handled by the research pipeline.
- `task`
  For bounded task execution and planner-owned tool work.
- `standard_task`
  Preserved as a compatibility label for older direct-answer and planner paths.
- `doc_mode`
  For uploaded-document-grounded questions and follow-ups.
- `clarification`
  For ambiguous or unsafe requests that need a user-facing clarification boundary instead of risky execution.

## Orchestration Core
The core runtime still centers on one engine:
- validate goal
- make a Phase 107 deterministic-first route decision
- classify intent/domain for compatibility metadata
- build interpretation and route decision without letting LLM classification be the first gate
- optionally take fast path
- otherwise plan and execute
- reflect, validate, and finalize

Important behaviors already present:
- request time budgeting
- fallback behavior for failed research stages
- trace capture
- trust block generation
- route-aware frontend hints
- route-specific authority formatting and follow-up shaping across current public routes

Phase 107 routing ownership is now explicit:
- cache/rules/scoring run before any semantic LLM classifier
- tiny LLM route fallback is allowed only for low-confidence ambiguous routing
- `fast_message` and `no_search` cannot enter planner/search paths
- `fast_search` is owned by Search Lite
- `deep_search`, `news_search`, `official_search`, and `comparison_search` are owned by ResearchPipeline
- `doc_mode` is owned by the document pipeline
- `task` is the only default route allowed to enter the FSM planner

Search-specific hardening now includes:
- `core/search/search_depth_router.py`
  Decides `no_search`, `fast_search`, `deep_search`, `news_search`, `official_search`, or `comparison_search`
- `core/routing/*`
  Phase 107 deterministic-first routing core with exact route cache, heuristic scoring, and tiny LLM fallback only for ambiguous cases
- `core/search/search_lite.py`
  Handles one-shot current lookups with live search plus route-aware search-result caching
- `core/research/research_pipeline.py`
  Builds deterministic deep-research query variants and now coordinates evidence selection, citation planning, conflict summaries, and freshness boosting helpers
- `core/research/evidence_selector.py`
  Ranks evidence by relevance, quality, freshness, extraction quality, uniqueness, and snippet-only penalties
- `core/research/citation_planner.py`
  Maps answer paragraphs to source IDs and flags unsupported factual sections
- `core/research/source_diversity_enforcer.py`
  Caps repeated domains and preserves category mix across official, trusted, technical, and balancing sources
- `core/research/freshness_booster.py`
  Triggers one extra recency-focused query only when freshness-sensitive cases remain weak
- `core/research/conflict_resolver.py`
  Summarizes conflicting evidence groups and unresolved disagreement signals
- `core/research/freshness_policy.py`
  Tracks freshness mode, stale detection, and freshness scoring
- `core/research/source_quality.py`
  Adds transparent source scoring + domain diversity controls
- `core/research/extract_recovery.py`
  Allows snippet-backed recovery when extraction fails
- `core/research/no_result_handler.py`
  Emits explicit verified-failure responses instead of hallucinated no-result answers

## Evaluation Harness
TAOS now also has a research evaluation layer for search and deep-research benchmarking:
- `core/evaluation/research_eval.py`
  Case loading, scoring, aggregation, weak-area diagnosis, and Markdown report generation
- `core/evaluation/live_eval_guard.py`
  Provider, cost, case-count, and runtime guardrails for optional live evaluation mode
- `scripts/run_research_eval.py`
  Mock-friendly benchmark runner with optional guarded `--live` mode and timestamped live reports
- `eval/research_cases.json`
  Seed benchmark cases for fast search, deep search, news search, and failure modes
- `eval/research_eval_report.md`
  Commit-friendly benchmark report output
- `eval/live_runs/`
  Timestamped live-eval artifacts when live mode is explicitly enabled

## Reliability Additions
The current hardening pass adds lightweight shared reliability utilities:
- `core/reliability/budgeting.py`
  `RequestBudgetManager` and `StageBudgetManager`
- `core/reliability/fallbacks.py`
  `TimeoutFallbackBuilder` and `AmbiguityFallbackHandler`

These are intended to keep edge-route behavior consistent even before deeper engine refactors.

## Audit Status
TAOS is now in a mixed state of mature runtime paths plus newly completed reusable modules.

Implemented and wired:
- deterministic routing core and route-boundary metadata
- search-result caching for `SearchLite` and deep-research search variants
- extract/evidence cache reuse inside the deep-research path
- reusable research quality modules for evidence selection, citation planning, diversity, freshness boosting, and conflict summaries
- high-stakes research guard module and trust metadata surfacing
- live-eval guard plus mock/live script support
- route-aware authority/citation formatting across `fast_search` and research-family routes
- route-aware frontend hints and progress labels for the full public route set
- intelligence eval route telemetry aligned with specific and compatibility route labels
- route-owner dispatch now enforces:
  - document requests -> document pipeline
  - `no_search` -> direct no-tools answer path
  - `fast_search` -> Search Lite
  - deep-research routes -> research pipeline before planner fallback

Still centralized in `orchestration/engine.py`:
- deep-research orchestration remains large and monolithic even though the route boundaries and cache layers are now explicit and enforced

## Response Contract
The backend is moving toward one stable envelope returned across normal and streaming execution:
- answer
- sections
- route
- mode
- confidence
- sources
- trust block
- trace
- warnings
- metadata
- evidence matrix summary

Compatibility fields are still preserved for the current frontend.

Search/research metadata now also surfaces:
- freshness summary
- cache summary
- source diversity score
- extraction recovery usage
- evidence matrix summary
- citation plan summary
- evidence selection summary
- conflict summary
- high-stakes summary
- route boundary summary

## Phase 108 Reliability Lock
The current reliability lock adds a route truth endpoint and safer weak-evidence behavior:
- `POST /debug/route`
  Returns the deterministic route, normalized query, route owner, confidence, matched rules, LLM fallback usage, and whether the request will use web, Search Lite, ResearchPipeline, DocumentPipeline, or the planner.
- Search Lite weak-evidence fallback
  If quick search finds candidate sources but cannot verify a source-of-record answer, it now returns a cautious candidate answer with sources, key points, uncertainty, and a confidence-reduction reason instead of only a generic no-answer response.
- Search Lite source-reading guard
  For current/version lookups, Search Lite can now open top candidate/source-of-record pages with `web_extract` when snippets are weak or incomplete, merge extracted page text into verification, and surface `extract_count`, `source_reading_used`, and `snippet_only` metadata.

## Persistence
Persistence is no longer “memory only.”

Current state:
- memory fallback exists
- Firebase/Firestore-backed persistence exists
- startup now logs which persistence backend is active
- readiness can degrade if Firebase is configured but unavailable

## Documents
Document support is substantial, not experimental:
- upload/init
- process document
- repository/status APIs
- retrieval-grounded ask flows
- local `/execute` doc-mode contract parity for retrieval metadata, cache summary, and evidence stats

One remaining architecture cleanup item is broader live validation of those document flows with real uploaded files in the target environment.

## Frontend Surface
The visible frontend surface in this repo is limited, but execution responses already expose:
- route and mode
- trust block
- trace
- answer sections
- frontend hints for rendering

The paired frontend in `D:\agent\frontend` now surfaces:
- evidence support badge
- citation coverage meter
- unsupported-claims warning
- evidence matrix panel in the trace inspector
- confidence-adjustment reason in the trust UI
- route ownership, research quality, and cache-layer trace sections

## Architecture Summary
TAOS should currently be described as:
- one strong orchestration system
- multiple specialized execution paths
- explicit search-depth routing before research
- a fast current-lookup search lane and a deeper research lane
- real persistence and document capabilities
- unified response-contract hardening across fast, standard, research, fallback, and doc paths
- locally completed route, contract, cache, and evidence-quality hardening
- still carrying architecture debt in the size of `orchestration/engine.py` and external validation work
