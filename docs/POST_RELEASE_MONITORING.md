# Post-release Monitoring

Track the first production window for intelligence and operations regressions.

## Daily Checks

- Unknown route count
- Low coverage answers
- Generic fallback answers
- Source unavailable errors
- Rate limit events
- Slow first visual load
- Bad user feedback
- Admin dashboard errors
- Document QA failures

## Bugfix Queue Rules

- Group by route, intent, answer mode, and source quality.
- Prioritize `didnt_understand`, generic fallback, and low coverage research cases.
- Do not auto-change model behavior from feedback without review.
- Release owner decides hotfix versus scheduled patch.
