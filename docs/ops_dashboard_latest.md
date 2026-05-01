# TAOS Operational Dashboard

Generated: 2026-04-30T18:01:42.930845+00:00

## Quality

- Intelligence eval score: 0.712
- Research eval score: 0.922
- Research pass rate: 1.0
- Feedback records: 0

## Route Health

| Route | Owner | Count | P50 ms | P95 ms | Error rate | Fallback rate | Avg cost USD | Coverage avg |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| deep_research | unknown | 4 | None | None | 0.0 | 0.0 | 0.0 |  |
| deep_search | research_pipeline | 10 | 8695.0 | 8824.75 | 0.0 | 0.2 | 0.01 |  |
| doc_mode | document_pipeline | 7 | 2513.5 | 2612.5 | 0.0 | 0.0 | 0.003429 |  |
| entity_lookup | entity_lookup_pipeline | 8 | 3313.5 | 3425.25 | 0.0 | 0.0 | 0.009 |  |
| fast_message | direct | 10 | 375.0 | 504.75 | 0.0 | 0.0 | 0.004 |  |
| fast_search | search_lite | 20 | 1188.5 | 1592.25 | 0.0 | 0.0 | 0.007 |  |
| news_search | research_pipeline | 8 | 5313.5 | 5425.25 | 0.0 | 0.0 | 0.01 |  |
| no_search | direct_llm_no_tools | 10 | 1495.0 | 1624.75 | 0.0 | 0.0 | 0.004 |  |
| official_search | research_pipeline | 10 | 7895.0 | 8024.75 | 0.0 | 0.2 | 0.01 |  |
| standard_task | unknown | 1 | None | None | 0.0 | 0.0 | 0.0 |  |

## Provider Health

| Provider | State | Failures | Fallbacks | Cache hits | Source unavailable |
| --- | --- | ---: | ---: | ---: | ---: |
| firebase | unknown | 0 | 0 | 0 | 0 |
| npm_registry | unknown | 0 | 0 | 0 | 0 |
| openrouter | unknown | 0 | 0 | 0 | 0 |
| serper | unknown | 0 | 0 | 0 | 0 |
| web_extract | unknown | 0 | 0 | 0 | 0 |

## Research Quality

- Average coverage: None
- Low coverage count: 0
- Average freshness: None
- Conflict detected count: 0
- Unsupported critical claims: 0
- Source-of-record unavailable count: 0

## Usage / Cost

- LLM calls: 82
- Search calls: 92
- Extract calls: 84
- Package registry calls: 0
- Cache hits: 10
- Estimated cost USD: 0.596
- Budget exceeded count: 0

### Slowest Routes

| Route | Owner | P95 ms | Avg cost USD | Budget exceeded rate |
| --- | --- | ---: | ---: | ---: |
| deep_search | research_pipeline | 8824.75 | 0.01 | 0.0 |
| official_search | research_pipeline | 8024.75 | 0.01 | 0.0 |
| news_search | research_pipeline | 5425.25 | 0.01 | 0.0 |
| entity_lookup | entity_lookup_pipeline | 3425.25 | 0.009 | 0.0 |
| doc_mode | document_pipeline | 2612.5 | 0.003429 | 0.0 |

### Route Budget Matrix

| Route | Owner | Tools | Research | P95 Budget ms | Max cost USD | Max search | Max extract |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| clarification | clarification_fallback | False | False | 1200 | 0.003 | 0 | 0 |
| comparison_search | research_pipeline | True | True | 15000 | 0.05 | 6 | 6 |
| deep_search | research_pipeline | True | True | 16000 | 0.04 | 5 | 6 |
| doc_mode | document_pipeline | True | False | 5000 | 0.015 | 0 | 0 |
| entity_lookup | entity_lookup_pipeline | True | False | 6000 | 0.018 | 3 | 3 |
| fast_message | direct | False | False | 1000 | 0.004 | 0 | 0 |
| fast_search | search_lite | True | False | 1500 | 0.01 | 2 | 1 |
| news_search | research_pipeline | True | True | 12000 | 0.045 | 5 | 6 |
| no_search | direct_llm_no_tools | False | False | 3000 | 0.006 | 0 | 0 |
| official_search | research_pipeline | True | True | 18000 | 0.035 | 4 | 5 |
| package_source_of_record | search_lite | True | False | 1200 | 0.001 | 0 | 0 |
| task | fsm_executor | True | False | 10000 | 0.06 | 3 | 3 |

### Answer Modes

- none recorded

## Latency

- p50 ms: 2429.0
- p90 ms: 8618.9
- p95 ms: 8709.4
- Slowest route: deep_search
- Slowest stage: None

## External Readiness

- error_monitoring_dashboard: external_required
- frontend_workspace_render_check: external_required
- local_intelligence_eval: available
- local_research_eval: available
- provider_cost_dashboard: external_required
- target_deployment_validation: external_required

## Routing Eval

- Route telemetry cases: 88
- Route match rate: not available
- Route mismatches: 0
- Fallback count: 0

## Recommendations

- Tune confidence mapping weights: agreement boost, conflict/stale penalties, high-stakes official-source downgrade.
- Increase claim-level citation coverage and ensure at least top claims include [S#] markers with source cards.
