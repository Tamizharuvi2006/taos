# TAOS Partial Implementation + Needs To Implement

Date: 2026-04-18  
Purpose: Execution-focused tracker of what is partially implemented and what must be completed next.

## A) Deep Research Pipeline
Status: Partial

### Already implemented
- Query planning/generation and sanitization.
- Search + extract + synthesis stage timeout caps.
- Partial-evidence and unverified fallback responses.
- Trust metadata generation (freshness/agreement/conflict hints).

### Needs to implement next
1. Fix synthesis success-path control flow edge in `orchestration/engine.py`.
2. Add regression test that proves successful synthesis path returns normal answer (not fallback).
3. Add typo/noisy-query normalization before deep research query expansion.
4. Improve fallback selection hierarchy for sparse but credible evidence.

## B) Fast Path / Routing Integrity
Status: Partial (safety improved, still tuning quality)

### Already implemented
- Adversarial fast-path guard patterns.
- Low-confidence fast-message block.
- Tamil-transliteration routing boost.

### Needs to implement next
1. Add stronger lookup-detection guard (`CEO`, profile, official page, LinkedIn-style asks) to force `standard_task`/`deep_research`.
2. Add normalization of misspelled “research” variants before intent scoring.
3. Add route telemetry counters to compare intent vs final mode in CI and eval reports.

## C) Trust / Citation / Confidence
Status: Partial

### Already implemented
- Uncertainty guard language for high-stakes weak-signal answers.
- Adversarial integrity refusal reinforcement.
- Citation/authority quality blocks.

### Needs to implement next
1. Tighten citation depth policy in weak/conflicting evidence answers.
2. Improve confidence calibration formula for low-evidence + high-stakes combinations.
3. Add explicit source-strength weighting (official vs secondary vs unknown) into trust score.

## D) Document Pipeline Integration
Status: Partial

### Already implemented
- Document upload/process/status endpoints.
- Retrieval and ask service with grounding rules.
- Hybrid chunk retrieval.

### Needs to implement next
1. Unify `/execute` document shortcut behavior with `DocumentAskService` logic.
2. Ensure one consistent answer-shaping contract for both doc and non-doc responses.
3. Add eval cases that compare doc shortcut vs doc ask path output parity.

## E) Frontend Reflection Hookup
Status: Partial (backend contract done, final app hookup pending in frontend repo path)

### Already implemented
- Backend response fields:
  - `trust_badges`
  - `uncertainty_box`
  - `answer_sections`
  - `source_cards`
  - `frontend_hints`
- Route-layer enrichers and API tests for reflection payload.

### Needs to implement next
1. Wire renderer in actual frontend chat message card.
2. Ensure fallback rendering when each field is missing/null.
3. Add end-to-end UI checks for high-stakes/adversarial/ambiguous/tamil-mix scenarios.

## F) Evaluation + CI Gate
Status: Partial

### Already implemented
- Eval runners for intelligence and research.
- Batch slicing support.
- Threshold checker and CI gate pipeline.

### Needs to implement next
1. Enforce a killer-v2 quality gate in CI (or at least critical subset).
2. Add route-integrity assertions in CI (e.g., adversarial prompts must not be `fast_message`).
3. Publish compact trend artifact per run (before/after completion + score + weak cases).

## Priority Order (Recommended)

1. Deep-research synthesis control-flow fix + regression test.
2. Routing normalization for misspell/noisy lookup prompts.
3. Citation/confidence quality tuning for high-stakes weak-signal.
4. Frontend actual chat-card hookup for reflection fields.
5. CI expansion to killer-v2 quality gate subset.

## Done Definition For This Tracker

- No synthesis-path control-flow regressions.
- Lookup/research prompts with typos route correctly.
- High-stakes weak-signal answers show stronger citation + confidence behavior.
- Frontend visibly renders trust/uncertainty/sections/sources/hints.
- CI blocks regressions on selected killer-v2 cases.
