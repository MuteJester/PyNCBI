# Contributing to PyNCBI

Thanks for helping make NCBI easier to use programmatically. The bar for every change:
no new runtime dependencies (stdlib + pandas/numpy/requests/tqdm/termcolor only), Python 3.10+ syntax only (3.10 is a legacy compatibility target), measurements are
never altered, and agents get structured results with clear errors.

## Development setup

```bash
git clone https://github.com/MuteJester/PyNCBI && cd PyNCBI
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,docs,protocol-test]"
```

## Checks that CI runs

```bash
ruff check src tests docs/scripts scripts && ruff format --check src tests docs/scripts scripts
vermin --target=3.10 --violations --eval-annotations src/PyNCBI   # 3.10 floor
mypy src/PyNCBI
python scripts/check_mcp_client.py                              # independent SDK client
pytest -m "not integration" --cov=PyNCBI --cov-fail-under=60   # offline, uses a local HTTP server fixture
pytest -m integration                                           # live NCBI (optional locally; weekly in CI)
python docs/scripts/gen_cli_reference.py                        # regenerate docs/source/cli.rst
python scripts/gen_schemas.py                                   # regenerate the published JSON Schemas
python scripts/gen_session_stubs.py                             # regenerate typed Session methods
python scripts/sync_agent_skill.py                              # canonical skill -> installable copy
python -m sphinx -W -b html docs/source docs/_build/html        # docs must build warning-free
```

## Where code goes

Layers import strictly one way, and `tests/test_architecture.py` fails the build if that breaks:

```
core -> io -> parsing -> domains -> metadata -> geo -> PyNCBI (facade) -> mcp -> cli
```

`core` holds configuration, exceptions, models and formatting; `io` the HTTP client, caches, URL and
response helpers; `parsing` the SOFT/matrix/GDS/JATS parsers; `domains` one module (or package) per NCBI
capability; `metadata` the harmonised study record; `geo` the `GSE`/`GSM`/`GEOReader` objects, which are
facades over `domains`. Deferred imports inside a function are the escape hatch when two layers genuinely
collaborate (for example `domains` calling into `metadata`).

Two more rules the same test enforces:

- **No module over 1200 lines.** Past that, split it into a package and keep the old import path working
  as a facade (`PyNCBI.domains.geo`, `PyNCBI.mcp.server`, `PyNCBI.cli.main` all work this way).
- **The public API surface is pinned** in `tests/fixtures/api_surface.json`. Moving code between modules
  must not change it. When you intentionally add or change a public name, regenerate the snapshot with
  `python scripts/gen_api_surface.py` and say so in the pull request.

Shared rules live in exactly one place: `core.types.TABULAR_EXTENSIONS` / `is_tabular_file` decide what
counts as a readable table, `io.urls.supplementary_urls` normalises GEO supplementary file fields, and
`domains.geo.samples.fetch_sample_tables` / `combine_sample_tables` are the only way to build a sample
table. Tests fail if a second copy appears.

Choose the owner below before adding retrieval behavior. Public functions, objects, CLI commands,
and MCP handlers should call these components rather than implement another policy.

| Responsibility | Shared owner | Caller rule |
| --- | --- | --- |
| HTTP, retries, rate limits, response caching | `io.http_client.NCBIClient` | Inject the client; domains do not import networking libraries. |
| Transfer budgets and context in workers | `io.transfer_limits` | Use `bounded_transfer` and `ContextThreadPoolExecutor` so nested work shares its session and budget. |
| Filename identity and patterns | `core.files` | Interpret URL `file=` parameters before detecting formats or matching names. Use `io.urls.download_filename` before saving a remote name. |
| Measurement text parsing | `parsing.tabular` | Reuse identifier, missing-value, delimiter and exact-float handling. SOFT parsers extract their format's table, then delegate cell parsing. |
| Persistent caches | `io.cache.LocalCache` | Reuse typed JSON and atomic file access. Domain adapters own keys and completeness flags, not temporary-file writers. |
| External JSON and canonical content | `core.serialization` | Use strict numeric/null handling for files, CLI/MCP output, audit events and record hashes. Cache tags and pagination tokens have separate structural formats. |

Architecture tests enforce the networking, worker-pool and measurement-parser boundaries, in addition
to layering and API compatibility. Metadata annotation text and Entrez run-info CSV have separate
column contracts; they are explicit exceptions to measurement parsing. Partial previews and raw
downloads also serve different purposes: previews read ranges, and downloads preserve hosted bytes.
Share policy and selection without forcing those operations through a full-table download.

## How tests are written

- Offline tests never mock `requests`; they spin up a real local HTTP server (`tests/conftest.py`,
  `local_http` fixture) and point `Config` URL templates at it, so parsers and the client are exercised
  end-to-end.
- New NCBI behaviour must be probed live first (see `docs/superpowers/specs/` for the record of such
  probes), then captured as a fixture.
- Every new feature ships with: a Python API, a CLI subcommand (`--json`/`--markdown`/`-o`), an MCP tool
  when agents would call it, a README/SKILL.md example, and a CHANGELOG entry.

## Error handling conventions

Raise `PyNCBIError` subclasses with a `hint`; add new classes to the taxonomy in
`core/exceptions.py` so `error_code_for()` stays exhaustive. Never let HTML or challenge pages
reach a parser silently.

## Releasing

1. Set `__version__` in `src/PyNCBI/__init__.py`; setuptools reads this single source.
2. Update `CITATION.cff` and the changelog with the matching version and actual release date.
3. Regenerate the API snapshot, Session typing, schemas, CLI reference and agent skill.
4. Build into a clean directory and run `python scripts/check_distribution.py DIST --smoke`.
5. Run the offline suite from the unpacked source archive, and run the live checks.
6. Review the exact commit's Linux 3.10–3.15, Windows and macOS CI jobs before tagging.
7. Publish the matching GitHub release; release-event CI gates PyPI publishing on all checks.

The generated `skills/pyncbi/SKILL.md` comes from `src/PyNCBI/resources/SKILL.md`; edit only
the canonical packaged source. `requirements.txt` delegates to `pyproject.toml` rather than
maintaining a second dependency list.

See `docs/source/releasing.rst` for the required PyPI Trusted Publisher and Read the Docs
account setup. A local docs build does not rebuild the hosted site. No API key is needed
for offline validation; an NCBI key is optional for live checks.


## GEO object boundaries

`geo/gse.py` and `geo/gsm.py` provide the public convenience interface. They must not implement
HTTP calls, retry loops or source-selection policy. `domains/geo/records.py` owns typed metadata
acquisition; `studies.py` applies explicit requests and delegates AUTO to the expression resolver;
`tables.py` owns table loading; `matrices.py` owns alignment and measurement-column selection.
`geo/_cache.py` serializes/hydrates snapshots and distinguishes request identities, while
`geo/_formatting.py` renders pure summaries. New behavior belongs in the shared engine, with
object/function parity tests in `tests/test_object_engine.py`.
