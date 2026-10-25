"""Configuration management for PyNCBI.

Provides typed configuration with environment variable support and runtime
configurability, including NCBI Entrez API key and realm-specific rate limits.

Environment Variables:
    PYNCBI_CACHE_DIR / PYNCBI_CACHE_FOLDER: Override default cache directory
    PYNCBI_REQUEST_TIMEOUT: Override default HTTP timeout (seconds)
    NCBI_API_KEY / PYNCBI_API_KEY: NCBI Entrez API key (enables 10 req/s instead of 3 req/s)
    NCBI_EMAIL / PYNCBI_EMAIL: Email address passed to NCBI E-utilities
    PYNCBI_AGENT_MODE: Set to "1" or "true" to suppress progress bars/colors and optimize for AI agents
    PYNCBI_MAX_DOWNLOAD_MB: Default download budget per operation (default 2048)
    PYNCBI_AUDIT_LOG: Path to an append-only JSONL log of every NCBI request
    PYNCBI_HTTP_CACHE_TTL: Seconds to reuse identical metadata responses (default 300, 0 disables)
    XDG_CACHE_HOME: Standard XDG cache directory (fallback)
"""

from __future__ import annotations

import contextlib
import os
import sys
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterator


def _get_default_cache_folder() -> Path:
    """Determine the default cache folder location."""
    if env_cache := (os.environ.get("PYNCBI_CACHE_DIR") or os.environ.get("PYNCBI_CACHE_FOLDER")):
        return Path(env_cache)

    if xdg_cache := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg_cache) / "PyNCBI"

    return Path.home() / ".cache" / "PyNCBI"


def _get_default_timeout() -> float:
    """Get default request timeout from environment or use default."""
    env_timeout = os.environ.get("PYNCBI_REQUEST_TIMEOUT")
    if env_timeout:
        try:
            return float(env_timeout)
        except ValueError:
            pass
    return 30.0


def _get_default_api_key() -> str | None:
    """Get NCBI API key from environment."""
    return os.environ.get("NCBI_API_KEY") or os.environ.get("PYNCBI_API_KEY") or None


def _get_default_email() -> str | None:
    """Get NCBI email from environment."""
    return os.environ.get("NCBI_EMAIL") or os.environ.get("PYNCBI_EMAIL") or None


def _get_default_max_download_mb() -> float:
    """Default per-operation download budget in MB (env PYNCBI_MAX_DOWNLOAD_MB)."""
    raw = os.environ.get("PYNCBI_MAX_DOWNLOAD_MB", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 2048.0


def _get_default_http_cache_ttl() -> float:
    """Seconds to reuse an identical metadata response (env PYNCBI_HTTP_CACHE_TTL, 0 disables)."""
    raw = os.environ.get("PYNCBI_HTTP_CACHE_TTL", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return 300.0


def _get_default_audit_log() -> Path | None:
    """Append-only JSONL request log (env PYNCBI_AUDIT_LOG)."""
    raw = os.environ.get("PYNCBI_AUDIT_LOG", "").strip()
    return Path(raw) if raw else None


def _get_default_agent_mode() -> bool:
    """Detect whether running in non-interactive / AI agent mode."""
    env_agent = os.environ.get("PYNCBI_AGENT_MODE", "").strip().lower()
    if env_agent in ("1", "true", "yes"):
        return True
    if env_agent in ("0", "false", "no"):
        return False
    return not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty())


@dataclass
class Config:
    """PyNCBI runtime configuration."""

    cache_folder: Path = field(default_factory=_get_default_cache_folder)
    downloads_folder: Path = field(default_factory=lambda: Path.home() / "Downloads")
    request_timeout: float = field(default_factory=_get_default_timeout)
    max_retries: int = 3
    retry_backoff: float = 0.5
    api_key: str | None = field(default_factory=_get_default_api_key)
    email: str | None = field(default_factory=_get_default_email)
    rate_limit_per_sec: float | None = None
    geo_rate_limit_per_sec: float = 8.0
    ftp_rate_limit_per_sec: float = 20.0
    agent_mode: bool = field(default_factory=_get_default_agent_mode)
    max_download_mb: float = field(default_factory=_get_default_max_download_mb)
    http_cache_ttl: float = field(default_factory=_get_default_http_cache_ttl)
    audit_log_path: Path | None = field(default_factory=_get_default_audit_log)

    # NCBI GEO & Entrez API URLs
    NCBI_QUERY_BASE_URL: ClassVar[str] = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
    NCBI_QUERY_URL: ClassVar[str] = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc="
    NCBI_QUERY_URL_TEMPLATE: ClassVar[str] = (
        "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?"
        "acc={accession}&targ=self&form=text&view=quick"
    )
    NCBI_BATCH_GSM_URL_TEMPLATE: ClassVar[str] = (
        "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?"
        "acc={accession}&targ=gsm&form=text&view=brief"
    )
    NCBI_DATA_URL_TEMPLATE: ClassVar[str] = (
        "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?"
        "acc={accession}&targ=self&form=text&view=data"
    )
    NCBI_GEO_DOWNLOAD_URL: ClassVar[str] = "https://www.ncbi.nlm.nih.gov/geo/download/"
    NCBI_EUTILS_BASE_URL: ClassVar[str] = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    NCBI_DATASETS_BASE_URL: ClassVar[str] = "https://api.ncbi.nlm.nih.gov/datasets/v2"
    NCBI_FTP_HTTPS_BASE: ClassVar[str] = "https://ftp.ncbi.nlm.nih.gov"
    NCBI_BLAST_URL: ClassVar[str] = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"
    ENA_PORTAL_API_URL: ClassVar[str] = "https://www.ebi.ac.uk/ena/portal/api"

    def __post_init__(self) -> None:
        if isinstance(self.cache_folder, str):
            self.cache_folder = Path(self.cache_folder)
        if isinstance(self.downloads_folder, str):
            self.downloads_folder = Path(self.downloads_folder)
        if isinstance(self.audit_log_path, str):
            self.audit_log_path = Path(self.audit_log_path)

    @property
    def effective_rate_limit(self) -> float:
        """Effective requests per second limit for NCBI E-Utilities."""
        if self.rate_limit_per_sec is not None and self.rate_limit_per_sec > 0:
            return self.rate_limit_per_sec
        return 9.5 if self.api_key else 2.8

    @property
    def local_downloads_folder(self) -> Path:
        """Alias for downloads_folder."""
        return self.downloads_folder

    def ensure_cache_dir(self) -> Path:
        """Ensure cache directory exists and return its path."""
        self.cache_folder.mkdir(parents=True, exist_ok=True)
        return self.cache_folder

    def get_ncbi_query_url(self, accession: str) -> str:
        """Get the NCBI SOFT query URL for an accession."""
        return self.NCBI_QUERY_URL_TEMPLATE.format(accession=accession.strip())

    def get_ncbi_batch_gsm_url(self, gse_id: str) -> str:
        """Get the single-request batch GSM metadata URL for a GSE."""
        return self.NCBI_BATCH_GSM_URL_TEMPLATE.format(accession=gse_id.strip())

    def get_ncbi_data_url(self, accession: str) -> str:
        """Get the NCBI data table URL for a GSM or GPL."""
        return self.NCBI_DATA_URL_TEMPLATE.format(accession=accession.strip())

    def get_gds_soft_url(self, gds_id: str, full: bool = False) -> str:
        """Construct the HTTPS URL for a GEO DataSet (GDS) SOFT file on NCBI FTP."""
        gds_id = gds_id.strip().upper()
        prefix = gds_id[:-3] + "nnn" if len(gds_id) > 6 else "GDSnnn"
        suffix = "_full.soft.gz" if full else ".soft.gz"
        return f"{self.NCBI_FTP_HTTPS_BASE}/geo/datasets/{prefix}/{gds_id}/soft/{gds_id}{suffix}"

    def get_series_matrix_url(self, gse_id: str, platform_id: str | None = None) -> str:
        """Construct the HTTPS URL for a GSE Series Matrix file on NCBI FTP."""
        gse_id = gse_id.strip().upper()
        prefix = gse_id[:-3] + "nnn" if len(gse_id) > 6 else "GSEnnn"
        if platform_id:
            filename = f"{gse_id}-{platform_id.strip().upper()}_series_matrix.txt.gz"
        else:
            filename = f"{gse_id}_series_matrix.txt.gz"
        return f"{self.NCBI_FTP_HTTPS_BASE}/geo/series/{prefix}/{gse_id}/matrix/{filename}"


_config: Config | None = None
_scoped_config: ContextVar[Config | None] = ContextVar("pyncbi_config", default=None)


def get_config() -> Config:
    """Get the active configuration: the context-scoped one if any, else the global instance.

    `Session` (and `config_scope`) install a configuration for the current context, so two
    workflows in one process can use different API keys, cache directories and budgets.
    Library-spawned worker threads see the process-wide configuration; anything a session
    needs inside those threads travels on its `NCBIClient` instead.
    """
    scoped = _scoped_config.get()
    if scoped is not None:
        return scoped
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(config: Config) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config


def reset_config() -> None:
    """Reset configuration to defaults."""
    global _config
    _config = None


@contextlib.contextmanager
def config_scope(config: Config) -> Iterator[Config]:
    """Use `config` for every `get_config()` call in this context (nestable, restored on exit)."""
    token = _scoped_config.set(config)
    try:
        yield config
    finally:
        _scoped_config.reset(token)


def derive_config(base: Config | None = None, **overrides: object) -> Config:
    """Copy a configuration, applying only the overrides that were given (not None)."""
    source = base if base is not None else Config()
    clean = {k: v for k, v in overrides.items() if v is not None}
    return replace(source, **clean)  # type: ignore[arg-type]
