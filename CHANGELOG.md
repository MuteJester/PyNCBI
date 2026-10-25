# Changelog

All notable changes to PyNCBI are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased] — 1.0.0

First release of the redesigned PyNCBI: a programmatic, agent-ready engine, CLI (`pyncbi`) and MCP server
(`pyncbi mcp`) for NCBI. Architecture: `core/`, `domains/`, `io/`, `cli/`, `mcp/`; `NCBIClient` with
realm-aware rate limiting; `inspect_accession`, `extract_gse_samples`, `preview_geo_file`,
`download_geo_files`, Series Matrix / RNA-seq loaders, Entrez search, PubMed, Gene, SRA, FASTA, and the
agent skill — plus everything below.

### Added
- **Isolated sessions** (`PyNCBI.Session`, `config_scope`, `derive_config`): per-workflow API key, email,
  cache directory, download budget, timeout, audit log, rate limiters, metadata store and telemetry. Every
  networked function is available as a session method with the session's client and budget injected.
  `NCBIClient` can pin its own app config, so credentials stay correct inside worker threads.
- **HTTP response cache**: identical metadata responses (GEO `acc.cgi`, E-Utilities, Datasets, ENA) are
  reused for `Config.http_cache_ttl` seconds (env `PYNCBI_HTTP_CACHE_TTL`, default 300, 0 disables), with
  per-client LRU bounds, hit/miss telemetry in `client.metrics.summary()` and `clear_response_cache()`.
  BLAST polling, file bodies, byte ranges and streams are never cached. Measured effect on a three-step
  GSE152418 workflow: 9 requests and 7.7 s down to 4 requests and 3.1 s.
- **MCP schema integrity**: every tool now declares the agent envelope as its `outputSchema`,
  `ncbi_samples` always carries that envelope, `mcp_manifest()` / `pyncbi schema mcp` print the whole
  agent contract, and `PyNCBI.mcp.validation` checks the schemas structurally and proves (from the
  handler's own source) that no tool advertises an argument it ignores or reads one it never documented.
- **BLAST** (`blast`, `submit_blast`, `check_blast`, `fetch_blast`, `delete_blast`, `parse_blast_xml`,
  `pyncbi blast`, MCP `ncbi_blast`): the asynchronous BLAST URL API lifecycle handled end to end -
  submit, poll (with a 10-second floor against NCBI and a dedicated `blast` rate-limiter realm), fetch
  and parse into `BlastHit` records (accession, title, bit score, E-value, percent identity, coverage)
  plus a hits DataFrame; `wait=False` / `--rid` / `rid=` let callers avoid long blocking calls, and a
  timeout always names the RID so a job is never lost.
- **Full MCP protocol surface**: behaviour annotations on all tools (`readOnlyHint`, `destructiveHint`,
  `idempotentHint`, `openWorldHint`), `structuredContent` on every tool result (success and error),
  protocol-version negotiation, server `instructions`, resources (`pyncbi://schema/{study,sample}`,
  `pyncbi://vocabulary`) plus resource templates (`pyncbi://study/{gse_id}/overview|samples|record`,
  `pyncbi://platform/{gpl_id}/table`), workflow prompts (`find_dataset`, `study_deep_dive`,
  `cohort_download`, `paper_methods`), and a stdlib Streamable HTTP transport
  (`pyncbi mcp --http`, `POST /mcp`) with loopback-origin checks, session ids and single-message JSON-RPC.
- **Cross-study screening** (`screen_studies`, `pyncbi screen`, MCP `ncbi_screen`): a GEO query or a list of
  accessions becomes one comparison table (sample count, available data sources, exact download bytes,
  harmonized-field coverage, quality score), with `require=` keeping only studies that carry the needed fields
  and per-study failures reported instead of aborting.
- **Rails for autonomous use**: `plan_geo_download` / `pyncbi download --dry-run` / MCP `dry_run` report what
  would be fetched without downloading; `max_download_mb` (env `PYNCBI_MAX_DOWNLOAD_MB`, default 2048) and a
  free-disk-space check refuse oversized transfers; `PYNCBI_AUDIT_LOG` appends one JSON line per NCBI request
  with API keys redacted.
- **Analysis-ready matrix resolver** (`load_expression`, `pyncbi expression`, MCP `ncbi_expression`): one call
  resolves the expression matrix across NCBI-generated counts, Series Matrix, tabular supplementary files,
  per-sample supplementary tables, GSM on-page tables and `*_RAW.tar` members; maps columns back to GSM IDs
  (accession, embedded accession, sample title or another identifying field), returns the aligned sample sheet,
  the chosen source and every candidate skipped with size and reason, with a `max_download_mb` guard.
- **Metadata layer** (`PyNCBI.metadata`): versioned `StudyRecord` / `SampleRecord` (`schema_version`, published
  JSON Schema via `pyncbi schema`), conservative controlled vocabulary with provenance and confidence on every
  normalized value (`harmonize_characteristics`, `vocabulary.register_*`), quality scoring with typed issues
  (`assess_study`), `build_study_record` (+ BioSample/SRA/PubMed enrichment), TTL `MetadataStore` keyed by
  content hash, checksummed on-disk layout (`save_study_record` / `load_study_record`), paginated and
  size-bounded serving (`paginate`, `bounded_payload`); `pyncbi metadata`, `pyncbi samples --harmonize`,
  MCP `ncbi_metadata`; bundles now include the record layout.
- **Gene & platform annotation**: `fetch_gpl_table`, `load_rnaseq_annotation`, `annotate_gene_ids`,
  `annotate_probes`, `annotate=True` on `load_rnaseq_counts` / `load_series_matrix`, `pyncbi gpl`,
  `pyncbi download --annotate`. Source cascade: local cache -> GEO annotation file -> NCBI Datasets ->
  Entrez Gene.
- **Cohort-filtered & parallel downloads**: `download_geo_files(where=...)`, `download_many`,
  `resolve_cohort_files`, `pyncbi download GSE --where ...`, multiple accessions, `--workers`,
  `--subseries` (SuperSeries fan-out).
- **PMC full text**: `fetch_pmc_text`, `resolve_pmc_id`, `attach_full_text`, `pyncbi pubmed
  --methods/--fulltext/--sections`, MCP `ncbi_pubmed` sections; data-availability extraction even when
  journals hide it in Acknowledgments.
- **Study bundles**: `build_study_bundle`, `pyncbi bundle`, MCP `ncbi_bundle` (STUDY_OVERVIEW.md,
  samples_metadata.csv, files_manifest.json, sra_runs.csv, pubmed_articles.json, paper_methods.md,
  optional annotated data table, SubSeries expansion).
- **Variants**: `search_clinvar`, `fetch_clinvar`, `fetch_dbsnp`, `fetch_variants`, `pyncbi variant`
  / `pyncbi clinvar`, MCP `ncbi_variant` with clean `ClinVarRecord` / `DbSNPRecord`.
- **Reads**: `fetch_fastq_urls`, `download_fastq` (ENA mirror, MD5-verified, parallel), `pyncbi fastq`,
  MCP `ncbi_fastq`.
- **Genomes**: `search_assemblies`, `fetch_assembly`, `download_genome` (NCBI Datasets, safe zip
  extraction), `pyncbi genome search|download`, MCP `ncbi_genome`.
- **Structured records** for BioSample (attributes unpacked), BioProject, Nucleotide/Protein and
  Taxonomy (full lineage): `fetch_biosamples`, `fetch_bioprojects`, `fetch_taxonomy`, `search_taxonomy`,
  `pyncbi taxonomy`, MCP `ncbi_taxonomy`; `search_ncbi` now returns clean records (`raw=True` for the
  original esummary).
- **GEO DataSets**: `load_gds`, `pyncbi gds`, `inspect`/`download` accept `GDS` accessions.
- **Citations**: `to_bibtex`, `to_ris`, `export_citations`, `pyncbi pubmed --format bibtex|ris`,
  `-o refs.bib`.
- **Pagination**: `esearch_ids` / `esummary_docs`; `limit=None` (CLI `search --all`) returns every match.
- **Agent envelope**: `schema_version`, `pyncbi_version`, `elapsed_ms`, `request_count`; stable
  `error_code` taxonomy + `retryable` in CLI `--json` errors and MCP error results
  (`error_code_for`, `error_payload`, `is_retryable`).
- `NCBIClient.stream_lines` (gzip-aware streaming with progress), `BotChallengeError` with automatic
  GEO session warm-up and retry, `extract_zipfile`, console-safe output on narrow encodings,
  argparse "did you mean" suggestions, `--no-color`, richer `--version`.

- **Reviewable module layout**: the four modules that had grown past review size are now packages of
  focused modules, and the import surface for each is unchanged. `PyNCBI.domains.geo` splits into `urls`,
  `transfer`, `resolve`, `planning`, `samples`, `inspection`, `tables`, `downloads` and `preview`;
  `PyNCBI.mcp.server` into `tools`, `handlers`, `resources`, `prompts`, `protocol` and `transport`;
  `PyNCBI.cli.main` into `console`, `parser` and five command modules; `PyNCBI.io.http_client` sheds
  `urls`, `responses` and `telemetry`. `tests/test_architecture.py` enforces a 1200-line ceiling, a
  one-way layer order (`core` -> `io` -> `parsing` -> `domains` -> `metadata` -> `geo` -> `mcp` -> `cli`)
  and a pinned public API surface (`tests/fixtures/api_surface.json`, regenerated by
  `scripts/gen_api_surface.py`).
- **One definition per shared idiom**: `PyNCBI.core.types.TABULAR_EXTENSIONS` / `is_tabular_file` and
  `PyNCBI.io.urls.supplementary_urls` replace the copies of those rules that had accumulated across the
  object layer, the parsers, the metadata builder and the GEO engine;
  `PyNCBI.domains.geo.samples.fetch_sample_tables` / `combine_sample_tables` are now the single
  request-and-assemble step behind the sample table, cohort file resolution and the `GSE` object, so
  `GSE.samples_df` and `extract_gse_samples` can no longer disagree.

### Fixed
- Standardize measurement parsing across expression, supplementary files, previews, GSM,
  Series Matrix and GDS; fix GDS decimal rounding and preserve literal `NA` feature IDs.
- Share decoded filename/pattern handling across selectors, planning, preview and download,
  including GEO `file=` query parameters.
- Route annotation persistence through the shared atomic typed cache; record completeness
  explicitly instead of guessing from table size. Old annotation TSV caches are ignored.
- Use strict JSON consistently for saved studies, bundles, audit events and canonical hashes;
  enforce HTTP, worker-context and measurement-parser ownership with architecture tests.
- Apply the shared operation budget during preflight instead of consulting a competing
  global/per-file limit when an explicitly configured client or enclosing operation owns it.
- Consolidate GSE/GSM retrieval behind typed GEO loaders, shared matrix alignment and sample-table parsing;
  AUTO uses the functional expression resolver while explicit modes remain strict.
- Separate object metadata from source/file-specific data caches, invalidate ambiguous old snapshots,
  bypass data caching for arbitrary selectors, and avoid caching partial results as complete data.
- Remove object-level retry loops and propagate systemic failures; report record-local failures explicitly.
- Preserve full precision in GSM on-page tables (remove the float32 downcast), and never treat numeric
  feature identifiers or file manifests as measurements.
- Make cache staging files unique so concurrent writes to the same accession remain atomic.
- Preserve full binary64 precision, identifiers, missing values and dtypes in cached tables;
  invalidate older rounded table-cache entries.
- Include feature/sample identifiers in JSON table exports and emit strict JSON with null
  for missing/non-finite values.
- Preserve numeric-looking feature identifiers (including leading zeros) when parsing tables.
- Enforce transfer budgets while reading all response bodies and share operation budgets across
  parallel downloads, FASTQ, genomes, nested loaders and SuperSeries downloads.
- Validate MCP requests and HTTP sessions; reject malformed messages without terminating stdio,
  enforce session termination, and reject wildcard browser origins.
- Include development helpers, fixtures, generated typing stubs and documentation in source archives.
- Avoid filesystem writes on import, including when requesting CLI help or version information.
- A remote `.parquet` supplementary table is read as parquet instead of being run through the CSV reader
  and returned as a single mangled column (`read_supplementary_table`).

### Notes
- Supports Python 3.10 - 3.15 (the floor is enforced by `vermin --target=3.10` in CI and by running the
  suite on 3.10 through 3.15 plus Windows and macOS); ships a `Dockerfile` for the MCP server and
  works with `uvx pyncbi mcp`.
- Local cache is gzip-compressed JSON (`LocalCache`, `<key>.json.gz`); pickle is never used; `GSE` / `GSM` rebuild sample objects from cached tables.
- RNA-seq assembly is resolved from the series organism everywhere (previously hard-coded to human in
  download/preview paths).
- `fetch_sra_runs` default `limit` raised to 500 and warns on truncation.
- Package is fully `mypy`-clean; `HttpClient` protocol gained `stream_lines`.

- Non-existent GEO accessions raise `AccessionNotFoundError` instead of returning an empty report.
- GPL platform tables keep every column (`parse_gpl_table_lines`); the GSM table parser is no longer
  used for platforms.
- HTML error pages received in place of tables raise a clear `DataError` instead of `BadGzipFile`.
