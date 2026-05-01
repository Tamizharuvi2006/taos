from __future__ import annotations

import unittest

from taos.core.research import ResearchAnswerMode, ResearchPipeline


class Phase108DResearchAnswerUtilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = ResearchPipeline()

    def _assert_useful_answer(self, query: str, rows: list[dict], *, conflict: dict | None = None) -> None:
        quality = self.pipeline.assess_source_quality(rows=rows, query=query, official_source_required=True)
        usable = quality["usable_rows"]
        answer = "The likely answer is grounded in the strongest available source. [S1]"
        citation_plan = self.pipeline.plan_citations(answer=answer, source_rows=usable)
        repaired = self.pipeline.repair_answer(answer=answer, citation_plan=citation_plan)
        repaired_plan = self.pipeline.plan_citations(answer=repaired["answer"], source_rows=usable)
        coverage = self.pipeline.citation_coverage(citation_plan=repaired_plan)
        conflict_summary = conflict or {}
        policy = self.pipeline.answer_policy(
            quality_summary=quality["summary"],
            citation_coverage=coverage,
            conflict_summary=conflict_summary,
        )
        final = self.pipeline.compose_research_answer(
            query=query,
            draft_answer=repaired["answer"],
            evidence_rows=usable,
            answer_policy=policy,
            conflict_summary=conflict_summary,
        )
        self.assertTrue(final.strip())
        self.assertTrue(final.startswith("Answer\nThe best-supported answer is:"))
        self.assertNotIn("I couldn't verify", final)
        self.assertNotIn("I could not verify a reliable answer", final)
        self.assertIn("Why this answer", final)
        self.assertIn("Confidence", final)
        self.assertIn("because", final.lower())
        self.assertIn("What to treat carefully", final)
        self.assertIn("Sources", final)
        self.assertEqual(repaired["unsupported_critical_claims"], 0)
        if usable:
            self.assertNotEqual(policy["answer_mode"], ResearchAnswerMode.NO_USABLE_EVIDENCE.value)

    def test_latest_openai_api_model_changes_gets_best_supported_answer(self):
        self._assert_useful_answer(
            "latest OpenAI API model changes",
            [
                {
                    "title": "OpenAI API model docs",
                    "link": "https://platform.openai.com/docs/models",
                    "snippet": "OpenAI model documentation lists current API model availability and recent model changes.",
                    "published_at": "2026-04-24",
                    "rank_score": 0.9,
                },
                {
                    "title": "OpenAI changelog",
                    "link": "https://openai.com/changelog",
                    "snippet": "OpenAI changelog describes API model updates and release notes.",
                    "published_at": "2026-04-22",
                    "rank_score": 0.82,
                },
            ],
        )

    def test_official_firebase_pricing_update_gets_direct_answer(self):
        self._assert_useful_answer(
            "official Firebase pricing update",
            [
                {
                    "title": "Firebase Pricing",
                    "link": "https://firebase.google.com/pricing",
                    "snippet": "Firebase pricing is documented on the official Firebase pricing page with plan and usage details.",
                    "published_at": "2026-04-20",
                    "rank_score": 0.88,
                }
            ],
        )

    def test_current_best_vector_databases_for_rag_explains_conflict(self):
        rows = [
            {
                "title": "Vector database benchmark 2026",
                "link": "https://trusted.example.com/vector-db-a",
                "snippet": "Vector database benchmark 2026 says Pinecone latency is 10 ms for RAG workloads.",
                "tier": "trusted",
                "published_at": "2026-04-10",
            },
            {
                "title": "Vector database benchmark 2026",
                "link": "https://other.example.com/vector-db-b",
                "snippet": "Vector database benchmark 2026 says Pinecone latency is 30 ms for RAG workloads.",
                "tier": "trusted",
                "published_at": "2026-04-11",
            },
        ]
        conflict = self.pipeline.resolve_conflicts(evidence_rows=rows)["summary"]
        self._assert_useful_answer("current best vector databases for RAG", rows, conflict=conflict)
        self.assertTrue(conflict["conflict_detected"])

    def test_nextjs_caching_recent_changes_gets_best_supported_answer(self):
        self._assert_useful_answer(
            "what changed in Next.js caching recently",
            [
                {
                    "title": "Next.js caching docs",
                    "link": "https://nextjs.org/docs/app/building-your-application/caching",
                    "snippet": "Next.js caching documentation explains current caching behavior and recent changes.",
                    "published_at": "2026-04-01",
                    "rank_score": 0.84,
                }
            ],
        )

    def test_latest_ai_agent_frameworks_2026_gets_useful_low_confidence_answer(self):
        self._assert_useful_answer(
            "latest AI agent frameworks in 2026",
            [
                {
                    "title": "AI agent frameworks 2026 roundup",
                    "link": "https://trusted.example.com/ai-agent-frameworks-2026",
                    "snippet": "A 2026 technical roundup compares LangGraph, CrewAI, AutoGen, and related agent frameworks.",
                    "tier": "trusted",
                    "published_at": "2026-03-20",
                    "rank_score": 0.72,
                },
                {
                    "title": "Agent framework release notes",
                    "link": "https://github.com/langchain-ai/langgraph/releases",
                    "snippet": "LangGraph release notes list recent agent framework changes.",
                    "published_at": "2026-04-15",
                    "rank_score": 0.8,
                },
            ],
        )

    def test_weak_evidence_answers_if_evidence_exists_and_only_zero_evidence_no_answer(self):
        query = "latest unknown private startup AI model release"
        weak_rows = [
            {
                "title": "Possible startup model release",
                "link": "https://example.com/private-startup-model",
                "snippet": "A weak but relevant report mentions a possible private startup AI model release.",
                "published_at": "2026-04-24",
                "tier": "other",
            }
        ]
        quality = self.pipeline.assess_source_quality(rows=weak_rows, query=query)
        policy = self.pipeline.answer_policy(quality_summary=quality["summary"], citation_coverage={"coverage": 0.55})
        self.assertEqual(policy["answer_mode"], ResearchAnswerMode.WEAK_CANDIDATE.value)
        final = self.pipeline.compose_research_answer(
            query=query,
            draft_answer="The possible release is only weakly supported. [S1]",
            evidence_rows=quality["usable_rows"],
            answer_policy=policy,
        )
        self.assertIn("The best-supported answer is:", final)
        self.assertNotIn("I could not verify a reliable answer", final)

        no_evidence_policy = self.pipeline.answer_policy(quality_summary={"usable_count": 0}, citation_coverage={})
        no_evidence = self.pipeline.compose_research_answer(
            query=query,
            evidence_rows=[],
            answer_policy=no_evidence_policy,
        )
        self.assertEqual(no_evidence_policy["answer_mode"], ResearchAnswerMode.NO_USABLE_EVIDENCE.value)
        self.assertIn("I could not verify a reliable answer", no_evidence)


if __name__ == "__main__":
    unittest.main()
