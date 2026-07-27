"""Central configuration. APP_PROFILE=prod validates required secrets at boot
(fail fast); APP_PROFILE=dev lets infra run without external keys so the stack
can be exercised locally — agents raise clearly if invoked without their key."""
import sys
from typing import ClassVar, Tuple

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_PROFILE: str = "dev"                     # dev | prod
    SERVICE_NAME: str = "sdr"

    DATABASE_URL: str = "postgresql://sdr:sdr@localhost:5432/sdr"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"

    ANTHROPIC_API_KEY: str = ""
    FIRECRAWL_API_KEY: str = ""
    SERPER_API_KEY: str = ""
    HUNTER_API_KEY: str = ""
    APOLLO_API_KEY: str = ""
    GMAIL_CLIENT_ID: str = ""
    GMAIL_CLIENT_SECRET: str = ""
    GMAIL_REFRESH_TOKEN: str = ""
    GMAIL_SENDER_EMAIL: str = ""

    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    # USD per million tokens — used to compute REAL cost from logged usage
    CLAUDE_INPUT_PRICE_PER_MTOK: float = 3.00
    CLAUDE_OUTPUT_PRICE_PER_MTOK: float = 15.00

    RESEARCH_MIN_CONFIDENCE: float = 0.65
    REPLY_AUTO_THRESHOLD: float = 0.80
    HUMAN_REVIEW_THRESHOLD: float = 0.75
    CLOSED_LOST_MIN_CONFIDENCE: float = 0.85

    FOLLOW_UP_HOURS: int = 72
    RESEND_WINDOW_DAYS: int = 7
    MAX_HANDLER_RETRIES: int = 3
    SEND_CLAIM_LEASE_MINUTES: int = 10
    STUCK_LEAD_LEASE_MINUTES: int = 15

    SCHEDULER_TICK_SECONDS: float = 1.0
    TIMER_EVERY_TICKS: int = 30          # follow-up / expiry scan cadence
    GMAIL_POLL_EVERY_TICKS: int = 60
    BOUNCE_SCAN_EVERY_TICKS: int = 300
    REAPER_EVERY_TICKS: int = 60

    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    METRICS_PORT: int = 9100
    SEED_DEMO_DATA: bool = False

    REQUIRED_IN_PROD: ClassVar[Tuple[str, ...]] = (
        "ANTHROPIC_API_KEY", "FIRECRAWL_API_KEY", "SERPER_API_KEY", "HUNTER_API_KEY",
        "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN", "GMAIL_SENDER_EMAIL",
    )

    def validate_profile(self) -> None:
        if self.APP_PROFILE != "prod":
            return
        missing = [k for k in self.REQUIRED_IN_PROD if not getattr(self, k)]
        if missing:
            sys.stderr.write(
                f"FATAL: APP_PROFILE=prod but required settings are empty: {', '.join(missing)}\n"
            )
            raise SystemExit(78)  # EX_CONFIG


settings = Settings()
