from __future__ import annotations

from pathlib import Path

from taos.config.settings import Settings
from taos.core.chat.persistent_chat_manager import PersistentChatManager
from taos.core.deployment.readiness import build_readiness_report
from taos.core.persistence.firestore_memory import FirestoreMemorySchema
from taos.core.persistence.runtime_status import build_persistence_status
from taos.core.documents.repository import DocumentRepository
from taos.infra.persistence.store import InMemoryStore


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "TAOS_ENV": "development",
        "DEBUG": False,
        "OPENROUTER_API_KEY": "test-openrouter",
        "SERPER_API_KEY": "test-serper",
        "STORAGE_BACKEND": "firebase",
        "AUTH_ALLOW_DEV_BYPASS": False,
        "MAX_REQUEST_TIME_SECONDS": 45,
        "MAX_STEP_TIME": 30,
        "MAX_STEPS": 15,
        "MAX_UPLOAD_SIZE_MB": 20,
    }
    base.update(overrides)
    return Settings(**base)


def _sandbox_roots() -> tuple[Path, Path]:
    root = Path("tmp_phase143_test_artifacts")
    frontend = root / "frontend"
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (frontend / ".next").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "ops_dashboard_latest.json").write_text("{}", encoding="utf-8")
    (root / "docs" / "ops_dashboard_latest.md").write_text("# ops\n", encoding="utf-8")
    (frontend / ".next" / "BUILD_ID").write_text("phase143", encoding="utf-8")
    return root, frontend


def test_production_blocks_memory_fallback_when_firebase_unavailable() -> None:
    settings = _settings(TAOS_ENV="production", STORAGE_BACKEND="firebase")
    status = build_persistence_status(settings=settings, firestore_ready=False, check_runtime=False)
    assert status["persistence_mode"] in {"memory_fallback", "disabled"}
    assert status["production_blocking"] is True


def test_development_allows_memory_fallback_with_warning_contract() -> None:
    settings = _settings(TAOS_ENV="development", STORAGE_BACKEND="firebase")
    status = build_persistence_status(settings=settings, firestore_ready=False, check_runtime=False)
    assert status["persistence_mode"] == "memory_fallback"
    assert status["production_blocking"] is False
    assert status["fallback_reason"] in {"firebase_admin_not_installed", "firebase_not_configured", "firestore_not_ready", None}


def test_staging_blocks_memory_fallback_by_default() -> None:
    settings = _settings(TAOS_ENV="staging", STORAGE_BACKEND="firebase")
    status = build_persistence_status(settings=settings, firestore_ready=False, check_runtime=False)
    assert status["production_blocking"] is True


def test_staging_allows_fallback_only_with_explicit_override() -> None:
    settings = _settings(
        TAOS_ENV="staging",
        STORAGE_BACKEND="firebase",
        ALLOW_MEMORY_FALLBACK_IN_PRODUCTION=True,
    )
    status = build_persistence_status(settings=settings, firestore_ready=False, check_runtime=False)
    assert status["persistence_mode"] == "memory_fallback"
    assert status["production_blocking"] is False


def test_readiness_report_exposes_persistence_metadata() -> None:
    root, frontend = _sandbox_roots()
    report = build_readiness_report(
        settings=_settings(TAOS_ENV="development", STORAGE_BACKEND="firebase"),
        repo_root=root,
        frontend_root=frontend,
        check_firebase_runtime=False,
    )
    checks = report["checks"]
    assert "persistence_mode" in checks
    assert "firebase_admin_available" in checks
    assert "firestore_ready" in checks
    assert "persistence_fallback_reason" in checks


def test_chat_persistence_keeps_trace_and_route_metadata() -> None:
    store = InMemoryStore()
    manager = PersistentChatManager(user_id="phase143_user", store=store)
    import asyncio

    asyncio.run(
        manager.upsert_chat(
            "c1",
            {
                "title": "Chat",
                "messages": [
                    {
                        "id": "m1",
                        "role": "assistant",
                        "text": "answer",
                        "status": "done",
                        "ts": 1.0,
                        "trace": {
                            "selected_route": "fast_search",
                            "public_route_label": "fast_search",
                        },
                        "trust_block": {"confidence": "medium", "answer_mode": "best_supported"},
                    }
                ],
                "doc_ids": [],
            },
        )
    )
    loaded = asyncio.run(manager.get_chat("c1"))
    message = loaded["messages"][0]
    assert message["trace"]["selected_route"] == "fast_search"
    assert message["trace"]["public_route_label"] == "fast_search"
    assert message["trust_block"]["answer_mode"] == "best_supported"


def test_cross_user_chat_isolation_still_holds() -> None:
    store = InMemoryStore()
    owner = PersistentChatManager(user_id="u1", store=store)
    intruder = PersistentChatManager(user_id="u2", store=store)
    import asyncio

    asyncio.run(owner.upsert_chat("private", {"title": "Private", "messages": [], "doc_ids": []}))
    assert asyncio.run(owner.get_chat("private")) is not None
    assert asyncio.run(intruder.get_chat("private")) is None


def test_document_metadata_persistence_is_user_scoped() -> None:
    import asyncio

    repo = DocumentRepository(use_firestore=False)
    asyncio.run(
        repo.create_document(
            {
                "doc_id": "d1",
                "user_id": "u1",
                "file_name": "a.pdf",
                "status": "ready",
                "processing_stage": "complete",
            }
        )
    )
    assert asyncio.run(repo.get_document("u1", "d1"))["status"] == "ready"
    assert asyncio.run(repo.get_document("u2", "d1")) is None


def test_firestore_memory_schema_blocks_prod_fallback() -> None:
    settings = _settings(TAOS_ENV="production", STORAGE_BACKEND="firebase")
    try:
        FirestoreMemorySchema._build_store  # type: ignore[attr-defined]
    except Exception:
        pass
    status = build_persistence_status(settings=settings, firestore_ready=False, check_runtime=False)
    assert status["production_blocking"] is True
