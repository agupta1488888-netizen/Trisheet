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

#: Older filings are sharded out of the submissions document into these.
SEC_SUBMISSIONS_SHARD_URL_TEMPLATE = "https://data.sec.gov/submissions/{name}"

SEC_ARCHIVES_BASE_URL = "https://www.sec.gov"

#: Directory listing for one filing. `cik_int` is unpadded; EDGAR archive paths
#: use the integer form while the JSON APIs use the zero-padded form.
SEC_FILING_INDEX_URL_TEMPLATE = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/"
    "{accession_dashed}-index.htm"
)

SEC_FILING_DOCUMENT_URL_TEMPLATE = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{document}"
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

# --- Response cache ---------------------------------------------------------

#: Filings are immutable, so their responses are cached with no expiry.
EDGAR_CACHE_TTL_PERMANENT: float | None = None

#: The ticker index changes as companies list and delist.
COMPANY_TICKERS_TTL_SECONDS = 86_400.0

#: XBRL company concepts gain new periods as filings arrive.
XBRL_CONCEPT_TTL_SECONDS = 86_400.0

EDGAR_CACHE_DIR_NAME = ".edgar_cache"

# --- Taxonomies -------------------------------------------------------------

#: Tried in order. Foreign private issuers fall through to IFRS.
XBRL_TAXONOMY_PREFERENCE = ("us-gaap", "ifrs-full")

#: Monetary concepts tried, in order, to discover a filer's reporting currency
#: from its XBRL unit keys. Assets is used because virtually every filer in
#: either taxonomy reports it.
CURRENCY_PROBE_CONCEPTS = (
    ("us-gaap", "Assets"),
    ("ifrs-full", "Assets"),
)

#: Unit keys that are not currencies and must never be read as one.
NON_CURRENCY_UNIT_KEYS = frozenset({"shares", "pure", "USD-per-shares"})

# --- Forms ------------------------------------------------------------------

#: The annual report each filer type files. Order matters: the most recently
#: filed annual form decides the filer type, so a company that migrated from
#: 20-F to 10-K is classified by what it files now.
ANNUAL_FORM_TO_FILER_TYPE = {
    "10-K": "domestic",
    "20-F": "foreign",
    "40-F": "canadian",
}

#: Forms a report may draw on. Anything else is dropped from the manifest.
PERMITTED_FORMS = frozenset(
    {"10-K", "10-Q", "8-K", "DEF 14A", "20-F", "40-F", "6-K"}
)

#: Current reports, whose exhibits carry the earnings release and slides.
CURRENT_REPORT_FORMS = frozenset({"8-K", "6-K"})

#: Exhibits worth resolving: the press release and the presentation.
EXHIBIT_TYPES_OF_INTEREST = ("EX-99.1", "EX-99.2")

#: Every current report costs one extra request to fetch its index, so only the
#: most recent ones are expanded. Older exhibits are fetched on demand.
MAX_CURRENT_REPORTS_WITH_EXHIBITS = 24

# --- Resolution -------------------------------------------------------------

#: Most candidates returned for an ambiguous query before the list is cut.
MAX_RESOLUTION_CANDIDATES = 10

#: Company-name suffixes stripped before comparing names, so that "Nike" and
#: "NIKE, Inc." compare equal.
COMPANY_NAME_SUFFIXES = (
    "incorporated",
    "corporation",
    "company",
    "limited",
    "holdings",
    "holding",
    "group",
    "plc",
    "inc",
    "corp",
    "ltd",
    "llc",
    "lp",
    "co",
    "sa",
    "nv",
    "ag",
)

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
