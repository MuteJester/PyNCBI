---
name: pyncbi
description: Search NCBI datasets and papers, inspect GEO sample metadata, resolve expression matrices, and retrieve sequence or study files with PyNCBI. Use for NCBI retrieval involving GEO accessions, PubMed/PMC, SRA, Gene, variants, taxonomy or assemblies; analysis of already-local data does not require this skill.
---

# PyNCBI

Use PyNCBI for NCBI retrieval and preserve the user's choice of analysis tools.

## Choose the available interface

- If PyNCBI MCP tools are connected, use those tools directly. No local package
  installation is needed merely to call a connected server.
- For local automation or processing rows incrementally, use the installed `pyncbi`
  CLI or Python API. Check `pyncbi --version` and `pyncbi <command> --help` when needed.
- If neither interface is available, explain the setup needed. Install `PyNCBI`
  in the user's intended Python environment when package installation is authorized.
  PyNCBI has no third-party runtime dependencies; an NCBI API key is optional.

For CLI calls, use `--json` for machine-readable output and `-o` for complete files.
`pyncbi schema mcp` describes the installed tool contract; do not invent parameters.

## Select a workflow

| Goal | MCP tool | CLI command |
| --- | --- | --- |
| Find studies, papers or genes | `ncbi_search` | `pyncbi search` |
| Compare candidate GEO studies | `ncbi_screen` | `pyncbi screen` |
| Inspect metadata and available files | `ncbi_inspect` | `pyncbi inspect` |
| Read/filter sample characteristics | `ncbi_samples` | `pyncbi samples` |
| Harmonize metadata with provenance | `ncbi_metadata` | `pyncbi metadata` |
| Preview a file without fetching it all | `ncbi_preview` | `pyncbi preview` |
| Resolve a matrix and aligned samples | `ncbi_expression` | `pyncbi expression` |
| Plan or download GEO files | `ncbi_download` | `pyncbi download` |
| Retrieve paper Methods | `ncbi_pubmed` | `pyncbi pubmed` |
| Retrieve SRA runs or FASTQ links | `ncbi_sra`, `ncbi_fastq` | `pyncbi sra`, `pyncbi fastq` |

Start with the smallest operation that answers the question. Search when no accession
is known; inspect metadata before choosing files or cohort filters. Use `ncbi_bundle`
only when the user needs an overview, samples, files and linked papers together.

Read [references/workflows.md](references/workflows.md) for concrete cohort,
matrix, literature, sequence, variant and BLAST workflows, or for error recovery.

## Data and output

- Metadata is dictionaries/lists. Parsed tables are ordinary Python dictionaries:
  `{"columns": ["feature_id", "GSM1"], "rows": [["0001", 9007199254740993]]}`.
  IDs remain strings, counts remain Python integers, missing measurements are `None`,
  and decimal measurements are binary64 floats. Leave analysis and other formats to callers.
- Built-in exports are JSON, CSV and TSV. Use `iter_supplementary_records` for large
  local/remote delimited files; close the iterator when stopping early.
- CLI/MCP envelopes use schema `2.0`. Read `ok`, `data`, `warnings`, `saved_files`,
  `error_code`, `hint` and `retryable`. Study-record schema `1.0` is a separate contract.
- Previews are incomplete by design. Follow `next_page_token` for sample pages;
  honor `_truncated` markers, and save complete output when needed. Never interpret
  a shortened preview as the full cohort or claim that a preview was downloaded in full.
- Files are written on the MCP server's filesystem. A remote server's `saved_files`
  paths are not automatically accessible on the agent's computer.

## Transfers and scientific interpretation

For raw-file retrieval, call `ncbi_download` with `dry_run=true` first (CLI:
`pyncbi download --dry-run`) to inspect candidates and sizes. Apply the user's transfer
budget using `max_download_mb`; the default is 2048 MB per operation, shared by nested
requests and workers. Unknown sizes remain estimates. Plans and download budgets
do not override the user's requested scope or the host's permissions.

Build cohort filters from returned field names and values. Metadata quality scores
describe completeness/consistency; they do not establish scientific validity.
Keep the selected source, accession, raw characteristics and annotation provenance.
Report a missing source or field rather than substituting an unrelated study.

Large transfers can exceed MCP client timeouts. Inspect existing output before retrying;
do not assume a client timeout cancelled server-side work. Prefer BLAST's `wait=false`
and RID polling for long sequence searches.
