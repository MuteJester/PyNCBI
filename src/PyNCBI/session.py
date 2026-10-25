"""Isolated PyNCBI sessions.

A `Session` bundles everything that should *not* be shared between concurrent workflows: its
own configuration (API key, email, cache directory, download budget), its own `NCBIClient`
with its own rate limiters and telemetry, and its own metadata store. Every networked verb of
the public API is available as a method on the session, with the session's client and budget
applied automatically:

    with PyNCBI.Session(api_key=KEY, cache_dir="./agent-1", max_download_mb=500) as s:
        record = s.build_study_record("GSE152418")
        matrix = s.load_expression("GSE152418")

The module-level functions keep working exactly as before and use the process-wide
configuration, so nothing has to change to adopt sessions.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyNCBI.core.config import Config, config_scope, derive_config
from PyNCBI.io.http_client import NCBIClient, NCBIClientConfig

if TYPE_CHECKING:
    from collections.abc import Callable

    from PyNCBI.metadata.store import MetadataStore

# Modules whose public functions become session verbs
_VERB_MODULES: tuple[str, ...] = ("PyNCBI.domains", "PyNCBI.metadata")
_INJECTED_CLIENT_PARAM = "http_client"
_BUDGET_PARAM = "max_download_mb"

_registry_lock = threading.Lock()
_verb_cache: dict[str, Callable[..., Any]] | None = None


def _verb_registry() -> dict[str, Callable[..., Any]]:
    """Public functions that take an `http_client`, i.e. everything that talks to NCBI."""
    global _verb_cache
    with _registry_lock:
        if _verb_cache is not None:
            return _verb_cache
        found: dict[str, Callable[..., Any]] = {}
        for module_name in _VERB_MODULES:
            module = importlib.import_module(module_name)
            for name in getattr(module, "__all__", ()):
                candidate = getattr(module, name, None)
                if not inspect.isfunction(candidate):
                    continue
                try:
                    params = inspect.signature(candidate).parameters
                except (TypeError, ValueError):  # pragma: no cover - builtins
                    continue
                if _INJECTED_CLIENT_PARAM in params:
                    found[name] = candidate
        _verb_cache = found
        return found


class Session:
    """An isolated PyNCBI workflow: its own credentials, caches, budget, client and telemetry.

    Args:
        api_key: NCBI API key for this session only (raises its E-Utilities rate to 10 req/s)
        email: Contact address sent to E-Utilities for this session
        cache_dir: Cache root for this session (study records, annotation and platform tables)
        downloads_dir: Default downloads folder
        max_download_mb: Download budget applied to every transfer this session makes
        request_timeout: HTTP timeout in seconds
        audit_log: Path to an append-only JSONL log of this session's NCBI requests
        agent_mode: Suppress progress bars and colors
        rate_limit_per_sec: Override the E-Utilities rate limit
        name: Label used in `repr` and telemetry (e.g. the agent or task id)
        config / client: Supply a prepared `Config` / `NCBIClient` instead of building them
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        email: str | None = None,
        cache_dir: str | Path | None = None,
        downloads_dir: str | Path | None = None,
        max_download_mb: float | None = None,
        request_timeout: float | None = None,
        audit_log: str | Path | None = None,
        agent_mode: bool | None = None,
        rate_limit_per_sec: float | None = None,
        name: str = "",
        config: Config | None = None,
        client: NCBIClient | None = None,
    ) -> None:
        self.name = name
        self._closed = False
        self._explicit_budget = max_download_mb
        self.config = config or derive_config(
            api_key=api_key,
            email=email,
            cache_folder=Path(cache_dir) if cache_dir is not None else None,
            downloads_folder=Path(downloads_dir) if downloads_dir is not None else None,
            max_download_mb=max_download_mb,
            request_timeout=request_timeout,
            audit_log_path=Path(audit_log) if audit_log is not None else None,
            agent_mode=agent_mode,
            rate_limit_per_sec=rate_limit_per_sec,
        )
        self.config.ensure_cache_dir()
        if client is not None:
            self.client = client
        else:
            self.client = NCBIClient(
                NCBIClientConfig(
                    timeout=self.config.request_timeout,
                    rate_limit_per_sec=self.config.rate_limit_per_sec,
                    geo_rate_limit_per_sec=self.config.geo_rate_limit_per_sec,
                    ftp_rate_limit_per_sec=self.config.ftp_rate_limit_per_sec,
                ),
                app_config=self.config,
            )
        self._store: MetadataStore | None = None

    # --- identity -------------------------------------------------------------------------

    @property
    def cache_dir(self) -> Path:
        """This session's cache root."""
        return Path(self.config.cache_folder)

    @property
    def max_download_mb(self) -> float | None:
        """The per-transfer budget this session injects (None when it was not set)."""
        return self._explicit_budget

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def store(self) -> MetadataStore:
        """This session's private metadata store (TTL-cached study records)."""
        if self._store is None:
            from PyNCBI.metadata.store import MetadataStore

            self._store = MetadataStore(self.cache_dir / "metadata")
            self._store.session = self  # type: ignore[attr-defined]
        return self._store

    # --- verbs ----------------------------------------------------------------------------

    @staticmethod
    def verbs() -> list[str]:
        """Names of the API functions available as methods on a session."""
        return sorted(_verb_registry())

    def _bind(self, name: str, func: Callable[..., Any]) -> Callable[..., Any]:
        params = inspect.signature(func).parameters
        wants_client = _INJECTED_CLIENT_PARAM in params
        wants_budget = _BUDGET_PARAM in params

        @functools.wraps(func)
        def verb(*args: Any, **kwargs: Any) -> Any:
            if self._closed:
                raise RuntimeError(
                    f"This PyNCBI session is closed; create a new Session to call {name}()"
                )
            if wants_client:
                kwargs.setdefault(_INJECTED_CLIENT_PARAM, self.client)
            if wants_budget and self._explicit_budget is not None:
                kwargs.setdefault(_BUDGET_PARAM, self._explicit_budget)
            with config_scope(self.config):
                return func(*args, **kwargs)

        verb.__doc__ = (func.__doc__ or "") + "\n\nBound to this PyNCBI session."
        return verb

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        func = _verb_registry().get(name)
        if func is None:
            raise AttributeError(
                f"Session has no verb '{name}'. Available verbs: {', '.join(self.verbs())}"
            )
        bound = self._bind(name, func)
        object.__setattr__(self, name, bound)  # memoize on the instance
        return bound

    def __dir__(self) -> list[str]:
        return sorted({*super().__dir__(), *self.verbs()})

    # --- diagnostics ----------------------------------------------------------------------

    def status(self, probe_network: bool = False) -> dict[str, Any]:
        """Health, configuration, cache and telemetry for this session."""
        with config_scope(self.config):
            return self.client.check_health(probe_network=probe_network)

    def metrics(self, include_recent: int = 5) -> dict[str, Any]:
        """Request telemetry for this session's client only."""
        return self.client.metrics.summary(include_recent=include_recent)

    def to_dict(self) -> dict[str, Any]:
        summary = self.metrics(include_recent=0)
        return {
            "name": self.name,
            "cache_dir": str(self.cache_dir),
            "api_key_configured": bool(self.config.api_key),
            "email_configured": bool(self.config.email),
            "max_download_mb": self._explicit_budget,
            "effective_eutils_rate_limit_per_sec": self.config.effective_rate_limit,
            "audit_log": str(self.config.audit_log_path) if self.config.audit_log_path else None,
            "closed": self._closed,
            "total_requests": summary["total_requests"],
            "total_bytes_received": summary["total_bytes_received"],
        }

    # --- lifecycle ------------------------------------------------------------------------

    def close(self) -> None:
        """Close the session's HTTP connection pool. Verbs raise afterwards."""
        if not self._closed:
            self.client.close()
            self._closed = True

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def __repr__(self) -> str:
        label = f" name={self.name!r}" if self.name else ""
        key = "api_key" if self.config.api_key else "no-api-key"
        budget = f" budget={self._explicit_budget}MB" if self._explicit_budget else ""
        state = " closed" if self._closed else ""
        return f"Session({key}{label} cache={self.cache_dir}{budget}{state})"
