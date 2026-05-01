# Phase 147 Live Provider Evidence QA

- Generated: 2026-05-01T07:15:54.506049+00:00
- Mode: live
- Total: 10
- Passed: 5
- Failed: 5
- Pass rate: 0.500

## Live blockers
- <urlopen error [WinError 10061] No connection could be made because the target machine actively refused it>
- [WinError 10054] An existing connection was forcibly closed by the remote host

## Provider diagnostics
- search_provider_ready: `True`
- search_provider_name: `serper`
- extract_provider_ready: `True`
- external_network_ready: `True`
- provider_mode: `live`
- last_provider_error_safe: `scrapling_normalized_unsuccessful status=200 text_len=0 quality=0.00 rejection=text_too_short`

| Case | Route | Owner | Answer mode | Exact verified | OK |
| --- | --- | --- | --- | --- | ---: |
| `relyce_ceo_role_truth` | entity_lookup | entity_lookup_pipeline | best_supported_candidate | no | yes |
| `relyce_founder_role_truth` | entity_lookup | entity_lookup_pipeline | role_mismatch_not_verified | no | yes |
| `relyce_official_site` | entity_lookup | entity_lookup_pipeline | best_supported_candidate | no | yes |
| `relyce_linkedin` | entity_lookup | entity_lookup_pipeline | unverified_no_answer | no | no |
| `relyce_legitimacy` | entity_lookup | entity_lookup_pipeline | role_mismatch_not_verified | no | yes |
| `known_ceo_official_company` | entity_lookup | entity_lookup_pipeline | verified_entity_fact | yes | yes |
| `known_founder_company` | entity_lookup | entity_lookup_pipeline | unverified_no_answer | no | no |
| `unknown_local_company_weak` | - | - | - | no | no |
| `claude_india_rumour` | - | - | - | no | no |
| `claude_supported_countries_official` | - | - | - | no | no |

## Detailed results

### relyce_ceo_role_truth
- Query: `who is the ceo of relyce infotech`
- Route: `entity_lookup`
- Owner: `entity_lookup_pipeline`
- Answer mode: `best_supported_candidate`
- Exact claim verified: `False`
- Role match: `True`
- Source tiers found: `tier1, tier3, tier2`
- Domains returned: `amarinfotech.com, in.linkedin.com, linkedin.com`
- Failed checks: `-`
- Pass/fail reason: The case correctly stayed unverified and avoided overclaiming.

Answer preview:
```txt
Answer
The best-supported public evidence points to Ukenthiran A as founder ceo of Relyce Infotech.

What public evidence supports
The available evidence supports the requested role: ceo.

Evidence
- Ukenthiran A (company linkedin; supports: founder_ceo; exact company match)
- Hari Balaji (company linkedin; supports: ceo; exact company match)
- Ukenthiran A (search snippet; supports: founder_ceo; exact company match)
- Relyce infotech's Post - LinkedIn (reputable news; supports: founder; exact company match)

Confidence
Medium (candidate)

What this does not prove
This is LinkedIn-supported candidate evidence, not source-of-record or registry-confirmed proof.

Bottom line
Use this as the current best-supported public ceo candidate, but verify against an official website or registry if you need legal or current source-of-record confirmation.

Sources checked:
- Relyce Infotech official website
- Relyce Infotech about team leadership
- Relyce Infotech about us leadership CEO
- Relyce Infotech team founder CEO

Verification policy:
- conflicting role evidence
```

### relyce_founder_role_truth
- Query: `who is the founder of relyce infotech`
- Route: `entity_lookup`
- Owner: `entity_lookup_pipeline`
- Answer mode: `role_mismatch_not_verified`
- Exact claim verified: `False`
- Role match: `False`
- Source tiers found: `tier1`
- Domains returned: `in.linkedin.com, linkedin.com, relyceinfotech.com`
- Failed checks: `-`
- Pass/fail reason: The case correctly stayed unverified and avoided overclaiming.

Answer preview:
```txt
Answer
I could not verify that this person is the founder of Relyce Infotech.

What public evidence supports
The available evidence supports founder, not founder.

Evidence
- Bhogollic | LinkedIn (company linkedin; supports: founder; exact company match)

Confidence
Low (not_verified)

What this does not prove
Available public evidence supports a different role, not the requested one. (no_exact_role_evidence)

Bottom line
Do not treat the person as founder unless official or stronger evidence confirms that exact role; current public evidence only supports founder.

Sources checked:
- Relyce Infotech official website
- Relyce Infotech about team leadership
- Relyce Infotech about us leadership FOUNDER
- Relyce Infotech team founder FOUNDER

Verification policy:
- role mismatch only
```

### relyce_official_site
- Query: `relyce infotech official website`
- Route: `entity_lookup`
- Owner: `entity_lookup_pipeline`
- Answer mode: `best_supported_candidate`
- Exact claim verified: `False`
- Role match: `True`
- Source tiers found: `tier1, tier3, tier2`
- Domains returned: `amarinfotech.com, in.linkedin.com, linkedin.com`
- Failed checks: `-`
- Pass/fail reason: The case matched route, trust metadata, and evidence requirements.

Answer preview:
```txt
Answer
The best-supported public evidence points to Ukenthiran A as founder ceo of Relyce Infotech.

What public evidence supports
The available evidence supports the requested role: requested role.

Evidence
- Ukenthiran A (company linkedin; supports: founder_ceo; exact company match)
- Hari Balaji (company linkedin; supports: ceo; exact company match)
- Ukenthiran A (search snippet; supports: founder_ceo; exact company match)
- Relyce infotech's Post - LinkedIn (reputable news; supports: founder; exact company match)

Confidence
Medium (candidate)

What this does not prove
This is LinkedIn-supported candidate evidence, not source-of-record or registry-confirmed proof.

Bottom line
Use this as the current best-supported public requested fact candidate, but verify against an official website or registry if you need legal or current source-of-record confirmation.

Sources checked:
- Relyce Infotech official website
- Relyce Infotech about team leadership
- Relyce Infotech about us leadership CEO
- Relyce Infotech team founder CEO

Verification policy:
- conflicting role evidence
```

### relyce_linkedin
- Query: `find linkedin of relyce infotech`
- Route: `entity_lookup`
- Owner: `entity_lookup_pipeline`
- Answer mode: `unverified_no_answer`
- Exact claim verified: `False`
- Role match: `False`
- Source tiers found: `-`
- Domains returned: `in.linkedin.com, linkedin.com, relyceinfotech.com, relyceinfotech.in`
- Failed checks: `source_tiers_present`
- Pass/fail reason: Failed checks: source_tiers_present

Answer preview:
```txt
I need the company or person name before I can verify this public entity question.

Sources checked:
- <entity> official website
- <entity> about team leadership
- <entity> about us leadership LINKEDIN_PROFILE
- <entity> team founder LINKEDIN_PROFILE

Verification policy:
- role mismatch only
```

### relyce_legitimacy
- Query: `is relyce infotech real company`
- Route: `entity_lookup`
- Owner: `entity_lookup_pipeline`
- Answer mode: `role_mismatch_not_verified`
- Exact claim verified: `False`
- Role match: `False`
- Source tiers found: `tier1, tier2, tier3`
- Domains returned: `in.linkedin.com, linkedin.com`
- Failed checks: `-`
- Pass/fail reason: The case matched route, trust metadata, and evidence requirements.

Answer preview:
```txt
Answer
I could not verify that this person is the legitimacy of Relyce Infotech.

What public evidence supports
The available evidence supports founder_ceo, not legitimacy.

Evidence
- Relyce infotech - LinkedIn (company linkedin; supports: founder_ceo; exact company match)
- Relyce infotech's Post - LinkedIn (reputable news; supports: founder; exact company match)
- Relyce infotech's Post - LinkedIn (search snippet; supports: founder_ceo; exact company match)
- Madesh M | Unreal Engine 5 & VR Developer | 3D Artist - Blender (search snippet; supports: employee; exact company match)

Confidence
Low (not_verified)

What this does not prove
Available public evidence supports a different role, not the requested one. (supported_role_is_founder_ceo_not_legitimacy)

Bottom line
Do not treat the person as legitimacy unless official or stronger evidence confirms that exact role; current public evidence only supports founder_ceo.

Sources checked:
- Relyce Infotech official website
- Relyce Infotech about team leadership
- Relyce Infotech about us leadership LEGITIMACY
- Relyce Infotech team founder LEGITIMACY

Verification policy:
- role mismatch only
```

### known_ceo_official_company
- Query: `who is the ceo of microsoft`
- Route: `entity_lookup`
- Owner: `entity_lookup_pipeline`
- Answer mode: `verified_entity_fact`
- Exact claim verified: `True`
- Role match: `True`
- Source tiers found: `tier1`
- Domains returned: `blogs.microsoft.com, news.microsoft.com, pr.linkedin.com, se.linkedin.com`
- Failed checks: `-`
- Pass/fail reason: The case verified the exact role from stronger evidence.

Answer preview:
```txt
Answer
The best-supported public answer is Satya Nadella.

What public evidence supports
The available evidence supports the requested role: ceo.

Evidence
- Satya Nadella (official website; supports: ceo; exact company match)
- Satya Nadella (official website; supports: ceo; exact company match)
- Microsoft | LinkedIn (company linkedin; supports: ceo; exact company match)
- ‏Microsoft‏ | LinkedIn (company linkedin; supports: ceo; exact company match)

Confidence
High (confirmed)

What this does not prove
Still verify recency if the role may have changed.

Bottom line
Use this as the current best-supported public answer for ceo, while still checking recency if leadership may have changed.

Sources checked:
- Microsoft official website
- Microsoft about team leadership
- Microsoft about us leadership CEO
- Microsoft team founder CEO

Verification policy:
- official explicit role confirmation
```

### known_founder_company
- Query: `who founded microsoft`
- Route: `entity_lookup`
- Owner: `entity_lookup_pipeline`
- Answer mode: `unverified_no_answer`
- Exact claim verified: `False`
- Role match: `False`
- Source tiers found: `-`
- Domains returned: `cnbc.com, founderandceopod.com, ideaentity.com`
- Failed checks: `source_tiers_present, requested_role_present, exact_role_verified`
- Pass/fail reason: Failed checks: source_tiers_present, requested_role_present, exact_role_verified

Answer preview:
```txt
I need the company or person name before I can verify this public entity question.

Sources checked:
- <entity> official website
- <entity> about team leadership
- <entity> about us leadership CEO
- <entity> team founder CEO

Verification policy:
- single source explicit claim
```

### unknown_local_company_weak
- Query: `who is the ceo of tiny neem labs vellore`
- Route: ``
- Owner: ``
- Answer mode: ``
- Exact claim verified: `False`
- Role match: `False`
- Source tiers found: `-`
- Domains returned: `-`
- Failed checks: `answer_not_empty, route_matches, owner_matches, evidence_matrix_present, source_tiers_present, requested_role_present`
- Pass/fail reason: Failed checks: answer_not_empty, route_matches, owner_matches, evidence_matrix_present, source_tiers_present, requested_role_present

Answer preview:
```txt

```

### claude_india_rumour
- Query: `india blocking claude rumour`
- Route: ``
- Owner: ``
- Answer mode: ``
- Exact claim verified: `False`
- Role match: `False`
- Source tiers found: `-`
- Domains returned: `-`
- Failed checks: `answer_not_empty, route_matches, owner_matches, evidence_matrix_present, source_tiers_present, research_lane_official_present, research_lane_news_present, research_lane_contradiction_present, research_lane_background_present, related_evidence_present`
- Pass/fail reason: Failed checks: answer_not_empty, route_matches, owner_matches, evidence_matrix_present, source_tiers_present, research_lane_official_present, research_lane_news_present, research_lane_contradiction_present, research_lane_background_present, related_evidence_present

Answer preview:
```txt

```

### claude_supported_countries_official
- Query: `official supported countries Claude India`
- Route: ``
- Owner: ``
- Answer mode: ``
- Exact claim verified: `False`
- Role match: `False`
- Source tiers found: `-`
- Domains returned: `-`
- Failed checks: `answer_not_empty, route_matches, owner_matches, evidence_matrix_present, source_tiers_present, research_lane_official_present, official_source_present`
- Pass/fail reason: Failed checks: answer_not_empty, route_matches, owner_matches, evidence_matrix_present, source_tiers_present, research_lane_official_present, official_source_present

Answer preview:
```txt

```
