---
name: pyncbi
description: Programmatic & agent-friendly toolkit, CLI (`pyncbi`), and MCP server for searching, inspecting, organizing metadata, and downloading raw/tabular data from NCBI (GEO Series/Samples/Platforms, Entrez, PubMed, Gene, BioProject, SRA). Activate whenever a task involves NCBI, GEO (`GSE`, `GSM`, `GPL`), Series Matrix, SOFT files, RNA-seq counts, DNA methylation, PubMed (`PMID`), PMC full text / Methods, Entrez Gene, gene/probe annotation (GPL), ClinVar / dbSNP variants, SRA / FASTQ reads, BioSample / BioProject / Taxonomy, genome assemblies (NCBI Datasets), or GEO DataSets (GDS).
---

# PyNCBI Skill — Agent Guide for Working with NCBI & GEO

Whenever you need to search NCBI, inspect GEO studies (`GSE`/`GSM`/`GPL`), extract sample clinical/phenotype metadata tables, download raw supplementary files or Series Matrix / RNA-seq count tables, or query PubMed and NCBI Gene, **always use `PyNCBI` (`pyncbi` CLI, `pyncbi mcp`, or `import PyNCBI`) instead of writing manual `curl`/`urllib`/`requests` scraping scripts.**

## Why Use `PyNCBI` Instead of Manual Scraping?
1. **Batch GSE Sample Metadata**: Fetching sample metadata for a 500-sample GSE via manual loops takes 500 HTTP requests. `pyncbi samples <GSE>` fetches all samples in **one batch metadata request** and automatically aligns ragged `!Sample_characteristics_ch1` `key: value` attributes into clean columns.
2. **Preflight File Inspection (No Blind Multi-GB Downloads)**: `pyncbi inspect <GSE>` checks all supplementary files, Series Matrix files, and NCBI RNA-seq count tables via HTTP `HEAD` and reports exact file sizes (`12.4 MB`, `4.2 GB`) and cohort phenotype distributions *before* you download anything.
3. **Automatic `ftp://` to `https://` & NCBI Rate Limiting**: Automatically rewrites NCBI `ftp://` links to `https://` and enforces NCBI's 3 req/s (or 10 req/s with `NCBI_API_KEY`) rate limit across threads.
4. **Zero Unwanted Post-Processing**: Downloads raw files and tables exactly as hosted on NCBI, outputting neatly formatted information either **right into the terminal** (`text`, `--markdown`, `--json`) or **saved to organized files** (`.csv`, `.tsv`, `.json`, or unpacked archives).

---

## Installation Check
If `pyncbi` is not yet installed in the environment, install it first:
```bash
pip install PyNCBI
```

---

## Recommended 3-Step Agent Workflow (`Search` → `Inspect` → `Download`)

### Step 1: Search & Discover (`pyncbi search`)
```bash
# Search GEO Series (GSE) with filters (JSON or Markdown output for agents)
pyncbi search geo "breast cancer resistance" --organism "Homo sapiens" --type rnaseq --min-samples 20 --json

# Search PubMed papers and abstracts
pyncbi search pubmed "fibromyalgia DNA methylation" --limit 5 --markdown

# Search NCBI Gene
pyncbi search gene "BRCA1" --organism "Homo sapiens" --json
```

### Step 2: Inspect Record, Sample Cohorts & Preview Remote Files Before Downloading (`pyncbi inspect`, `pyncbi samples`, `pyncbi preview`)
```bash
# Inspect a GSE, GSM, or GPL: shows study design, sample count, cohort characteristics
# distribution, SuperSeries/SubSeries relations, and all downloadable files with reported byte sizes
pyncbi inspect GSE85506 --markdown
pyncbi inspect GSE85506 --json

# View, filter (--where), or save the organized sample metadata & unpacked characteristics table (O(1) single request)
pyncbi samples GSE85506 --markdown
pyncbi samples GSE152418 --where "severity == 'Severe'" -o severe_samples.csv
pyncbi samples GSE85506 -o GSE85506_samples.json

# Stream & preview the first few rows, column names, gene ID format, or .tar archive members of a remote file from a bounded prefix
pyncbi preview GSE152418 --source supp --rows 5 --markdown
pyncbi preview GSE152418 --source rnaseq --rows 5 --markdown
```

### Step 3: Download Raw Files or Data Tables (`pyncbi download`)
```bash
# List all downloadable files and their indices/sizes for an accession
pyncbi files GSE85506

# Download and parse the GEO Series Matrix directly to JSON or CSV
pyncbi download GSE85506 --source matrix -o GSE85506_matrix.json

# Download NCBI-generated RNA-seq counts (for sequencing studies)
pyncbi download GSE152418 --source rnaseq -o GSE152418_raw_counts.tsv.gz

# Download raw supplementary file(s) as if manually downloading from NCBI
pyncbi download GSE85506 --source supp --file-index 0 -o ./GSE85506_raw/
pyncbi download GSE85506 --source supp --pattern "*.txt.gz" --unpack -o ./GSE85506_unpacked/

# Download a single GSM's on-page data table
pyncbi download GSM1518180 --source table -o GSM1518180_table.csv
```

### Step 4: Annotate, Read Methods, Bundle, Filter Cohorts & Look Up Variants
```bash
# Gene annotation: NCBI RNA-seq counts are indexed by numeric Entrez GeneID -> add Symbol/GeneType/EnsemblGeneID
pyncbi download GSE152418 --source rnaseq --annotate -o GSE152418_counts_annotated.csv
# Probe annotation: Series Matrix probes (200800_s_at, cg00000029) -> gene symbol via the GPL table
pyncbi download GSE85506 --source matrix --annotate -o GSE85506_matrix_annotated.csv
pyncbi gpl GPL570 --columns "Gene Symbol,ENTREZ_GENE_ID" -n 20 --markdown

# Cohort-filtered downloads: only the matching samples' own files, resolved in 1 request, parallel
pyncbi download GSE85506 --where "group == 'case'" --pattern "*.idat.gz" --workers 6 -o ./fm/
pyncbi download GSM2267972 GSM2267973 --workers 2 -o ./samples/

# How did the authors process the data? PMC open-access Methods + Data Availability sections
pyncbi pubmed 32788292 --methods --markdown

# One command instead of inspect + samples + files + sra + pubmed:
pyncbi bundle GSE152418 -o ./GSE152418/        # then read ./GSE152418/STUDY_OVERVIEW.md

# Structured ClinVar / dbSNP lookup (clean records, not raw esummary dicts)
pyncbi variant BRCA1 --significance pathogenic -n 10 --markdown
pyncbi variant rs7412 --json
```

**Agent tip:** start a new GEO analysis with `pyncbi bundle <GSE>`; it gives you the cohort breakdown, the file inventory with sizes, the SRA runs, and the paper's Methods in one directory. Then use `pyncbi download --where` for the exact samples you need.

### Step 5: Reads, Genomes, Taxonomy, Records & Citations
```bash
pyncbi fastq GSE152418 --markdown                      # FASTQ URLs/sizes/MD5 per run (GSM alias included)
pyncbi fastq GSE152418 --download --runs SRR12007824 -o ./fastq/
pyncbi genome search "Mus musculus" --json ; pyncbi genome download GCF_000001635.27 --include genome,gff3 -o ./mm39/
pyncbi taxonomy human 10090 --json                     # TaxID, rank, full lineage
pyncbi search biosample "SAMN15180526" --json          # clean attributes (tissue, age, sex...)
pyncbi search geo "glioblastoma single cell" --all -o hits.csv   # every match, paginated
pyncbi gds GDS1234 -o GDS1234.csv                      # curated GEO DataSet + subsets
pyncbi pubmed 32788292 --format bibtex -o refs.bib
```

**Errors:** with `--json`, failures return `{"ok": false, "error_code": "not_found|private|no_data|rate_limited|bot_challenge|timeout|...", "hint": "...", "retryable": true|false}`. Branch on `error_code`; retry only when `retryable` is true. Set `PYNCBI_AGENT_MODE=1` to silence progress output.

### Step 6: Metadata Records (harmonized, provenance-tracked, quality-scored)
```bash
pyncbi metadata GSE152418 --json             # versioned StudyRecord: raw + harmonized sample fields, links, files, quality
pyncbi metadata GSE152418 -o ./GSE152418_record/   # study.json, samples_harmonized.csv, provenance.csv, quality.json, manifest.json
pyncbi samples GSE152418 --harmonize --json  # sex / age (+age_years) / tissue / disease (+is_control) ...; unmapped keys kept as raw:<key>
pyncbi schema vocabulary                     # which raw keys map to which canonical fields
```
**Agent tip:** prefer `ncbi_metadata` / `pyncbi metadata` over `samples` when you need to filter or compare cohorts across studies: canonical columns, units, `is_control`, confidence, and a quality score come with every record. Values with confidence `verbatim`/`unmapped` are NCBI's original strings.

### Step 7: One Call for the Matrix (`pyncbi expression`)
```bash
pyncbi expression GSE152418 -o counts.csv      # matrix + counts_samples.csv, columns are GSM IDs
pyncbi expression GSE85506 --markdown          # shows which source was used and what was skipped
pyncbi expression GSE152418 --where "severity == 'Severe'" --annotate -o severe.csv
```
**Agent tip:** reach for `ncbi_expression` / `pyncbi expression` instead of guessing between `--source rnaseq`, `--source matrix` and supplementary files: it tries NCBI counts, Series Matrix, supplementary tables, per-sample files, on-page tables and `*_RAW.tar` in order, maps columns to GSM IDs, and reports the source plus every candidate it skipped and why.

### Step 8: Choose a Dataset Before Downloading (`pyncbi screen`) and Download Safely
```bash
# Compare candidates in one table (samples, sources, exact size, metadata coverage, quality)
pyncbi screen "covid19 PBMC" --organism "Homo sapiens" --type rnaseq --min-samples 20 --require sex,age --markdown
pyncbi screen GSE152418 GSE85506 --sort-by sample_count --json

# Always look before a big transfer
pyncbi download GSE85506 --source supp --dry-run                # 1 file, 572.6 MB, fits on disk
pyncbi download GSE85506 --source supp --max-download-mb 100    # refuses rather than filling the disk
```
**Agent tip:** `ncbi_screen` with `require: ["sex","age"]` is the cheapest way to pick a dataset: it never downloads data, and `download_human` tells you the cost before you commit. Use `dry_run: true` on `ncbi_download` for anything that might be large, and set `PYNCBI_MAX_DOWNLOAD_MB` plus `PYNCBI_AUDIT_LOG` when running unattended.

### MCP Resources & Prompts (when PyNCBI runs as an MCP server)
Resources give you contracts and whole studies without a tool call:
`pyncbi://schema/study` (record JSON Schema), `pyncbi://vocabulary` (which raw keys map to which canonical
field), `pyncbi://study/{GSE}/overview` (Markdown), `pyncbi://study/{GSE}/samples` (harmonized CSV),
`pyncbi://study/{GSE}/record` (full JSON), `pyncbi://platform/{GPL}/table`.
Prompts encode the recommended workflows: `find_dataset`, `study_deep_dive`, `cohort_download`, `paper_methods`.
Tool results carry `structuredContent` (the JSON envelope) alongside the text, and each tool declares
`readOnlyHint`, so you can tell a lookup from a download before calling it.

### Step 9: BLAST a Sequence
```bash
pyncbi blast "AGCTCGCTGAGACTTCCTGGACCCCGCACC..." --hitlist-size 5 --markdown   # submit, poll and fetch
pyncbi blast query.fasta --program blastx --database nr -o hits.csv
pyncbi blast NM_007294.4 --no-wait        # prints a Request ID (RID) to collect later
pyncbi blast --rid <RID>                  # fetch/poll that job
```
**Agent tip:** BLAST is asynchronous and can take minutes. Call `ncbi_blast` with `wait=false` to get the RID back immediately, then call again with `rid=<RID>`; if a job is still running the tool tells you so instead of blocking. NCBI jobs expire after about 24 hours.

### Sessions & Request Reuse (Python API)
```python
with PyNCBI.Session(api_key=KEY, cache_dir="./agent-1", max_download_mb=500) as s:
    s.build_study_record("GSE152418")  # session client, cache and budget applied automatically
    s.load_expression("GSE152418")
```
Identical metadata requests within the same client are reused for 300 s, reducing repeated requests across inspection, metadata and expression workflows. Set `PYNCBI_HTTP_CACHE_TTL=0` to disable. Polling endpoints and file downloads are never cached.

### Cross-Database Linking, SRA RunInfo & FASTA Sequences (`pyncbi link`, `pyncbi sra`, `pyncbi fasta`, `pyncbi pubmed`, `pyncbi gene`)
```bash
# Find PubMed articles, BioProject, SuperSeries/SubSeries, and SRA links for a GSE
pyncbi link GSE85506 --json

# Find all GEO datasets (GSEs) deposited by a PubMed paper (PMID)
pyncbi link PMID:27713574 --json

# Fetch SRA RunInfo table (SRR runs, SRX, GSM SampleName, layout, instrument, size_MB, download_path)
pyncbi sra GSE152418 --markdown -o GSE152418_sra_runs.csv

# Fetch Nucleotide or Protein FASTA sequence(s) with optional region slicing
pyncbi fasta NM_007294.4 --start 1 --stop 300 -o brca1_region.fasta

# Fetch full PubMed abstract(s)
pyncbi pubmed 27713574 --markdown

# Lookup Gene info by symbol or Entrez ID
pyncbi gene BRCA1 TP53 --organism "Homo sapiens" --markdown
```

---

## Python API Quick Reference

```python
import PyNCBI

# 1. Inspect any GSE, GSM, or GPL (returns InspectionReport)
report = PyNCBI.inspect_accession("GSE85506")
print(report.to_markdown())

# 2. Extract & optionally filter sample metadata & unpacked characteristics in 1 HTTP request
samples, characteristics, summaries = PyNCBI.extract_gse_samples(
    "GSE152418",
    where="severity == 'Severe'",
    save_to="severe_samples.csv",
)

# 3. Preview remote files (.txt.gz, .csv.gz, .tar, Series Matrix, RNA-seq counts) from a bounded prefix without full download
preview = PyNCBI.preview_geo_file("GSE152418", source="rnaseq", max_rows=5)
print(preview.to_markdown())

# 4. Search GEO, PubMed, Gene, SRA RunInfo, and FASTA sequences
geo_hits = PyNCBI.search_geo(
    "alzheimer", organism="Homo sapiens", dataset_type="rnaseq", min_samples=30
)
papers = PyNCBI.search_pubmed("alzheimer microglia single cell", limit=5)
genes = PyNCBI.search_gene("APOE", organism="Homo sapiens")
sra = PyNCBI.fetch_sra_runs("GSE152418", save_to="sra_runs.csv")
fasta_recs = PyNCBI.fetch_fasta("NM_007294.4", db="nuccore", seq_start=1, seq_stop=300)

# 5. Load Series Matrix or NCBI RNA-seq counts as native columns/rows dictionaries
matrix_res = PyNCBI.load_series_matrix("GSE85506")
expression = matrix_res.matrix  # features (ID_REF) x samples (GSM)
metadata = matrix_res.characteristics_table  # samples (GSM) x unpacked characteristics

# 6. Download raw supplementary files as hosted on NCBI
paths = PyNCBI.download_geo_files(
    "GSE85506", output_dir="./data", source="supp", file_index=0, unpack=True
)

# 7. Annotation, cohort downloads, PMC methods, bundles, variants
counts = PyNCBI.load_rnaseq_counts(
    "GSE152418", annotate=True
)  # + Symbol/GeneType/EnsemblGeneID columns
gpl = PyNCBI.fetch_gpl_table("GPL570", columns=["Gene Symbol", "ENTREZ_GENE_ID"])
PyNCBI.download_geo_files("GSE85506", "./fm/", where="group == 'case'", pattern="*.idat.gz")
paper = PyNCBI.fetch_pmc_text("32788292", sections=["methods", "data_availability"])
print(paper.methods)
bundle = PyNCBI.build_study_bundle("GSE152418", "./GSE152418/")  # STUDY_OVERVIEW.md etc.
variants = PyNCBI.search_clinvar("BRCA1", significance="pathogenic")
snp = PyNCBI.fetch_dbsnp("rs7412")[0]

# 8. Object-oriented GSM / GSE classes
gse = PyNCBI.GSE("GSE62003", shell_only=True)
print(gse.characteristics_table["rows"][:5])
gsm = PyNCBI.GSM("GSM1518180")
print(gsm.data["rows"][:5] if gsm.data else [])
```

## Output and limits

Use `pyncbi schema mcp` for the current tool manifest. JSON table exports retain feature IDs
and float precision; missing/non-finite values are null. Previews can be truncated: save full
results with `-o`. Quality scores describe metadata completeness, not scientific validity.
`PYNCBI_MAX_DOWNLOAD_MB` is an operation budget shared across nested requests and workers,
including metadata fetched inside an operation. It counts delivered HTTP body bytes before
archive expansion, not lifetime session traffic. Zero disables it. Inspect the download plan
first and check free space for extraction. HTTP MCP requires initialization and session IDs;
send one JSON-RPC message per POST.

## Native runtime contract

PyNCBI has no third-party runtime dependencies. Table results are ordinary dictionaries
with `columns` and `rows`; metadata records use native dictionaries/lists. The first
measurement column is `feature_id`. Callers choose their own DataFrame/analysis library
or YAML/Parquet converter. Built-in exports are JSON, CSV and TSV. Use
`iter_supplementary_records(url)` for large files and close its generator when stopping
early. Use `iter_records(table)` for dictionaries from an already loaded table.
CLI/MCP native-data envelopes use schema version 2.0.
