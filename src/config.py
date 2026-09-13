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

    database_url: str = Field(default="sqlite:///./honest_congress.db", alias="DATABASE_URL")

    # CORS. Comma-separated origins. "*" is allowed for local/dev only —
    # in production set this to your dashboard origin(s).
    allowed_origins: str = Field(default="*", alias="ALLOWED_ORIGINS")

    # Congress.gov API key (optional - get free key at https://api.congress.gov/sign-up/)
    congress_gov_api_key: str = Field(default="", alias="CONGRESS_GOV_API_KEY")

    # FEC API key, for corporate PAC donations. Free from
    # https://api.data.gov/signup/ and allows 1,000 requests/hour, which is the
    # binding constraint on `ingest-donations` -- see src/ingestion/fec.py.
    fec_api_key: str = Field(default="", alias="FEC_API_KEY")

    # Senate LDA key, for lobbying disclosures. OPTIONAL: the API serves
    # anonymous callers at roughly 15 requests/minute and keyed callers at 120.
    # Registration is free at https://lda.senate.gov/api/register/
    lda_api_key: str = Field(default="", alias="LDA_API_KEY")

    wealth_growth_threshold_percent: float = Field(
        default=200.0, alias="WEALTH_GROWTH_THRESHOLD_PERCENT"
    )
    congressional_salary: int = Field(default=174000, alias="CONGRESSIONAL_SALARY")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    admin_password: str = Field(default="", alias="ADMIN_PASSWORD")

    # Late-filing detector knobs. Defaults are tuned to keep the noise low —
    # only PTRs filed >60 days late AND for trades >= $50k are flagged.
    late_filing_min_days: int = Field(default=60, alias="LATE_FILING_MIN_DAYS")
    late_filing_min_amount_usd: int = Field(default=50000, alias="LATE_FILING_MIN_AMOUNT_USD")

    # Detectors whose output is not currently defensible and must not be
    # written or served. See docs/DECISIONS.md.
    #   outperforming_trades - benchmarks against a hardcoded flat 10%, and
    #     computes "return" as (sells - buys)/buys with no position matching.
    #   perfect_timing       - counts buy/sell date pairs without ever reading a
    #     price; the O(n^2) numerator over a linear denominator yields rates >100%.
    #   loss_avoidance       - increments numerator and denominator on the same
    #     branch, so its rate is always exactly 100%.
    # Re-enabling requires real price history; see the plan's "Price data" note.
    disabled_anomaly_types: str = Field(
        default="outperforming_trades,perfect_timing,loss_avoidance",
        alias="DISABLED_ANOMALY_TYPES",
    )

    @property
    def disabled_anomaly_types_set(self) -> set[str]:
        raw = (self.disabled_anomaly_types or "").strip()
        if not raw:
            return set()
        return {t.strip() for t in raw.split(",") if t.strip()}

    # Multiple-comparisons control. The suite runs sixteen detectors against
    # every member, so some of what it flags is what running thousands of tests
    # over hundreds of people produces. `fdr_alpha` is the false-discovery rate
    # the API filters at; `significance_permutations` is how many shifted
    # calendars the null is built from -- more is slower and gives a finer
    # p-value floor of 1/(n+1). See src/analysis/significance.py.
    fdr_alpha: float = Field(default=0.05, alias="FDR_ALPHA")
    significance_permutations: int = Field(default=1000, alias="SIGNIFICANCE_PERMUTATIONS")

    # SEC refuses requests whose User-Agent does not carry a contact email --
    # a bare descriptive string gets a 403. Set this to a real address you
    # monitor before running any SEC-backed ingestion in production; the
    # default is a placeholder and SEC may rate-limit or block it.
    sec_contact_email: str = Field(default="contact@example.com", alias="SEC_CONTACT_EMAIL")

    @property
    def database_url_display(self) -> str:
        """The database URL with any password redacted.

        `reset` prints its target before destroying it, and DATABASE_URL
        carries credentials -- so this must never reach a terminal or a CI log
        intact.
        """
        raw = self.database_url or ""
        if "://" not in raw:
            return raw

        scheme, _, rest = raw.partition("://")
        if "@" not in rest:
            return raw

        credentials, _, host = rest.rpartition("@")
        user, sep, _password = credentials.partition(":")
        if not sep:
            return f"{scheme}://{credentials}@{host}"
        return f"{scheme}://{user}:***@{host}"

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


@lru_cache
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
