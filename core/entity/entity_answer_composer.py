from __future__ import annotations

from collections import Counter
import re
from typing import Iterable, List

from .entity_evidence_ranker import EntityEvidenceRanker
from .entity_models import EntityAnswer, EntityEvidence, EntityIntent, EntityProfileCandidate, EntityQuery, SOURCE_AUTHORITY
from .profile_discovery import ProfileDiscovery


def _has_explicit_role_bearing_evidence(row: EntityEvidence) -> bool:
    if not row:
        return False
    if not row.role_match:
        return False
    if row.rejection_reason:
        return False
    if row.candidate_name and row.role_holder_detected:
        return True
    if row.role_holder_detected and row.role_applies_to_person:
        return True
    text = " ".join(
        str(value or "")
        for value in (
            row.candidate_name,
            row.role_holder_detected,
            row.extracted_role,
            row.supported_role,
            row.snippet,
            row.title,
        )
    ).strip()
    if not text:
        return False
    if row.company_match and row.target_entity_match and row.role_applies_to_person:
        return True
    return bool((row.candidate_name or row.role_holder_detected) and row.supported_role and row.source_type not in {"search_snippet", "random_social"})


def _row_is_source_link_only_placeholder(row: EntityEvidence) -> bool:
    if not row:
        return False
    if row.extraction_quality > 0:
        return False
    if row.snippet.strip():
        return False
    if row.candidate_name.strip() or row.role_holder_detected.strip():
        return False
    if row.role_applies_to_person:
        return False
    return True


class EntityAnswerComposer:
    def compose(
        self,
        *,
        entity_query: EntityQuery,
        evidence_rows: Iterable[EntityEvidence] = (),
        profile_candidates: Iterable[EntityProfileCandidate] = (),
    ) -> EntityAnswer:
        if not entity_query.has_entity:
            return EntityAnswer(
                mode="unverified_no_answer",
                answer="I need the company or person name before I can verify this public entity question.",
                confidence="Low",
                why="The query did not include a resolvable entity.",
                uncertainty="The target entity is missing.",
                verification_state="not_verified",
                evidence_strength="weak",
                source_agreement="unknown",
                disambiguation_needed=True,
                confidence_reason="The entity name is missing, so no public evidence can be matched safely.",
                sources_checked=(),
            )
        ranked = EntityEvidenceRanker().rank(evidence_rows)
        profiles = ProfileDiscovery().rank_profiles(profile_candidates)
        requested_attribute = str(entity_query.requested_attribute or "").strip().lower()
        if requested_attribute in {"official_website", "website"}:
            return self._official_website_answer(entity_query=entity_query, ranked=ranked)
        if entity_query.intent == EntityIntent.LEGITIMACY_CHECK or requested_attribute in {"legitimacy", "business_legitimacy"}:
            return self._business_legitimacy_answer(entity_query=entity_query, ranked=ranked)
        if entity_query.intent in {EntityIntent.OFFICIAL_SOCIAL_PROFILE, EntityIntent.LINKEDIN_PROFILE}:
            return self._profile_answer(entity_query, profiles, ranked=ranked)
        candidates = [row.candidate_name for row in ranked if row.candidate_name]
        counts = Counter(candidates)
        official_found = any(
            row.source_type in {"official_website", "government_registry", "verified_social_link"}
            and (row.company_match or row.supports_claim or bool(row.candidate_name))
            for row in ranked
        )
        linkedin_found = any(row.source_type in {"company_linkedin", "person_linkedin"} for row in ranked)
        registry_found = any(row.source_type in {"government_registry", "registry_directory"} for row in ranked)
        requested_role = str(entity_query.requested_attribute or "").strip().lower()
        supporting_rows = [row for row in ranked if _has_explicit_role_bearing_evidence(row)]
        mismatched_rows = [row for row in ranked if row.supported_role and not row.role_match]
        conflict_detected = bool(supporting_rows and mismatched_rows)
        supported_role = supporting_rows[0].supported_role if supporting_rows else mismatched_rows[0].supported_role if mismatched_rows else ""
        strongest_source_type = ranked[0].source_type if ranked else ""
        source_tiers_found = tuple(dict.fromkeys(str(row.source_tier or "") for row in ranked if str(row.source_tier or "").strip()))
        search_lanes_used = ("official_website", "linkedin", "registry_directory", "news_articles", "general_web")
        top_distinct = []
        seen_candidates = set()
        for row in ranked:
            candidate = str(row.candidate_name or "").strip()
            if not candidate or candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)
            top_distinct.append(row)
            if len(top_distinct) >= 2:
                break
        ambiguous_top_candidates = len(counts) > 1 and len({count for count in counts.values()}) == 1
        if ambiguous_top_candidates and _should_force_disambiguation(top_distinct):
            return EntityAnswer(
                mode="multiple_candidates",
                answer=f"I found multiple public candidates for {entity_query.requested_attribute or 'this attribute'} of {entity_query.entity_name}.",
                confidence="Low",
                why="Public sources do not agree on one strongest candidate.",
                uncertainty="Do not treat any candidate as confirmed without a stronger official source.",
                verification_state="not_verified",
                candidate_count=len(counts),
                official_source_found=official_found,
                linkedin_source_found=linkedin_found,
                registry_source_found=registry_found,
                requested_role=requested_role,
                supported_role=supported_role,
                role_match=False,
                role_mismatch_reason="multiple_candidates_without_exact_role_consensus",
                conflict_detected=conflict_detected,
                exact_role_verified=False,
                strongest_source_type=strongest_source_type,
                evidence_strength="candidate",
                source_agreement="mixed",
                disambiguation_needed=True,
                confidence_reason="Multiple similarly strong public candidates were found and none clearly outranked the others.",
                search_depth_used="escalated",
                search_lanes_used=search_lanes_used,
                source_tiers_found=source_tiers_found,
                sources_checked=tuple(_source_labels(ranked)),
                alternatives=tuple(counts.keys()),
            )
        best = ranked[0] if ranked else None
        best_support = supporting_rows[0] if supporting_rows else None
        best_mismatch = mismatched_rows[0] if mismatched_rows else None
        selected_row = best_support or best_mismatch or best
        linkedin_supported_public = bool(
            best_support
            and best_support.source_type == "company_linkedin"
            and best_support.role_applies_to_person
            and best_support.company_match
            and not any(
                row.source_type in {"official_website", "government_registry", "verified_social_link"}
                and (row.company_match or row.supports_claim or bool(row.candidate_name))
                and row.supports_claim
                for row in ranked
            )
        )
        if best and best.source_type in {"official_website", "government_registry", "verified_social_link"} and best.supports_claim and best.role_match:
            mode = "verified_entity_fact"
            confidence = "High"
            answer = f"The best-supported public answer is {best.candidate_name or best.snippet or best.title}."
            uncertainty = "Still verify recency if the role may have changed."
            verification_state = "confirmed"
            evidence_strength = "strong"
            agreement = "supported"
            confidence_reason = "An official or source-of-record public source explicitly supports the role claim."
            role_mismatch_reason = ""
            exact_role_verified = True
        elif supporting_rows:
            mode = "best_supported_candidate"
            confidence = "Medium" if best_support and best_support.source_type in {"company_linkedin", "person_linkedin", "reputable_news"} else "Low"
            if linkedin_supported_public:
                answer = (
                    f"The best-supported public evidence points to {best_support.candidate_name or best_support.title} "
                    f"as {best_support.supported_role.replace('_', ' ')} of {entity_query.entity_name}."
                )
                uncertainty = "This is LinkedIn-supported candidate evidence, not source-of-record or registry-confirmed proof."
            else:
                answer = f"The best-supported candidate is {best_support.candidate_name or best_support.title}."
                uncertainty = "This is candidate evidence, not an officially confirmed current fact."
            verification_state = "candidate"
            evidence_strength = "medium" if confidence == "Medium" else "candidate"
            agreement = "mixed" if conflict_detected else "partial"
            confidence_reason = "Public signals point to one candidate, but the role is not confirmed by a stronger official or corroborated source set."
            role_mismatch_reason = ""
            exact_role_verified = False
        elif mismatched_rows:
            mismatch = best_mismatch
            mode = "role_mismatch_not_verified"
            confidence = "Low"
            answer = (
                f"I could not verify that {mismatch.candidate_name or 'this person'} is the {requested_role or 'requested role'} "
                f"of {entity_query.entity_name}."
            )
            uncertainty = "Available public evidence supports a different role, not the requested one."
            verification_state = "not_verified"
            evidence_strength = "weak"
            agreement = "conflicting" if conflict_detected else "role_mismatch"
            confidence_reason = "Available evidence supports a different public role than the one requested, so the exact role is not verified."
            role_mismatch_reason = mismatch.role_mismatch_reason or "role_mismatch"
            exact_role_verified = False
        else:
            mode = "unverified_no_answer"
            confidence = "Low"
            answer = f"I could not verify {entity_query.requested_attribute or 'the requested fact'} for {entity_query.entity_name} from public evidence."
            uncertainty = "No usable public evidence was supplied."
            verification_state = "not_verified"
            evidence_strength = "weak"
            agreement = "unknown"
            confidence_reason = (
                "Only placeholder source links or non-role-bearing evidence survived ranking and safety filtering."
                if any(_row_is_source_link_only_placeholder(row) for row in ranked)
                else "No usable public evidence survived ranking and safety filtering."
            )
            role_mismatch_reason = "no_usable_public_evidence"
            exact_role_verified = False
        evidence_block = _evidence_block(ranked)
        answer_text = _render_entity_answer(
            entity_query=entity_query,
            answer=answer,
            evidence_block=evidence_block,
            confidence=confidence,
            uncertainty=uncertainty,
            verification_state=verification_state,
            supported_role=supported_role,
            requested_role=requested_role,
            role_match=bool(best_support.role_match) if best_support else False,
            role_mismatch_reason=role_mismatch_reason,
        )
        return EntityAnswer(
            mode=mode,
            answer=answer_text,
            confidence=confidence,
            why=_why(ranked),
            uncertainty=uncertainty,
            verification_state=verification_state,
            selected_candidate=selected_row.candidate_name if selected_row else "",
            candidate_count=max(len(counts), 1 if best and best.candidate_name else 0),
            official_source_found=official_found,
            linkedin_source_found=linkedin_found,
            registry_source_found=registry_found,
            requested_role=requested_role,
            supported_role=supported_role,
            role_match=bool(best_support.role_match) if best_support else False,
            role_mismatch_reason=role_mismatch_reason,
            conflict_detected=conflict_detected,
            exact_role_verified=exact_role_verified,
            strongest_source_type=strongest_source_type,
            evidence_strength=evidence_strength,
            source_agreement=agreement,
            disambiguation_needed=mode == "multiple_candidates",
            confidence_reason=confidence_reason,
            search_depth_used="escalated" if requested_role in {"ceo", "founder"} else "standard",
            search_lanes_used=search_lanes_used,
            source_tiers_found=source_tiers_found,
            sources_checked=tuple(_source_labels(ranked)),
            alternatives=tuple(name for name, _ in counts.most_common()[1:]),
        )

    def _profile_answer(
        self,
        entity_query: EntityQuery,
        profiles: List[EntityProfileCandidate],
        *,
        ranked: List[EntityEvidence],
    ) -> EntityAnswer:
        if not profiles:
            linkedin_rows = [row for row in ranked if row.source_type in {"company_linkedin", "person_linkedin", "verified_social_link"}]
            if linkedin_rows:
                best_row = linkedin_rows[0]
                link = str(best_row.url or "").strip()
                label = str(best_row.title or best_row.candidate_name or link or "LinkedIn candidate profile").strip()
                candidate_note = "Multiple candidate profile signals were found; verify the exact company page." if len(linkedin_rows) > 1 else "This is a candidate public profile signal and should be verified."
                return EntityAnswer(
                    mode="profile_link_result",
                    answer=f"The likely profile result for {entity_query.entity_name} is {link or label}.",
                    confidence="Medium",
                    why="Public LinkedIn/profile evidence exists but no high-confidence official-link profile candidate was fully confirmed.",
                    uncertainty=candidate_note,
                    verification_state="candidate",
                    selected_candidate=link or label,
                    candidate_count=len(linkedin_rows),
                    official_source_found=False,
                    linkedin_source_found=True,
                    registry_source_found=False,
                    evidence_strength="candidate",
                    source_agreement="partial",
                    disambiguation_needed=len(linkedin_rows) > 1,
                    confidence_reason="A public LinkedIn/profile signal is present, but source-of-record confirmation is limited.",
                    sources_checked=tuple(_source_labels(linkedin_rows)),
                )
            return EntityAnswer(
                mode="profile_link_result",
                answer=f"I did not find a public profile I can treat as official for {entity_query.entity_name}.",
                confidence="Low",
                why="No safe public candidate profile was supplied.",
                uncertainty="Login-only/private profiles were not used.",
                verification_state="not_verified",
                evidence_strength="weak",
                source_agreement="unknown",
                confidence_reason="No public profile candidate survived the public-only safety filter.",
                sources_checked=(),
            )
        best = profiles[0]
        confidence = "Medium-high" if best.confidence >= 0.82 else "Medium" if best.confidence >= 0.55 else "Low"
        verification_state = "confirmed" if best.confidence >= 0.82 else "candidate"
        confidence_reason = "The candidate profile was ranked using official-link, public verification, handle/name, and domain signals."
        return EntityAnswer(
            mode="profile_link_result",
            answer=f"The likely public {best.platform} profile is {best.handle}.",
            confidence=confidence,
            why="The candidate profile was ranked using official-link, name/handle, and domain-match signals.",
            uncertainty="Do not treat unrelated or login-only profiles as official.",
            verification_state=verification_state,
            selected_candidate=best.handle,
            candidate_count=len(profiles),
            official_source_found=best.linked_from_official_site,
            linkedin_source_found=best.platform == "linkedin",
            registry_source_found=False,
            evidence_strength="strong" if best.confidence >= 0.82 else "candidate",
            source_agreement="supported" if len(profiles) == 1 else "partial",
            disambiguation_needed=len(profiles) > 1 and (len(profiles) == 1 or abs(best.confidence - profiles[1].confidence) < 0.08),
            confidence_reason=confidence_reason,
            sources_checked=(best.url or best.handle,),
            alternatives=tuple(profile.handle for profile in profiles[1:3]),
        )

    def _official_website_answer(self, *, entity_query: EntityQuery, ranked: List[EntityEvidence]) -> EntityAnswer:
        if not ranked:
            return EntityAnswer(
                mode="official_website_result",
                answer=f"I could not find a usable official website signal for {entity_query.entity_name}.",
                confidence="Low",
                why="No usable public website evidence was found.",
                uncertainty="This does not prove there is no website; only that current evidence was insufficient.",
                verification_state="not_verified",
                official_source_found=False,
                linkedin_source_found=False,
                registry_source_found=False,
                evidence_strength="weak",
                source_agreement="unknown",
                confidence_reason="No usable public website evidence survived ranking.",
            )
        website_rows = [row for row in ranked if row.source_type == "official_website"]
        if website_rows:
            best = website_rows[0]
            link = str(best.url or "").strip()
            return EntityAnswer(
                mode="official_website_result",
                answer=f"The likely official website for {entity_query.entity_name} is {link or best.title}.",
                confidence="Medium",
                why="An official-domain style source was ranked highest among public website evidence.",
                uncertainty="Treat this as a likely/candidate official website unless corroborated by additional official signals.",
                verification_state="candidate",
                selected_candidate=link or best.title,
                candidate_count=len(website_rows),
                official_source_found=True,
                linkedin_source_found=any(row.source_type in {"company_linkedin", "person_linkedin"} for row in ranked),
                registry_source_found=any(row.source_type in {"government_registry", "registry_directory"} for row in ranked),
                evidence_strength="candidate",
                source_agreement="partial" if len(website_rows) > 1 else "supported",
                confidence_reason="A direct company-domain style source is present, but strict source-of-record confirmation is still limited.",
                sources_checked=tuple(_source_labels(ranked)),
            )
        fallback = ranked[0]
        return EntityAnswer(
            mode="official_website_result",
            answer=f"I found website candidates for {entity_query.entity_name}, but none can be safely confirmed as the official site yet.",
            confidence="Low",
            why="Only aggregator/directory/general-web evidence was available.",
            uncertainty="This is candidate website evidence, not confirmed official domain proof.",
            verification_state="candidate",
            selected_candidate=str(fallback.url or fallback.title or "").strip(),
            candidate_count=len(ranked),
            official_source_found=False,
            linkedin_source_found=any(row.source_type in {"company_linkedin", "person_linkedin"} for row in ranked),
            registry_source_found=any(row.source_type in {"government_registry", "registry_directory"} for row in ranked),
            evidence_strength="candidate",
            source_agreement="mixed",
            confidence_reason="No official-domain evidence was found; only lower-confidence web candidates exist.",
            sources_checked=tuple(_source_labels(ranked)),
        )

    def _business_legitimacy_answer(self, *, entity_query: EntityQuery, ranked: List[EntityEvidence]) -> EntityAnswer:
        website_found = any(row.source_type == "official_website" for row in ranked)
        linkedin_found = any(row.source_type in {"company_linkedin", "person_linkedin", "verified_social_link"} for row in ranked)
        registry_found = any(row.source_type in {"government_registry", "registry_directory"} for row in ranked)
        public_presence_supported = bool(website_found or linkedin_found or registry_found)
        legal_registration_verified = bool(any(row.source_type == "government_registry" for row in ranked))

        if not ranked:
            return EntityAnswer(
                mode="business_legitimacy_result",
                answer=f"I could not find enough public evidence to assess whether {entity_query.entity_name} has a reliable company presence.",
                confidence="Low",
                why="No usable website/profile/registry evidence was available.",
                uncertainty="This is not legal verification and does not prove non-existence.",
                verification_state="not_verified",
                official_source_found=False,
                linkedin_source_found=False,
                registry_source_found=False,
                evidence_strength="weak",
                source_agreement="unknown",
                confidence_reason="No usable public legitimacy evidence survived ranking.",
            )

        if public_presence_supported:
            legal_line = (
                "Legal/registry verification is supported by source-of-record evidence."
                if legal_registration_verified
                else "Legal registration is not verified from source-of-record registry evidence in this result set."
            )
            answer = (
                f"Public online presence for {entity_query.entity_name} is supported by current evidence. "
                f"{legal_line}"
            )
            return EntityAnswer(
                mode="business_legitimacy_result",
                answer=answer,
                confidence="Medium" if legal_registration_verified else "Low",
                why="Legitimacy was assessed using website/profile/registry evidence instead of role attribution.",
                uncertainty="Public presence support is not the same as legal registration proof unless registry evidence is present.",
                verification_state="candidate",
                official_source_found=website_found,
                linkedin_source_found=linkedin_found,
                registry_source_found=registry_found,
                evidence_strength="candidate",
                source_agreement="partial",
                confidence_reason="At least one public presence source exists; legal verification depends on registry-level evidence.",
                sources_checked=tuple(_source_labels(ranked)),
            )

        return EntityAnswer(
            mode="business_legitimacy_result",
            answer=f"I did not find strong public presence evidence for {entity_query.entity_name} in the current result set.",
            confidence="Low",
            why="Only weak/indirect evidence was available.",
            uncertainty="This does not prove the company is fake; it indicates low-confidence public evidence.",
            verification_state="not_verified",
            official_source_found=False,
            linkedin_source_found=False,
            registry_source_found=False,
            evidence_strength="weak",
            source_agreement="unknown",
            confidence_reason="No strong website/profile/registry evidence was found.",
            sources_checked=tuple(_source_labels(ranked)),
        )


def _why(rows: List[EntityEvidence]) -> str:
    if not rows:
        return "No usable public evidence was available."
    best = rows[0]
    return f"Top evidence source type: {best.source_type}; weak/private/login-only rows are excluded."


def _source_labels(rows: List[EntityEvidence]) -> List[str]:
    return [row.title or row.url or row.source_type for row in rows[:5]]


def _should_force_disambiguation(rows: List[EntityEvidence]) -> bool:
    if len(rows) < 2:
        return False
    best, second = rows[0], rows[1]
    best_score = SOURCE_AUTHORITY.get(best.source_type, 0.3) + (0.1 if best.role_match else 0.0)
    second_score = SOURCE_AUTHORITY.get(second.source_type, 0.3) + (0.1 if second.role_match else 0.0)
    if best.source_type in {"official_website", "government_registry", "verified_social_link"} and best_score >= second_score + 0.1:
        return False
    return abs(best_score - second_score) < 0.08


def _evidence_block(rows: List[EntityEvidence]) -> List[str]:
    items: List[str] = []
    for row in rows[:4]:
        label = row.candidate_name or row.title or row.url or row.source_type
        source = row.source_type.replace("_", " ")
        role = row.supported_role or "unknown role"
        qualifiers: List[str] = [f"supports: {role}"]
        if row.role_holder_detected and row.role_holder_detected != label:
            qualifiers.append(f"role holder: {row.role_holder_detected}")
        if row.company_match:
            qualifiers.append("exact company match")
        if row.rejection_reason:
            qualifiers.append(f"rejected: {row.rejection_reason}")
        items.append(f"- {label} ({source}; {'; '.join(qualifiers)})")
    if not items:
        items.append("- No usable public evidence was available.")
    return items


def _render_entity_answer(
    *,
    entity_query: EntityQuery,
    answer: str,
    evidence_block: List[str],
    confidence: str,
    uncertainty: str,
    verification_state: str,
    supported_role: str,
    requested_role: str,
    role_match: bool,
    role_mismatch_reason: str,
) -> str:
    requested = requested_role or "requested role"
    lines = [
        "Answer",
        answer,
        "",
        "What public evidence supports",
        (
            f"The available evidence supports {supported_role}, not {requested}."
            if supported_role and not role_match
            else "No usable public evidence confirmed the requested role."
            if not supported_role and not role_match
            else f"The available evidence supports the requested role: {requested}."
        ),
        "",
        "Evidence",
        *evidence_block,
        "",
        "Confidence",
        f"{confidence} ({verification_state})",
        "",
        "What this does not prove",
        uncertainty if not role_mismatch_reason else f"{uncertainty} ({role_mismatch_reason})",
        "",
        "Bottom line",
        _bottom_line(
            entity_query=entity_query,
            answer=answer,
            verification_state=verification_state,
            supported_role=supported_role,
            role_match=role_match,
        ),
    ]
    return "\n".join(lines).strip()


def _bottom_line(*, entity_query: EntityQuery, answer: str, verification_state: str, supported_role: str, role_match: bool) -> str:
    attr = entity_query.requested_attribute or "requested fact"
    if verification_state == "confirmed":
        return f"Use this as the current best-supported public answer for {attr}, while still checking recency if leadership may have changed."
    if verification_state == "candidate":
        if re.search(r"linkedin-supported public evidence|best-supported public evidence", answer, flags=re.I):
            return f"Use this as the current best-supported public {attr} candidate, but verify against an official website or registry if you need legal or current source-of-record confirmation."
        return f"Treat this as the strongest public candidate for {attr}, not as a fully verified current fact."
    if supported_role and not role_match:
        return f"Do not treat the person as {attr} unless official or stronger evidence confirms that exact role; current public evidence only supports {supported_role}."
    return f"There is not enough strong public evidence to verify the {attr} confidently yet."
