from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Deployment environment. Set ENV=production on Railway / prod hosts —
    # this enables stricter checks (e.g. ADMIN_PASSWORD must be non-empty).
    env: str = Field(default="dev", alias="ENV")

    database_url: str = Field(
        default="sqlite:///./honest_congress.db", alias="DATABASE_URL"
    )

    # CORS. Comma-separated origins. "*" is allowed for local/dev only —
    # in production set this to your dashboard origin(s).
    allowed_origins: str = Field(default="*", alias="ALLOWED_ORIGINS")

    # Congress.gov API key (optional - get free key at https://api.congress.gov/sign-up/)
    congress_gov_api_key: str = Field(default="", alias="CONGRESS_GOV_API_KEY")

    # QuiverQuant API key (for congressional trading data)
    quiverquant_api_key: str = Field(default="", alias="QUIVERQUANT_API_KEY")

    wealth_growth_threshold_percent: float = Field(default=200.0, alias="WEALTH_GROWTH_THRESHOLD_PERCENT")
    congressional_salary: int = Field(default=174000, alias="CONGRESSIONAL_SALARY")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    admin_password: str = Field(default="", alias="ADMIN_PASSWORD")

    # Late-filing detector knobs. Defaults are tuned to keep the noise low —
    # only PTRs filed >60 days late AND for trades >= $50k are flagged.
    late_filing_min_days: int = Field(default=60, alias="LATE_FILING_MIN_DAYS")
    late_filing_min_amount_usd: int = Field(default=50000, alias="LATE_FILING_MIN_AMOUNT_USD")

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}

    @property
    def allowed_origins_list(self) -> List[str]:
        raw = (self.allowed_origins or "").strip()
        if raw in ("", "*"):
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @field_validator("env")
    @classmethod
    def _normalize_env(cls, v: str) -> str:
        return (v or "dev").strip().lower()


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    if settings.is_production and not settings.admin_password:
        # Fail fast — admin endpoints are mutating, and a blank password
        # plus "no admin password configured" 503s would silently disable
        # half the API rather than alerting an operator.
        raise RuntimeError(
            "ADMIN_PASSWORD must be set when ENV=production. "
            "Generate a strong password and add it to your Railway/host env."
        )
    return settings
