from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    database_url: str = Field(default="sqlite:///./honest_congress.db", alias="DATABASE_URL")

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
