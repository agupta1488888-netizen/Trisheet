"""Application configuration.

Every tunable in the system lives here. No magic numbers or inline literals are
permitted elsewhere in the codebase.

Secrets are read from the environment only. They are never committed and never
logged — `SecretStr` is used so that an accidental repr does not leak them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- SEC EDGAR endpoints ----------------------------------------------------
# Filings are immutable, so responses from these endpoints are cached forever.

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANY_FACTS_URL_TEMPLATE = (
    "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
)
SEC_COMPANY_CONCEPT_URL_TEMPLATE = (
    "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{tag}.json"
)

# --- SEC EDGAR client limits ------------------------------------------------

#: CIK is always zero-padded to this width in EDGAR URLs.
CIK_PAD_WIDTH = 10

#: Global ceiling across all workers, enforced by a shared token bucket.
EDGAR_MAX_REQUESTS_PER_SECOND = 10

#: A request that receives 429 is retried at most this many times.
EDGAR_MAX_RETRY_ATTEMPTS = 3

#: Exponential backoff base. Delay for attempt n is BASE * (2 ** n) seconds,
#: unless the response carries a Retry-After header, which always wins.
EDGAR_BACKOFF_BASE_SECONDS = 1.0

EDGAR_REQUEST_TIMEOUT_SECONDS = 30.0

#: SEC returns 403 to any request without a contact-bearing User-Agent.
EDGAR_USER_AGENT_TEMPLATE = "Tearsheet {contact_email}"

# --- Taxonomies -------------------------------------------------------------

#: Tried in order. Foreign private issuers fall through to IFRS.
XBRL_TAXONOMY_PREFERENCE = ("us-gaap", "ifrs-full")

# --- Source tiers -----------------------------------------------------------

#: Tiers accepted by section 3 (financial highlights). Enforced in code.
SECTION_3_ALLOWED_TIERS = (1, 2)


class Settings(BaseSettings):
    """Environment-backed settings. Instantiated once via `get_settings`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    port: int = 8000

    #: Sent to SEC in every User-Agent. Required — SEC blocks anonymous traffic.
    edgar_contact_email: str = Field(default="", description="Contact for SEC")

    anthropic_api_key: SecretStr = SecretStr("")

    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")

    #: Comma-separated. Kept as a string so that pydantic-settings does not
    #: attempt to JSON-decode it, which is a common source of startup failure.
    cors_allowed_origins: str = "http://localhost:3000"

    @property
    def cors_origins(self) -> list[str]:
        """Allowed browser origins, parsed from the comma-separated setting."""
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    @property
    def edgar_user_agent(self) -> str:
        """User-Agent header sent on every sec.gov and data.sec.gov request."""
        return EDGAR_USER_AGENT_TEMPLATE.format(
            contact_email=self.edgar_contact_email
        )

    @property
    def edgar_configured(self) -> bool:
        """EDGAR is the only hard dependency; startup checks this."""
        return bool(self.edgar_contact_email)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns the process-wide settings instance."""
    return Settings()
