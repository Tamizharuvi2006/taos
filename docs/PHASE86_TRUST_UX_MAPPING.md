# Phase 86 Trust UX Mapping

This mapping defines how frontend can render trust signals from `/execute` trace payload.

## Trace Inputs

Use fields from:

- `response.trace.pipeline_stages`
- `response.trace.trust_block`
- `response.trace.freshness_check`

Primary trust fields:

- `freshness`
- `agreement`
- `signal`
- `extraction_quality`
- `domain_diversity`
- `official_source_required`
- `official_source_found`
- `usable_sources_count`
- `rejected_sources_count`
- `uncertainty_flags[]`

## Badge Mapping

1. Freshness badge
- Value: `trust_block.freshness`
- Tone:
  - `High` -> success
  - `Medium` -> warning
  - `Failed` -> danger
  - else -> neutral

2. Agreement badge
- Value: `trust_block.agreement` (`high|medium|low|unknown`)
- Tone:
  - `high` -> success
  - `medium` -> warning
  - `low|unknown` -> neutral

3. Signal/conflict badge
- Value: `trust_block.signal` (`clean|partial_conflict|conflicting`)
- Label:
  - `clean` -> Clean
  - `partial_conflict` -> Partial conflict
  - `conflicting` -> Conflicting
- Tone:
  - clean -> success
  - partial_conflict -> warning
  - conflicting -> danger

4. Extraction quality badge
- Value: `trust_block.extraction_quality` (0.0 to 1.0)
- Suggested label:
  - `>= 0.75` -> High
  - `>= 0.5` -> Moderate
  - else -> Low

5. Domain diversity badge
- Value: `trust_block.domain_diversity` (0.0 to 1.0)
- Suggested label:
  - `>= 0.65` -> Broad
  - `>= 0.4` -> Medium
  - else -> Narrow

6. Official-source badge (when required)
- Render only when `official_source_required == true`.
- Value:
  - `official_source_found == true` -> Official source found
  - else -> Official source missing
- Tone:
  - found -> success
  - missing -> warning

## Pipeline Row

Render `trace.pipeline_stages` as compact chips:

`Query -> Search -> Rank -> Extract -> Synthesize -> Trust`

Fallback/direct paths may emit:

`Query -> Generate -> Trust`

## Source Counters

Display:

- `usable_sources_count`
- `rejected_sources_count`
- `source_count` (candidate count after rank+dedupe)

## Uncertainty Box

Show an uncertainty callout when any of these hold:

- `signal` in `{partial_conflict, conflicting}`
- `stale_detected == true`
- `agreement` in `{low, unknown}`
- `uncertainty_flags` non-empty

Suggested content:

- Summary line from flags (humanized)
- Optional `freshness_check.note` when available
- Keep tone calm and informative
