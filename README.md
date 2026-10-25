<p align="center">
  <img src="https://raw.githubusercontent.com/MuteJester/PyNCBI/main/assets/pyncbi-logo.svg" alt="PyNCBI" width="480">
</p>

# PyNCBI

[![PyPI](https://img.shields.io/pypi/v/PyNCBI)](https://pypi.org/project/PyNCBI/)
[![Python](https://img.shields.io/pypi/pyversions/PyNCBI)](https://pypi.org/project/PyNCBI/)
[![CI](https://github.com/MuteJester/PyNCBI/actions/workflows/python-publish.yml/badge.svg?branch=main)](https://github.com/MuteJester/PyNCBI/actions/workflows/python-publish.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/MuteJester/PyNCBI/blob/main/LICENSE)

Python tools, a command-line interface, and an MCP server for discovering NCBI studies,
inspecting sample metadata, and downloading scientific data.

[Documentation](https://mutejester.github.io/PyNCBI/) ·
[Migration to 1.0](https://mutejester.github.io/PyNCBI/migration.html) ·
[Issues](https://github.com/MuteJester/PyNCBI/issues) ·
[Changelog](https://github.com/MuteJester/PyNCBI/blob/main/CHANGELOG.md)

PyNCBI 1.0.0 provides a Python API, CLI and MCP server using only Python's standard
library. Upgrading from an earlier version? Read the [migration guide](https://mutejester.github.io/PyNCBI/migration.html)
for the native data API and other breaking changes.

## Install

```bash
pip install PyNCBI
```

For the development version, clone this repository and run `pip install -e ".[dev,docs]"`.
Python 3.10 remains a legacy compatibility target. CI also checks newer Python versions;
see [CONTRIBUTING.md](https://github.com/MuteJester/PyNCBI/blob/main/CONTRIBUTING.md) for the release validation policy.

There are **no third-party runtime dependencies**. PyNCBI uses only the Python standard library. An NCBI API key
is optional. Configure it with `NCBI_API_KEY`; use `NCBI_EMAIL` for your contact address.

## Find a study, inspect it, then fetch the data

```bash
pyncbi search geo "fibromyalgia methylation" --organism "Homo sapiens"
pyncbi inspect GSE85506 --markdown
pyncbi samples GSE85506 -o samples.csv
pyncbi files GSE85506

# Review the download plan and sizes first.
pyncbi download GSE85506 --source supp --dry-run --json

# Download the matching samples' files with a transfer budget.
pyncbi download GSE85506 --where "group == 'case'" --pattern "*.idat.gz" --max-download-mb 500 -o ./fm_idats/

# Resolve a processed matrix and write its aligned sample sheet too.
pyncbi expression GSE152418 -o counts.csv
```

Sample metadata uses GEO's batch view rather than making a separate request for every
sample. File previews read a bounded prefix. Actual latency depends on the study,
server, network, rate limits, and cache state.

## Python

```python
import PyNCBI

with PyNCBI.Session(cache_dir="./pyncbi-cache", max_download_mb=500) as session:
    report = session.inspect_accession("GSE152418")
    samples, characteristics, summary = session.extract_gse_samples("GSE152418")
    result = session.load_expression("GSE152418", save_to="counts.csv")
    print(result.source, len(result.matrix["rows"]))
    print(result.matrix["columns"])
```

The module-level functions expose the same operations. `GSE`, `GSM`, and `GEOReader`
remain available as object-oriented facades.

## Other workflows

| Task | Entry point |
| --- | --- |
| Harmonized sample metadata, provenance, completeness checks | `pyncbi metadata`, `pyncbi samples --harmonize` |
| Study overview, sample sheet, file inventory and linked papers | `pyncbi bundle` |
| Compare candidate studies | `pyncbi screen` |
| Series Matrix, RNA-seq counts and platform/gene annotation | `pyncbi download`, `pyncbi gpl` |
| PubMed abstracts, PMC Methods and citations | `pyncbi pubmed` |
| SRA runs, FASTQ files and FASTA sequences | `pyncbi sra`, `pyncbi fastq`, `pyncbi fasta` |
| ClinVar/dbSNP, assemblies, taxonomy and cross-database links | `pyncbi variant`, `pyncbi genome`, `pyncbi taxonomy`, `pyncbi link` |
| Sequence similarity | `pyncbi blast` |

Run `pyncbi --help` or `pyncbi <command> --help` for the installed version's options.
The docs contain worked examples and the generated CLI/API references.

## Data and output contracts

Raw downloads preserve the hosted bytes. Parsed tables are plain Python dictionaries:

```python
{"columns": ["feature_id", "GSM1"], "rows": [["0001234", 9007199254740993]]}
```

IDs stay strings, counts stay exact Python integers even beside missing values, and decimal
measurements use Python floats. JSON exports use `null` for missing/non-finite values.
JSON, CSV and TSV are built in. DataFrame conversion, YAML, Parquet and analysis belong to
the caller; PyNCBI never installs an analysis library.

For large files, consume rows without building a full table:

```python
from contextlib import closing

# A downloaded file or an HTTPS URL can be used here.
with closing(PyNCBI.iter_supplementary_records("counts.tsv.gz")) as rows:
    for record in rows:
        print(record)
        break
```

Use `--json` for an agent envelope and `-o` for the complete saved result. Terminal/agent
previews can be truncated. Metadata harmonization preserves raw fields and records the
normalization rule and confidence. The 0–100 metadata score measures completeness and
consistency; it does not assess scientific validity.

`PYNCBI_MAX_DOWNLOAD_MB` sets the default transfer budget (2048 MB; `0` disables it).
Download operations share a budget across nested calls and workers, and enforce it while
reading bodies even when sizes are unknown. `--max-download-mb` overrides it where exposed.
Metadata requests made inside an operation also count. A size plan is an estimate when
servers omit sizes. The limit counts bytes delivered by the HTTP transport, not expanded archive
size, exported CSV size, or total lifetime session traffic. Raw file writes check free space.

## Agents and MCP

```bash
pyncbi mcp                         # stdio
pyncbi mcp --http --port 8765       # local Streamable HTTP endpoint /mcp
pyncbi schema mcp                  # current tool, resource and prompt manifest
pyncbi skill install --target codex  # or --target claude / cursor
```

```json
{
  "mcpServers": {
    "pyncbi": {"command": "pyncbi", "args": ["mcp"]}
  }
}
```

After installing, connect the MCP server once in your client:

```bash
codex mcp add pyncbi -- pyncbi mcp
claude mcp add --transport stdio --scope user pyncbi -- pyncbi mcp
```

Choose the command for your client. The skill installer includes supporting workflow
references; it does not register an MCP server. The [agent setup guide](https://mutejester.github.io/PyNCBI/agents.html)
covers executable paths, repository skills, long-operation timeouts and output-file access.

Set `PYNCBI_AGENT_MODE=1` to suppress colors/progress. MCP errors include `error_code`,
`hint`, and `retryable`. HTTP clients initialize a session and send its `Mcp-Session-Id`
on subsequent requests. Each POST contains one JSON-RPC message. Network-facing hosting
requires your own authentication; the default address is loopback.

To run a local container, first build it:

```bash
docker build -t pyncbi .
docker run --rm -i pyncbi mcp
```

## Development and citation

See [CONTRIBUTING.md](https://github.com/MuteJester/PyNCBI/blob/main/CONTRIBUTING.md) for tests, generated artifacts and release steps.
[CITATION.cff](https://github.com/MuteJester/PyNCBI/blob/main/CITATION.cff) contains the software citation. PyNCBI is distributed under
the [MIT license](https://github.com/MuteJester/PyNCBI/blob/main/LICENSE).
