# TAOS Route-Discipline Runtime Evidence Report

Date: 2026-04-30  
Workspace: `D:\agent\taos`  
Report mode: focused repo inspection + simulated API/runtime checks  
Scope note: no new feature work was added for this report. One blocking route-discipline bug was fixed during verification in `core/understanding/route_hint_builder.py` so definition/explanation prompts stop collapsing into `clarification`.

## 1. Current route map

| Route | Owner | Main deciding files/functions | Tools used | Calls LLM | Calls research pipeline | Should appear in frontend trace |
| --- | --- | --- | --- | --- | --- | --- |
| `fast_message` | `direct_fast_message` | `core/understanding/route_hint_builder.py:RouteHintBuilder.build`, `core/routing/route_decider.py:RouteDecider.decide`, `orchestration/engine.py:_resolve_selected_route` | No | Yes, via engine micro-fast/direct path | No | Yes |
| `no_search` | `direct_llm_no_tools` | `core/understanding/route_hint_builder.py:RouteHintBuilder.build`, `core/routing/route_rules.py:deterministic_route`, `orchestration/handlers/no_search_handler.py:NoSearchHandler.handle` | No | Yes | No | Yes |
| `fast_search` | `search_lite` | `core/understanding/route_hint_builder.py:RouteHintBuilder.build`, `orchestration/handlers/fast_search_handler.py:FastSearchHandler.handle`, `orchestration/engine.py:_run_fast_search` | Yes | Yes | Escalates only if verification is weak | Yes |
| `news_search` | `research_pipeline` | `core/understanding/route_hint_builder.py:RouteHintBuilder.build`, `core/routing/route_rules.py:deterministic_route`, `orchestration/handlers/research_handler.py:ResearchHandler.handle` | Yes | Yes | Yes | Yes |
| `deep_search` | `research_pipeline` | `core/routing/route_rules.py:deterministic_route`, `core/routing/route_decider.py:RouteDecider.decide`, `orchestration/handlers/research_handler.py:ResearchHandler.handle` | Yes | Yes | Yes | Yes |
| `official_search` | `research_pipeline` | `core/understanding/route_hint_builder.py:RouteHintBuilder.build`, `core/routing/route_rules.py:deterministic_route`, `orchestration/handlers/research_handler.py:ResearchHandler.handle` | Yes | Yes | Yes | Yes |
| `comparison_search` | `research_pipeline` | `core/understanding/route_hint_builder.py:RouteHintBuilder.build`, `core/routing/route_rules.py:deterministic_route`, `orchestration/handlers/research_handler.py:ResearchHandler.handle` | Yes | Yes | Yes | Yes |
| `entity_lookup` | `entity_lookup_pipeline` | `core/understanding/route_hint_builder.py:RouteHintBuilder.build`, `orchestration/engine.py:_resolve_selected_route`, `orchestration/engine.py:_run_entity_lookup` | Yes | Yes | No generic research pipeline; dedicated entity path | Yes |
| `doc_mode` | `document_pipeline` | `core/understanding/route_hint_builder.py:RouteHintBuilder.build`, `core/routing/route_rules.py:deterministic_route`, `orchestration/handlers/document_handler.py:DocumentHandler.handle` | Yes | Yes | No | Yes |
| `task` | `fsm_planner` | `core/understanding/route_hint_builder.py:RouteHintBuilder.build`, `core/routing/route_rules.py:deterministic_route`, `orchestration/handlers/task_handler.py:TaskHandler.handle`, main FSM loop in `orchestration/engine.py` | Usually yes | Usually yes | No | Yes |
| `clarification` | `clarification_fallback` | `core/understanding/route_hint_builder.py:RouteHintBuilder.build`, `core/routing/route_rules.py:deterministic_route`, `orchestration/handlers/clarification_handler.py:ClarificationHandler.handle` | No | Usually no extra model call for the clarification template | No | Yes |

### Notes
- `RouteDispatcher` maps route owners in [D:\agent\taos\orchestration\route_dispatcher.py](D:\agent\taos\orchestration\route_dispatcher.py).
- `deep_search`, `news_search`, `official_search`, and `comparison_search` all converge into selected route `deep_research` for execution.
- `entity_lookup` is intentionally separate and is guarded in [D:\agent\taos\orchestration\engine.py](D:\agent\taos\orchestration\engine.py) so it no longer collapses into generic deep research.

## 2. Route decision flow

Current execution flow:

1. User input enters the engine in [D:\agent\taos\orchestration\engine.py](D:\agent\taos\orchestration\engine.py), `OrchestrationEngine.run(...)`.
2. Universal understanding runs first in [D:\agent\taos\core\understanding\universal_understanding_gateway.py](D:\agent\taos\core\understanding\universal_understanding_gateway.py), `UniversalUnderstandingGateway.understand(...)`.
3. Inside universal understanding:
   - intent/search shaping starts from `SearchIntentPlanner.plan(...)`
   - entity correction/locking happens during `normalize_for_universal_routes(...)`
   - route hints are produced by [D:\agent\taos\core\understanding\route_hint_builder.py](D:\agent\taos\core\understanding\route_hint_builder.py), `RouteHintBuilder.build(...)`
   - meaning frame is attached via `build_meaning_frame(...)`
4. Route decision runs in [D:\agent\taos\core\routing\route_decider.py](D:\agent\taos\core\routing\route_decider.py), `RouteDecider.decide(...)`.
   - order is: cache -> universal route hint -> deterministic rules -> global hybrid -> heuristic scorer -> tiny llm fallback -> safe default
5. Deterministic rules live in [D:\agent\taos\core\routing\route_rules.py](D:\agent\taos\core\routing\route_rules.py), `deterministic_route(...)` and `safe_default_route(...)`.
6. Semantic classification still runs in parallel support mode via [D:\agent\taos\core\semantic\intent_classifier.py](D:\agent\taos\core\semantic\intent_classifier.py), `IntentClassifier.classify(...)`, then the engine aligns classification with the route using `_apply_route_decision_to_classification(...)`.
7. Execution route and owner are resolved in [D:\agent\taos\orchestration\engine.py](D:\agent\taos\orchestration\engine.py):
   - `_resolve_selected_route(...)`
   - `_route_owner_for_selected_route(...)`
   - `_build_route_boundary_summary(...)`
8. Route owner dispatch runs through [D:\agent\taos\orchestration\route_dispatcher.py](D:\agent\taos\orchestration\route_dispatcher.py), `RouteDispatcher.dispatch(...)`.
9. Route handlers then execute:
   - `FastMessageHandler.handle(...)`
   - `NoSearchHandler.handle(...)`
   - `FastSearchHandler.handle(...)`
   - `ResearchHandler.handle(...)`
   - `DocumentHandler.handle(...)`
   - `TaskHandler.handle(...)`
   - `ClarificationHandler.handle(...)`
10. Final output passes through [D:\agent\taos\orchestration\finalization_pipeline.py](D:\agent\taos\orchestration\finalization_pipeline.py), `FinalizationPipeline.finalize(...)`, which applies meaning-frame authority and drift correction.
11. Public contract normalization happens through [D:\agent\taos\apps\api\response_contract.py](D:\agent\taos\apps\api\response_contract.py), `normalize_contract_payload(...)`.

## 3. Browser prompt QA table

Run mode: simulated through current repo runtime using `UniversalUnderstandingGateway`, `RouteDecider`, `IntentClassifier`, and engine route-owner resolution. This is route/runtime evidence, not a full browser screenshot assertion.

| Prompt | Phase107 route | Selected route | Owner | Answer mode | Tool usage | Trace route | UI-visible output correctness |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `heyy macha` | `fast_message` | `micro_fast` | `direct_fast_message` | direct | no tools | `fast_message` | Correct expected |
| `what is docker` | `no_search` | `no_search` | `direct_llm_no_tools` | direct | no tools | `no_search` | Correct expected |
| `docker na enna` | `no_search` | `no_search` | `direct_llm_no_tools` | direct | no tools | `no_search` | Correct expected |
| `explain useEffect simply` | `no_search` | `no_search` | `direct_llm_no_tools` | direct | no tools | `no_search` | Correct expected |
| `latest OpenAI news today` | `news_search` | `deep_research` | `research_pipeline` | deep | search + research | `deep_research` | Correct expected |
| `fina i got an break up today` | `no_search` | `no_search` | `direct_llm_no_tools` | direct | no tools | `no_search` | Correct expected |
| `who is the founder fo relyce infotech` | `entity_lookup` | `entity_lookup` | `entity_lookup_pipeline` | deep-style entity path | search/entity pipeline | `entity_lookup` | Correct expected |
| `who is the ceo of relyce infotech` | `entity_lookup` | `entity_lookup` | `entity_lookup_pipeline` | deep-style entity path | search/entity pipeline | `entity_lookup` | Correct expected |
| `do it` | `clarification` | `clarification` | `clarification_fallback` | standard | no tools | `standard_task` in current public trace mapping | Route correct; trace label mismatch remains |
| `current vite version` | `fast_search` | `fast_search` | `search_lite` | standard | search lite | `fast_search` | Correct expected |
| `india blocking claude rumour` | `news_search` | `deep_research` | `research_pipeline` | deep | search + research | `deep_research` | Correct expected |
| `react vs angular which better` | `comparison_search` | `deep_research` | `research_pipeline` | deep | search + research | `deep_research` | Correct expected |
| `from this pdf give important 16 marks` | `doc_mode` | `doc_mode` | `document_pipeline` | standard | document pipeline | `standard_task` in current public trace mapping | Route correct; trace label mismatch remains |

### Browser/API path notes
- `clarification` currently resolves to public trace label `standard_task` because of `_route_label_from_selected_route(...)` in [D:\agent\taos\orchestration\engine.py](D:\agent\taos\orchestration\engine.py).
- `doc_mode` also surfaces as `standard_task` in that same mapping for some public-facing trace summaries.
- These are trace-label/UI-sync issues, not primary route-decision failures.

## 4. Trace samples

Safe public trace shapes below are derived from the current response contract and finalization pipeline. These are representative public-safe fields only.

### fast_message
```json
{
  "request_id": "req_fast_message",
  "route_label": "fast_message",
  "planner_path": "direct_fast_message",
  "fallback_used": false,
  "timing": {
    "total_ms": 0.0
  }
}
```

### no_search
```json
{
  "request_id": "req_no_search",
  "route_label": "no_search",
  "planner_path": "no_search",
  "meaning_frame": {
    "user_intent": "general_research",
    "route_hint": "no_search"
  },
  "route_boundary_summary": {
    "route": "no_search",
    "owner": "direct_llm_no_tools",
    "web_search_allowed": false,
    "research_allowed": false
  }
}
```

### fast_search
```json
{
  "request_id": "req_fast_search",
  "route_label": "fast_search",
  "planner_path": "fast_search",
  "dag_name": "search_lite",
  "route_boundary_summary": {
    "route": "fast_search",
    "owner": "search_lite",
    "web_search_allowed": true,
    "research_allowed": false
  },
  "timing": {
    "total_ms": 0.0
  }
}
```

### entity_lookup
```json
{
  "request_id": "req_entity_lookup",
  "route_label": "entity_lookup",
  "planner_path": "entity_lookup",
  "query_kind": "entity_lookup",
  "verification_state": "not_verified",
  "policy_reason": "entity_lookup_no_verified_candidates",
  "route_boundary_summary": {
    "route": "entity_lookup",
    "owner": "entity_lookup_pipeline",
    "web_search_allowed": true,
    "research_allowed": true
  }
}
```

### news_search
```json
{
  "request_id": "req_news_search",
  "route_label": "news_search",
  "planner_path": "deep_research",
  "dag_name": "research_v2",
  "meaning_frame": {
    "user_intent": "rumour_verification",
    "route_hint": "news_search"
  },
  "route_boundary_summary": {
    "route": "news_search",
    "owner": "research_pipeline",
    "web_search_allowed": true,
    "research_allowed": true
  }
}
```

## 5. Misroute protection checks

Where the repo currently blocks bad routing:

- Greetings entering research
  - [D:\agent\taos\core\understanding\route_hint_builder.py](D:\agent\taos\core\understanding\route_hint_builder.py)
  - `RouteHintBuilder.build(...)` -> `fast_message`
  - [D:\agent\taos\core\routing\route_rules.py](D:\agent\taos\core\routing\route_rules.py)
  - `deterministic_route(...)` -> `fast_message`

- Stable explanations entering search
  - [D:\agent\taos\core\understanding\route_hint_builder.py](D:\agent\taos\core\understanding\route_hint_builder.py)
  - explanation signal now resolves to `no_search` before weak entity fallback
  - verified on `what is docker`, `docker na enna`, `explain useEffect simply`

- Emotional/support messages entering research
  - [D:\agent\taos\core\routing\route_rules.py](D:\agent\taos\core\routing\route_rules.py)
  - [D:\agent\taos\core\understanding\route_hint_builder.py](D:\agent\taos\core\understanding\route_hint_builder.py)
  - [D:\agent\taos\core\semantic\intent_classifier.py](D:\agent\taos\core\semantic\intent_classifier.py)
  - personal-support regex forces non-research path

- Entity lookup collapsing into generic deep research
  - [D:\agent\taos\orchestration\engine.py](D:\agent\taos\orchestration\engine.py)
  - `_resolve_selected_route(...)`
  - `_route_owner_for_selected_route(...)`
  - `_run_entity_lookup(...)`
  - `ENTITY_LOOKUP_V1_ENABLED=true` path verified

- Ambiguous commands becoming random tool tasks
  - [D:\agent\taos\core\understanding\route_hint_builder.py](D:\agent\taos\core\understanding\route_hint_builder.py)
  - low-context/low-signal short commands return `clarification`
  - verified on `do it`

- Typo-heavy rumour queries using raw messy text as the primary search
  - [D:\agent\taos\core\understanding\universal_understanding_gateway.py](D:\agent\taos\core\understanding\universal_understanding_gateway.py)
  - entity correction through `normalize_for_universal_routes(...)`
  - meaning-frame authority through [D:\agent\taos\orchestration\finalization_pipeline.py](D:\agent\taos\orchestration\finalization_pipeline.py)
  - search planning and evidence gates already hardened in earlier phases

## 6. Tool usage audit

| Prompt | tools_called | search_called | research_called | entity_pipeline_called | doc_pipeline_called | fsm_called |
| --- | --- | --- | --- | --- | --- | --- |
| `heyy macha` | false | false | false | false | false | false |
| `what is docker` | false | false | false | false | false | false |
| `docker na enna` | false | false | false | false | false | false |
| `explain useEffect simply` | false | false | false | false | false | false |
| `latest OpenAI news today` | true | true | true | false | false | false |
| `fina i got an break up today` | false | false | false | false | false | false |
| `who is the founder fo relyce infotech` | true | true | false | true | false | false |
| `who is the ceo of relyce infotech` | true | true | false | true | false | false |
| `do it` | false | false | false | false | false | false |
| `current vite version` | true | true | false | false | false | false |
| `india blocking claude rumour` | true | true | true | false | false | false |
| `react vs angular which better` | true | true | true | false | false | false |
| `from this pdf give important 16 marks` | true | false | false | false | true | false |

## 7. Performance and latency snapshot

Method: 15-sample focused `RouteDecider.decide(...)` timings with `PYTHONPATH=D:\agent`. This is route-decision latency, not full provider/research execution latency.

| Route bucket | Probe prompt | p50 ms | p95 ms | Samples |
| --- | --- | ---: | ---: | ---: |
| `fast_message` | `heyy macha` | 5.02 | 6.73 | 15 |
| `no_search` | `what is docker` | 7.57 | 10.07 | 15 |
| `fast_search` | `current vite version` | 9.55 | 11.15 | 15 |
| `entity_lookup` | `who is the ceo of relyce infotech` | 17.03 | 19.71 | 15 |
| `news_search` | `latest OpenAI news today` | 22.07 | 31.12 | 15 |
| `deep_search` | `research AI job market India 2026` | 13.43 | 17.72 | 15 |

### Latency note
- These numbers show the router/understanding layer is fast.
- They do **not** measure real web/provider latency for research/entity/doc execution.

## 8. Remaining known issues

- Firebase/admin persistence fallback
  - Local runtime still logs `firebase.not_installed` and uses memory fallback.
  - This affects persistence/history realism in browser smoke.

- Local memory fallback
  - Some local smoke behaviors do not represent production-backed persistence.

- Auth/dev-bypass limitations
  - Dev bypass is improved, but local browser smoke still depends on `AUTH_ALLOW_DEV_BYPASS=true` and can diverge from production auth flows.

- Frontend/browser mismatch
  - Public trace labeling is not perfectly aligned for all routes.
  - `clarification` and `doc_mode` can surface as `standard_task` in some public-facing trace labels.

- API route correct but visible UI answer may still be wrong
  - Route discipline is much better now, but answer quality inside `research_pipeline` and `entity_lookup` still depends on real provider/search evidence.
  - The route can be correct while the final content is weak if local search/provider quality is weak.

- Trace missing or misleading
  - Current public trace route mapping is slightly misleading for `clarification` and `doc_mode`.
  - This is a frontend-trace/UI-sync issue rather than a primary route-decision failure.

- Classification metadata not always visually aligned with final route
  - Some prompts still show classifier metadata like `standard_task` or `deep_research` even when the final route owner and selected route are correct.
  - Example buckets: `what is docker`, `current vite version`, `india blocking claude rumour`.

## 9. Regression results

### Command used
```powershell
$env:PYTHONPATH='D:\agent'
& 'C:\Users\aruvi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest -q tests/test_phase107_deterministic_routing.py tests/test_phase95_entity_lookup.py tests/test_semantic.py
```

### Result
- Passed: `149 passed`
- Warnings: `2`
  - event loop deprecation from test setup
  - pytest cache permission warning in local Windows environment
- Failed tests: `0`

### Files changed during this verification pass
- `core/understanding/route_hint_builder.py`
- `tests/test_phase107_deterministic_routing.py`
- `DEVELOPMENT_LOG.md`

## 10. Final recommendation

### Verdict
TAOS now behaves much more like a needed-tool hybrid agent at the routing layer.

### Best next work
**B. frontend trace/UI sync**

Reason:
- Primary route discipline is now materially improved and backed by focused regression evidence.
- The biggest visible mismatch left is that some correct routes still show slightly misleading public trace/UI labels, especially:
  - `clarification` -> `standard_task`
  - `doc_mode` -> `standard_task`
- After that, the next strongest candidate is **D. research answer quality** for improving content quality when the route is already correct.

### Recommendation order
1. **B. frontend trace/UI sync**
2. **D. research answer quality**
3. **C. Firebase production persistence**
4. **E. entity intelligence real-provider integration**
5. **F. performance/cost optimization**

### Bottom line
Do not rebuild TAOS into a fully tool-based agent. The current architecture is correct. The evidence says the next leverage point is making the visible trace/UI faithfully reflect the now-correct backend route decisions.
