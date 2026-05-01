# Phase 134 Universal Understanding QA

- Generated: 2026-04-26T17:17:55.123405+00:00
- Mode: mock
- Total: 9
- Passed: 9
- Failed: 0
- Pass rate: 1.000

| Case | Route | Owner | Route hint | OK | Failed checks | Normalized query |
| --- | --- | --- | --- | ---: | --- | --- |
| `fast_message_messy` | fast_message | direct | fast_message | yes | - | hey buddy |
| `no_search_messy` | no_search | direct | no_search | yes | - | explain JavaScript closures simply |
| `package_typo` | fast_search | search_lite | fast_search | yes | - | current vite version |
| `rumour_research` | news_search | research_pipeline | news_search | yes | - | india lovking claude |
| `doc_exam_messy` | doc_mode | document_pipeline | doc_mode | yes | - | in this pdf give important 16 marks |
| `task_reminder_messy` | task | fsm_planner | task | yes | - | remind me tomorrow morning at 8 |
| `code_help_messy` | task | fsm_planner | task | yes | - | fix module not found error in React |
| `comparison_messy` | comparison_search | research_pipeline | comparison_search | yes | - | React vs Angular which better |
| `low_signal_ambiguity` | clarification | clarification | clarification | yes | - | do that thing from before |

## Contract Checks

- Universal understanding frame must appear in trace/public trace.
- Original and normalized query must be preserved.
- Route hint, handler owner, and route must align.
- Package, research, document, task, code, comparison, and clarification paths are locked.
