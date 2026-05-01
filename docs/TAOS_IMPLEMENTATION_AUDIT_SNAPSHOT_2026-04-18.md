# TAOS IMPLEMENTATION AUDIT (Snapshot)

Date: 2026-04-18
Scope: Current repository implementation audit snapshot for external reviewer handoff.

## 1) Executive Summary

- TAOS has a real multi-path architecture: `fast_message`, `standard_task`, `deep_research`, and `doc_mode` paths.
- Routing is implemented with hybrid semantics + heuristic policy overrides.
- Adversarial fast-path guards and low-confidence fast-route blocking are implemented.
- Timeout reliability hardening is implemented (global + per-stage caps) and reduced prior batch timeout failures.
- Research pipeline includes query generation, ranking, extraction, uncertainty guardrails, and fallbacks.
- Trust/citation/uncertainty enforcement exists in backend synthesis and post-processing blocks.
- Frontend reflection payload contract is implemented in API schema and route enrichers.
- Evaluation harness is implemented with batch slicing (`--offset`, `--limit`) and report generation.
- CI quality gate exists and checks thresholds, but currently runs against the smaller base fixture set.
- Current strongest area: routing stability + reliability compared to earlier baseline.
- Current weakest quality area: citation depth/confidence calibration on noisy and high-stakes/adversarial prompts.
- Important gap: research success-path control flow around synthesis should be cleaned up and regression tested.

## 2) Runtime Flow (High Level)

User -> `/execute` or `/execute/stream` -> intent classification -> selected path (`micro-fast` / `standard_task` / `deep_research` / `doc_mode`) -> quality guards (trust/citation/uncertainty/integrity) -> API response enriched with frontend reflection fields -> UI render.

## 3) Subsystem Status

### 3.1 Routing + Intent Layer
Status: Implemented

- File: `core/semantic/intent_classifier.py`
- Evidence:
  - adversarial guard patterns
  - Tamil/mixed intent hints
  - low-confidence fast-message guard
  - policy override function
- Notes: solid and actively tuned.

### 3.2 Fast Path / Micro-Fast Path
Status: Implemented (with quality limitations)

- Files:
  - `core/fast_path/fast_path.py`
  - `core/fast_path/query_cache.py`
  - `apps/api/routes/agent.py` (micro-fast trust block)
- Notes: very fast for light prompts; can under-serve lookup-style requests if misrouted.

### 3.3 Standard Task Path
Status: Implemented

- File: `orchestration/engine.py` + controller FSM modules
- Notes: planner/executor/reflect flow with request budget checks and safety transitions.

### 3.4 Deep Research Pipeline
Status: Implemented (partial robustness gaps)

- File: `orchestration/engine.py`
- Includes:
  - research query generation + sanitization
  - evidence ranking/agreement
  - semantic cache hooks
  - timeout-aware fallback behavior
- Notes: strong structure, but synthesis success-path control-flow cleanup is still needed.

### 3.5 Document / Uploaded File Pipeline
Status: Implemented (partially split path)

- Files:
  - `apps/api/routes/documents.py`
  - `core/documents/processing_service.py`
  - `core/documents/ask_service.py`
  - `core/tools/builtin/retrieve_chunks.py`
- Notes: document stack is substantial; `/execute` doc shortcut and ask-service path are not fully unified.

### 3.6 Trust / Uncertainty / Citation Layer
Status: Implemented (quality tuning still needed)

- File: `orchestration/engine.py`
- Includes:
  - trust block calibration
  - citation insertion/quality blocks
  - high-stakes uncertainty guard
  - adversarial integrity guard
- Notes: improved safety language, but metric-level citation quality remains a main weak area.

### 3.7 Frontend Reflection Layer
Status: Contract implemented, real frontend hookup appears external/partial

- Files:
  - `apps/api/schemas/agent.py`
  - `apps/api/routes/agent.py` (payload enrichers)
  - tests for reflection fields
- Notes: backend sends reflection fields; this repo does not contain a complete active Next.js chat UI hookup.

### 3.8 Evaluation Harness
Status: Implemented

- Files:
  - `core/evaluation/intelligence_eval.py`
  - `scripts/run_intelligence_eval.py`
  - `core/evaluation/research_eval.py`
  - `scripts/run_research_eval.py`
- Notes: batch runner and detailed reports exist.

### 3.9 CI Quality Gate
Status: Implemented (scope-limited)

- Files:
  - `.github/workflows/quality-gate.yml`
  - `scripts/check_eval_thresholds.py`
  - `docs/eval_thresholds.json`
- Notes: threshold enforcement exists, but killer-v2 full-suite is not the default CI gate target.

## 4) Deep Research Audit

- Query generation: implemented with sanitizer + recovery query builder in `orchestration/engine.py`.
- Search: multi-query web search path with stage timeout enforcement.
- Ranking: evidence ranking and agreement scoring implemented.
- Extraction: extraction stage timeout + fallback behavior exists.
- Trust/freshness/agreement: computed and exposed through trust block/front-end hints.
- Fallbacks: partial-evidence and unverified fallbacks for timeout/error/sparse-result states.
- Current bottlenecks:
  - over-aggressive routing to fast path for some lookup prompts still possible,
  - synthesis success control-flow cleanup needed,
  - citation depth/confidence shaping still needs pass-level tuning.

## 5) Document Pipeline Audit

- Upload processing: implemented with chunking + dedupe behavior.
- Retrieval: hybrid scoring in retrieval tool (semantic + lexical + fuzzy/phrase helpers).
- Modes: intent router exists for document ask flows.
- Fallback/timeout: ask service includes fallback handling.
- Grounding: citation-style grounding contract exists and is tested.

## 6) Trust + Answer Quality Audit

- Citation enforcement: present via quality blocks and post-processing.
- Uncertainty handling: present with explicit high-stakes/weak-signal language reinforcement.
- Confidence calibration: implemented, but metric outputs show this still needs tuning.
- High-stakes behavior: improved and guarded.
- Adversarial integrity: implemented with explicit refusal-style safeguards.
- Backend vs frontend:
  - backend builds trust/reflection payload,
  - frontend rendering parity is only partially represented in this repo.

## 7) Frontend Reflection Audit

Structured fields implemented in API contract:

- `trust_badges`
- `uncertainty_box`
- `answer_sections`
- `source_cards`
- `frontend_hints`

Backend enrichment exists in route layer and is covered by tests. Real chat-window production hookup appears to be in a separate frontend workspace.

## 8) Evaluation + Benchmarks

- Fixtures:
  - base intelligence fixture
  - v2 killer fixture (expanded stress set)
- Reports available in `docs/` include baseline and post-fix batch runs.
- Trend from reports:
  - reliability improved significantly after phase 93.1 (timeouts down, completion up),
  - quality bottlenecks remain in citation depth and confidence shaping on stress prompts.
- CI thresholds exist but are not yet anchored to the full killer-v2 suite.

## 9) File-Level Evidence (Compact)

| Area | Status | Key Files | Evidence |
|---|---|---|---|
| Routing | Implemented | `core/semantic/intent_classifier.py` | guards + overrides + translit hints |
| Fast path | Implemented | `core/fast_path/fast_path.py`, `apps/api/routes/agent.py` | micro-fast + trust block |
| Standard task | Implemented | `orchestration/engine.py`, `core/controller/*` | FSM execution + budget checks |
| Deep research | Partial (strong) | `orchestration/engine.py` | query->search->rank->fallback flow |
| Doc pipeline | Partial (strong) | `core/documents/*`, `apps/api/routes/documents.py` | upload/ask/retrieve stack |
| Trust/citation | Partial (strong) | `orchestration/engine.py` | quality guards + uncertainty blocks |
| Frontend contract | Implemented (contract) | `apps/api/schemas/agent.py`, `apps/api/routes/agent.py` | reflection payload fields |
| Eval harness | Implemented | `scripts/run_intelligence_eval.py`, `core/evaluation/*` | batch eval + reports |
| CI gate | Partial | `.github/workflows/quality-gate.yml`, `docs/eval_thresholds.json` | threshold gate on limited fixture scope |

## 10) Top Gaps Not Yet Done

1. Fix deep-research synthesis success-path control-flow edge and lock with regression test.
2. Reduce over-misrouting to fast path for lookup/research-ish prompts.
3. Improve citation quality under noisy/adversarial/high-stakes cases.
4. Improve confidence calibration for weak/conflicting evidence.
5. Fully unify `/execute` doc shortcut path with `DocumentAskService` retrieval behavior.
6. Strengthen freshness handling for date-sensitive/news-like prompts.
7. Add CI gate stage for killer-v2 subset/full suite.
8. Add richer research cache observability and hit/miss diagnostics in normal telemetry.
9. Improve multilingual typo/noisy query normalization before research planner.
10. Complete real frontend hookup in production chat renderer using reflection fields.

## 11) Single Best Next Upgrade

Fix and test the deep-research synthesis success-path control flow in `orchestration/engine.py`, then rerun weak killer slices with CI threshold checks tied to those slices.

## 12) Copy-Paste Review Summary

TAOS already has a mature multi-path architecture (fast, task, deep-research, doc) with strong routing hardening and major reliability gains after Phase 93.1. Core execution and trust scaffolding are implemented, and frontend reflection payload fields are available from backend. Current bottlenecks are mostly quality-layer issues (citation depth, confidence calibration, adversarial/high-stakes wording consistency) plus one critical deep-research success-path cleanup and CI scope expansion to killer-v2 stress coverage. Next best move: patch and regression-test research synthesis success flow, tighten routing for lookup/research prompts, and enforce killer-slice quality thresholds in CI.
