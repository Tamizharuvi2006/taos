# Phase 133 Messy Query Live QA

- Generated: 2026-04-26T17:18:04.781588+00:00
- Mode: mock
- Total: 6
- Passed: 6
- Failed: 0
- Pass rate: 1.000

| Case | Route | Intent | Relation | OK | Failed checks | Primary query |
| --- | --- | --- | --- | ---: | --- | --- |
| `india_claude_lovking_rumour` | deep_research | rumour_verification | blocked_or_restricted_access | yes | - | Anthropic Claude supported countries India |
| `india_banning_claude` | deep_research | rumour_verification | blocked_or_restricted_access | yes | - | Anthropic Claude supported countries India |
| `claud_not_working_india` | deep_research | rumour_verification | unavailable_or_outage | yes | - | Anthropic Claude supported countries India |
| `openai_blocked_india` | deep_research | rumour_verification | blocked_or_restricted_access | yes | - | OpenAI supported countries India |
| `gemini_down_europe` | deep_research | rumour_verification | unavailable_or_outage | yes | - | Google Gemini supported countries Europe |
| `package_typo_guard` | fast_search | current_lookup | - | yes | - | What is the current status of Vite |

## Gate Checks

- Raw typo-heavy query is preserved for trace/debug but must not be primary search.
- Query plan summary must include normalized question and source-aware lanes.
- Rumour cases must show rumour status, best-supported status, and related/confusion context.
- Package typo guard must keep source-of-record behavior.
