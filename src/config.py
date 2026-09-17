import logging
from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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

    # CORS. Comma-separated origins. The "*" default applies to local
    # development only -- `allowed_origins_list` refuses it in production. See
    # the note there for why.
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
    #
    # The next two are HELD, NOT CONDEMNED, and the distinction is the whole
    # reason this comment is here. Nothing is known to be wrong with them:
    #   wealth_vs_salary         - both read their roster from
    #   rapid_asset_appreciation   `members_with_annual_filings`, which gated on
    #     `Disclosure.filing_type == "FD"` -- a value ZERO of 3,900 stored rows
    #     carry. Only the Senate ingester ever wrote it, as a fallback that
    #     stopped firing once real report titles were stored. So both detectors
    #     walked an empty roster and found nothing, silently, for months.
    #
    #     That gate is fixed. These are off because the fix means they publish
    #     again, and NOBODY HAS EVER READ WHAT THEY SAY. This project has
    #     published four confident false accusations against named members of
    #     Congress -- three checkers during the audit, and the wealth findings
    #     that had Craig Goldman gaining $15,008,502.50 in a year against a real
    #     figure of $551,001. A fifth unread accuser is the same mistake.
    #
    #     Remove them once someone has looked at a sample of their output and
    #     can say it is sound. That is a lower bar than the three above, which
    #     need price data that does not exist. Do not conflate the two.
    disabled_anomaly_types: str = Field(
        default=(
            "outperforming_trades,perfect_timing,loss_avoidance,"
            "wealth_vs_salary,rapid_asset_appreciation"
        ),
        alias="DISABLED_ANOMALY_TYPES",
    )

    @property
    def disabled_anomaly_types_set(self) -> set[str]:
        raw = (self.disabled_anomaly_types or "").strip()
        if not raw:
            return set()
        return {t.strip() for t in raw.split(",") if t.strip()}

    # Multiple-comparisons control. The suite runs seventeen detectors against
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
        """Origins the API answers cross-origin requests from.

        The wildcard is a development convenience and is refused in production.
        `src/api/main.py` pairs this with `allow_headers=["*"]`, so a wildcard
        on a deployed host lets any page on the internet send `X-Admin-Token`
        to the mutating endpoints. `allow_credentials` self-disables on "*",
        which stops cookies but not a header an attacker sets deliberately.

        Falling back to "no cross-origin allowed" rather than raising is the
        deliberate choice. `ADMIN_PASSWORD` raises in `get_settings()` because
        the app genuinely cannot serve its admin routes without one; a missing
        origin list is different -- raising would take a running site down at
        the moment someone deployed a security fix. Nothing legitimate breaks
        here either way: the dashboard fetches its own `/api/*` from the same
        origin, and same-origin requests never go through CORS at all. Only a
        separate front end on another domain would notice, and that is exactly
        the case that should have to be declared.
        """
        raw = (self.allowed_origins or "").strip()

        if raw in ("", "*"):
            if self.is_production:
                logger.warning(
                    "ALLOWED_ORIGINS is %s in production; refusing cross-origin "
                    "requests. Set it to your dashboard origin(s) to allow them.",
                    "unset" if not raw else "'*'",
                )
                return []
            return ["*"]

        return [o.strip() for o in raw.split(",") if o.strip()]

    @field_validator("env")
    @classmethod
    def _normalize_env(cls, v: str) -> str:
        return (v or "dev").strip().lower()

    @field_validator("sec_contact_email", mode="before")
    @classmethod
    def _blank_contact_falls_back_to_the_default(cls, v: object) -> object:
        """An environment variable set to "" is not the same as one left unset.

        A GitHub workflow that writes `SEC_CONTACT_EMAIL: ${{ secrets.X }}` for a
        secret that does not exist sets the variable to the EMPTY STRING, which
        overrides this field's default instead of leaving it in place. The
        User-Agent then reads "honest-congress " with no address, and SEC answers
        403 -- a result this repository has already measured and written down in
        src/ingestion/sec_tickers.py.

        The consequence was not a loud failure. The ticker register came back
        empty, so TickerResolver resolved nothing, so every USASpending award and
        every LDA lobbying filing was discarded as unresolvable, so two detectors
        that need no API key at all sat permanently dark while their workflow
        steps reported success in about a second each.

        Falling back here rather than only in the workflows covers Railway and
        any other caller that passes a blank through. The API-key fields need no
        such treatment: their default is already "", so blank and absent mean the
        same thing and both fail loudly at the point of use.
        """
        if isinstance(v, str) and not v.strip():
            return "contact@example.com"
        return v

    @field_validator("congress_gov_api_key", "fec_api_key", "lda_api_key", mode="before")
    @classmethod
    def _strip_credential_whitespace(cls, v: object) -> object:
        """A secret pasted with a trailing newline is not the secret.

        It authenticates as a subtly wrong string and the upstream rejects it,
        which reads like a bad key rather than a stray character.
        """
        return v.strip() if isinstance(v, str) else v


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
