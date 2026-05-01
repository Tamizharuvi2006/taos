# TAOS Performance Load Test

- Generated: 2026-04-30T18:00:17.228070+00:00
- Mode: `mock`
- Total Requests: **82**
- Passed: **82**
- Failed: **0**
- Error Rate: **0.0**
- Latency P50/P90/P95 (ms): **2429.0 / 8618.9 / 8709.4**
- Reliability Gate: **passed**
- Latency Gate: **passed**
- Warm-up Requests Excluded: **9**

## Reliability Details

| Metric | Value |
| --- | ---: |
| `error_rate` | 0.0 |
| `error_rate_budget` | 0.02 |
| `allow_rate_limited_failures` | False |
| `rate_limited_failures` | 0 |
| `unknown_route_failures` | 0 |
| `auth_failures` | 0 |
| `raw_internal_errors` | 0 |
| `contract_failures` | 0 |
| `route_mismatch_failures` | 0 |
| `non_200_failures` | 0 |

## Route Metrics

| Route | Owner | Count | P50 ms | P95 ms | Avg cost USD | Fallback Rate | Error Rate |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `deep_search` | `research_pipeline` | 10 | 8695.0 | 8824.75 | 0.01 | 0.2 | 0.0 |
| `doc_mode` | `document_pipeline` | 6 | 2513.5 | 2612.5 | 0.004 | 0.0 | 0.0 |
| `entity_lookup` | `entity_lookup_pipeline` | 8 | 3313.5 | 3425.25 | 0.009 | 0.0 | 0.0 |
| `fast_message` | `direct` | 10 | 375.0 | 504.75 | 0.004 | 0.0 | 0.0 |
| `fast_search` | `search_lite` | 20 | 1188.5 | 1592.25 | 0.007 | 0.0 | 0.0 |
| `news_search` | `research_pipeline` | 8 | 5313.5 | 5425.25 | 0.01 | 0.0 | 0.0 |
| `no_search` | `direct_llm_no_tools` | 10 | 1495.0 | 1624.75 | 0.004 | 0.0 | 0.0 |
| `official_search` | `research_pipeline` | 10 | 7895.0 | 8024.75 | 0.01 | 0.2 | 0.0 |

## Case Budgets

| Case | Route | Owner | Concurrency | P95 ms | Budget ms | Avg cost USD | Successful Samples | Budget Passed |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fast_message` | `fast_message` | `direct` | 5 | 504.75 | 1000 | 0.004 | 10 | yes |
| `package_vite_cached` | `fast_search` | `search_lite` | 5 | 1004.75 | 1500 | 0.007 | 10 | yes |
| `no_search_explain` | `no_search` | `direct_llm_no_tools` | 3 | 1624.75 | 3000 | 0.004 | 10 | yes |
| `official_search` | `official_search` | `research_pipeline` | 2 | 8024.75 | 18000 | 0.01 | 10 | yes |
| `entity_lookup_company_role` | `entity_lookup` | `entity_lookup_pipeline` | 2 | 3425.25 | 6000 | 0.009 | 8 | yes |
| `news_search_latest` | `news_search` | `research_pipeline` | 2 | 5425.25 | 12000 | 0.01 | 8 | yes |
| `doc_mode_short` | `doc_mode` | `document_pipeline` | 1 | 2612.5 | 5000 | 0.004 | 6 | yes |
| `deep_research_small` | `deep_search` | `research_pipeline` | 2 | 8824.75 | 16000 | 0.01 | 10 | yes |

## Route Budget Matrix

| Route | Owner | Tools | Research | P95 Budget ms | Max cost USD | Max search | Max extract |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| `clarification` | `clarification_fallback` | False | False | 1200 | 0.003 | 0 | 0 |
| `comparison_search` | `research_pipeline` | True | True | 15000 | 0.05 | 6 | 6 |
| `deep_search` | `research_pipeline` | True | True | 16000 | 0.04 | 5 | 6 |
| `doc_mode` | `document_pipeline` | True | False | 5000 | 0.015 | 0 | 0 |
| `entity_lookup` | `entity_lookup_pipeline` | True | False | 6000 | 0.018 | 3 | 3 |
| `fast_message` | `direct` | False | False | 1000 | 0.004 | 0 | 0 |
| `fast_search` | `search_lite` | True | False | 1500 | 0.01 | 2 | 1 |
| `news_search` | `research_pipeline` | True | True | 12000 | 0.045 | 5 | 6 |
| `no_search` | `direct_llm_no_tools` | False | False | 3000 | 0.006 | 0 | 0 |
| `official_search` | `research_pipeline` | True | True | 18000 | 0.035 | 4 | 5 |
| `package_source_of_record` | `search_lite` | True | False | 1200 | 0.001 | 0 | 0 |
| `task` | `fsm_executor` | True | False | 10000 | 0.06 | 3 | 3 |

## Cache Warm vs Cold

| Case | Cold Avg ms | Warm Avg ms | Improvement ms |
| --- | ---: | ---: | ---: |
| `package_vite_cached` | 1477.7 | 887.7 | 590.0 |
