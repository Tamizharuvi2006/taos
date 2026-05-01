"""
TAOS Configuration — Central settings loaded from environment.
All configuration flows through this module. No component reads .env directly.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Production settings for TAOS AgentOS."""

    # ─── Environment ───
    taos_env: str = Field(default="development", alias="TAOS_ENV")
    debug: bool = Field(default=False, alias="DEBUG")

    # ─── OpenRouter LLM ───
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        alias="OPENROUTER_BASE_URL",
    )
    site_url: str = Field(default="https://relyce.com", alias="SITE_URL")
    site_name: str = Field(default="TAOS AgentOS", alias="SITE_NAME")

    # ─── Model Selection ───
    planner_model: str = Field(default="qwen/qwen3.6-plus:free", alias="PLANNER_MODEL")
    executor_model: str = Field(default="openai/gpt-4o-mini", alias="EXECUTOR_MODEL")
    reflection_model: str = Field(default="google/gemma-4-31b-it", alias="REFLECTION_MODEL")
    fallback_model: str = Field(default="anthropic/claude-3-haiku", alias="FALLBACK_MODEL")

    # ─── Tool API Keys ───
    serper_api_key: str = Field(default="", alias="SERPER_API_KEY")
    serper_base_url: str = Field(default="https://google.serper.dev", alias="SERPER_BASE_URL")

    # ─── Firebase ───
    firebase_project_id: str = Field(default="", alias="FIREBASE_PROJECT_ID")
    firebase_private_key_id: str = Field(default="", alias="FIREBASE_PRIVATE_KEY_ID")
    firebase_private_key: str = Field(default="", alias="FIREBASE_PRIVATE_KEY")
    firebase_client_email: str = Field(default="", alias="FIREBASE_CLIENT_EMAIL")
    firebase_client_id: str = Field(default="", alias="FIREBASE_CLIENT_ID")
    firebase_database_id: str = Field(default="(default)", alias="FIREBASE_DATABASE_ID")
    firebase_storage_bucket: str = Field(default="", alias="FIREBASE_STORAGE_BUCKET")
    firebase_auth_uri: str = Field(default="https://accounts.google.com/o/oauth2/auth", alias="FIREBASE_AUTH_URI")
    firebase_token_uri: str = Field(default="https://oauth2.googleapis.com/token", alias="FIREBASE_TOKEN_URI")
    firebase_auth_provider_x509_cert_url: str = Field(default="https://www.googleapis.com/oauth2/v1/certs", alias="FIREBASE_AUTH_PROVIDER_X509_CERT_URL")
    firebase_client_x509_cert_url: str = Field(default="", alias="FIREBASE_CLIENT_X509_CERT_URL")
    firebase_universe_domain: str = Field(default="googleapis.com", alias="FIREBASE_UNIVERSE_DOMAIN")
    max_upload_size_mb: int = Field(default=20, alias="MAX_UPLOAD_SIZE_MB")
    document_processing_max_concurrency: int = Field(default=2, alias="DOCUMENT_PROCESSING_MAX_CONCURRENCY")
    document_ask_max_docs: int = Field(default=5, alias="DOCUMENT_ASK_MAX_DOCS")
    # ─── Notifications ───
    zeptomail_api_key: str = Field(default="", alias="ZEPTOMAIL_API_KEY")
    zeptomail_from_email: str = Field(default="", alias="ZEPTOMAIL_FROM_EMAIL")
    zeptomail_api_url: str = Field(default="", alias="ZEPTOMAIL_API_URL")
    zeptomail_logo_url: str = Field(default="", alias="ZEPTOMAIL_LOGO_URL")
    whatsapp_webhook_url: str = Field(default="", alias="WHATSAPP_WEBHOOK_URL")
    whatsapp_api_key: str = Field(default="", alias="WHATSAPP_API_KEY")
    default_whatsapp_target: str = Field(default="", alias="DEFAULT_WHATSAPP_TARGET")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    razorpay_key_id: str = Field(default="", alias="RAZORPAY_KEY_ID")
    razorpay_key_secret: str = Field(default="", alias="RAZORPAY_KEY_SECRET")
    razorpay_webhook_secret: str = Field(default="", alias="RAZORPAY_WEBHOOK_SECRET")
    max_notifications_per_day: int = Field(default=200, alias="MAX_NOTIFICATIONS_PER_DAY")
    storage_backend: str = Field(default="memory", alias="STORAGE_BACKEND")
    allow_memory_fallback_in_production: bool = Field(default=False, alias="ALLOW_MEMORY_FALLBACK_IN_PRODUCTION")
    feedback_similarity_threshold: float = Field(default=0.2, alias="FEEDBACK_SIMILARITY_THRESHOLD")
    feedback_min_confidence: float = Field(default=0.65, alias="FEEDBACK_MIN_CONFIDENCE")
    feedback_ttl_days: int = Field(default=45, alias="FEEDBACK_TTL_DAYS")
    feedback_max_context_items: int = Field(default=3, alias="FEEDBACK_MAX_CONTEXT_ITEMS")
    feedback_max_records: int = Field(default=1000, alias="FEEDBACK_MAX_RECORDS")
    research_profile_ttl_hours: int = Field(default=72, alias="RESEARCH_PROFILE_TTL_HOURS")
    research_profile_max_records: int = Field(default=500, alias="RESEARCH_PROFILE_MAX_RECORDS")
    research_cache_min_similarity: float = Field(default=0.58, alias="RESEARCH_CACHE_MIN_SIMILARITY")
    research_cache_max_age_hours: int = Field(default=120, alias="RESEARCH_CACHE_MAX_AGE_HOURS")
    agent_message_ttl_steps: int = Field(default=3, alias="AGENT_MESSAGE_TTL_STEPS")
    agent_message_ttl_seconds: int = Field(default=600, alias="AGENT_MESSAGE_TTL_SECONDS")
    agent_message_max_per_step: int = Field(default=5, alias="AGENT_MESSAGE_MAX_PER_STEP")
    agent_message_max_total: int = Field(default=200, alias="AGENT_MESSAGE_MAX_TOTAL")
    goal_decomposition_enabled: bool = Field(default=True, alias="GOAL_DECOMPOSITION_ENABLED")
    goal_decomposition_max_subgoals: int = Field(default=5, alias="GOAL_DECOMPOSITION_MAX_SUBGOALS")
    agent_low_trust_threshold: float = Field(default=0.45, alias="AGENT_LOW_TRUST_THRESHOLD")
    microservices_enabled: bool = Field(default=False, alias="MICROSERVICES_ENABLED")
    enable_planner_service: bool = Field(default=False, alias="ENABLE_PLANNER_SERVICE")
    enable_research_service: bool = Field(default=False, alias="ENABLE_RESEARCH_SERVICE")
    enable_execution_service: bool = Field(default=False, alias="ENABLE_EXECUTION_SERVICE")
    enable_debate_service: bool = Field(default=False, alias="ENABLE_DEBATE_SERVICE")
    planner_service_url: str = Field(default="http://localhost:8001", alias="PLANNER_SERVICE_URL")
    research_service_url: str = Field(default="http://localhost:8002", alias="RESEARCH_SERVICE_URL")
    execution_service_url: str = Field(default="http://localhost:8003", alias="EXECUTION_SERVICE_URL")
    service_timeout_seconds: float = Field(default=8.0, alias="SERVICE_TIMEOUT_SECONDS")
    service_retries: int = Field(default=2, alias="SERVICE_RETRIES")
    service_circuit_failures: int = Field(default=5, alias="SERVICE_CIRCUIT_FAILURES")
    service_circuit_cooldown_seconds: int = Field(default=60, alias="SERVICE_CIRCUIT_COOLDOWN_SECONDS")
    entity_lookup_v1_enabled: bool = Field(default=False, alias="ENTITY_LOOKUP_V1_ENABLED")
    scrapling_http_extractor_enabled: bool = Field(default=False, alias="SCRAPLING_HTTP_EXTRACTOR_ENABLED")
    scrapling_dynamic_extractor_enabled: bool = Field(default=False, alias="SCRAPLING_DYNAMIC_EXTRACTOR_ENABLED")
    scrapling_stealth_extractor_enabled: bool = Field(default=False, alias="SCRAPLING_STEALTH_EXTRACTOR_ENABLED")
    scrapling_domain_policy_enabled: bool = Field(default=True, alias="SCRAPLING_DOMAIN_POLICY_ENABLED")
    scrapling_http_empty_skip_threshold: int = Field(default=2, alias="SCRAPLING_HTTP_EMPTY_SKIP_THRESHOLD")
    scrapling_dynamic_max_urls_per_query: int = Field(default=2, alias="SCRAPLING_DYNAMIC_MAX_URLS_PER_QUERY")
    scrapling_stealth_max_urls_per_query: int = Field(default=1, alias="SCRAPLING_STEALTH_MAX_URLS_PER_QUERY")
    scrapling_dynamic_timeout_ms: int = Field(default=5000, alias="SCRAPLING_DYNAMIC_TIMEOUT_MS")
    scrapling_stealth_timeout_ms: int = Field(default=7000, alias="SCRAPLING_STEALTH_TIMEOUT_MS")
    max_request_time_seconds: int = Field(default=45, alias="MAX_REQUEST_TIME_SECONDS")
    max_research_time_seconds: int = Field(default=180, alias="MAX_RESEARCH_TIME_SECONDS")
    sse_heartbeat_seconds: int = Field(default=12, alias="SSE_HEARTBEAT_SECONDS")
    execution_sampling_rate: float = Field(default=0.2, alias="EXECUTION_SAMPLING_RATE")
    execution_store_latency_threshold_ms: int = Field(default=3000, alias="EXECUTION_STORE_LATENCY_THRESHOLD_MS")
    max_execution_history_records: int = Field(default=300, alias="MAX_EXECUTION_HISTORY_RECORDS")
    max_plan_history_records: int = Field(default=300, alias="MAX_PLAN_HISTORY_RECORDS")
    max_stored_field_bytes: int = Field(default=8192, alias="MAX_STORED_FIELD_BYTES")
    task_scheduler_poll_seconds: int = Field(default=10, alias="TASK_SCHEDULER_POLL_SECONDS")
    task_scheduler_max_concurrent: int = Field(default=10, alias="TASK_SCHEDULER_MAX_CONCURRENT")
    disable_tool_limits: bool = Field(default=True, alias="DISABLE_TOOL_LIMITS")
    auth_allow_dev_bypass: bool = Field(default=False, alias="AUTH_ALLOW_DEV_BYPASS")
    perf_test_mode: bool = Field(default=False, alias="PERF_TEST_MODE")
    perf_test_user_id: str = Field(default="", alias="PERF_TEST_USER_ID")
    perf_test_per_minute_limit: int = Field(default=120, alias="PERF_TEST_PER_MINUTE_LIMIT")

    # ─── Execution Limits ───
    max_task_time: int = Field(default=120, alias="MAX_TASK_TIME")
    max_step_time: int = Field(default=30, alias="MAX_STEP_TIME")
    max_steps: int = Field(default=15, alias="MAX_STEPS")
    max_retries: int = Field(default=3, alias="MAX_RETRIES")
    max_replans: int = Field(default=2, alias="MAX_REPLANS")
    dag_max_concurrency: int = Field(default=3, alias="DAG_MAX_CONCURRENCY")

    # ─── Cost Control ───
    cost_budget_per_task: float = Field(default=1.0, alias="COST_BUDGET_PER_TASK")

    # ─── Confidence ───
    confidence_retry_threshold: float = Field(default=0.6, alias="CONFIDENCE_RETRY_THRESHOLD")
    confidence_terminate_threshold: float = Field(default=0.3, alias="CONFIDENCE_TERMINATE_THRESHOLD")

    # ─── Loop Detection ───
    repeated_state_threshold: int = Field(default=3, alias="REPEATED_STATE_THRESHOLD")

    # ─── Server ───
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    # ─── Logging ───
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="json", alias="LOG_FORMAT")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v

    @property
    def is_production(self) -> bool:
        return self.taos_env == "production"

    @property
    def is_staging(self) -> bool:
        return self.taos_env == "staging"

    @property
    def is_production_like(self) -> bool:
        return self.taos_env in {"production", "staging"}

    @property
    def is_development(self) -> bool:
        return self.taos_env == "development"

    model_config = {
        "env_file": (".env", "taos/.env", "d:/agent/taos/.env"),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }


@lru_cache()
def get_settings() -> Settings:
    """Singleton settings instance. Cached after first call."""
    return Settings()
