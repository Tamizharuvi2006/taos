# Known Issues

## Reliability
- Some live research paths still depend on external provider latency and can degrade under slow search/extract/synthesis conditions.
- Streaming and non-streaming paths are much closer now, but a few legacy response-shape differences may still surface in less common paths.

## Evidence Quality
- Claim-level citation support is stronger in research paths now, but non-research factual answers still need the same strict grounding behavior.
- Confidence calibration is improved, but still depends on external provider quality when search snippets or extraction quality are weak.

## Search And Deep Research
- `fast_search` and `deep_research` are now split more clearly, but route heuristics still need broader eval coverage on messy multilingual/adversarial follow-ups.
- Deep research still depends on external search and extraction services; provider instability can degrade freshness, extraction depth, or source diversity.
- No-result handling is safer now, but some niche entities/topics may still need better disambiguation prompts or broader trusted-source coverage.

## Docs And Contract
- Historical docs and dev-log entries still contain older status snapshots; the new current-state docs should be treated as the authoritative overview.
- Some older API examples in historical documents reflect pre-unification payloads.

## Frontend
- The repo contains limited frontend build infrastructure in the current workspace snapshot, so CI uses a conditional frontend build step rather than a guaranteed app build.

## Ops
- Docker and CI artifacts are now present in-repo, but they still need full live deployment validation in the target environment.
- Local operational dashboard artifacts are now generated from eval reports, but provider-backed cost dashboards and external error/latency monitoring still need target-environment integration.
