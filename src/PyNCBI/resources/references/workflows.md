# PyNCBI workflow examples

Use only the sections needed for the task. These examples describe the 1.0 API;
consult the installed CLI help or MCP schema when working with another version.

## GEO metadata and cohort downloads

For an accession, call `ncbi_inspect` and `ncbi_samples` before constructing a filter.
For candidate discovery, call `ncbi_search` with `db="geo"` and a topic/organism;
`ncbi_screen` compares studies without fetching their matrices.

```bash
pyncbi search geo "fibromyalgia methylation" --organism "Homo sapiens" --json
pyncbi inspect GSE85506 --json
pyncbi samples GSE85506 --json
pyncbi download GSE85506 --where "group == 'case'" --pattern "*.idat.gz" --dry-run --json
pyncbi download GSE85506 --where "group == 'case'" --pattern "*.idat.gz" --max-download-mb 500 -o ./fm_idats/ --json
```

The `group` field belongs to this example. Other studies expose different fields.
`ncbi_samples` with `as_json=true` supports `page_size` and `page_token`. Iterate
pages until `next_page_token` is null; use `save_to` for the complete sample table.
Missing, private or empty data is a reported condition, not evidence of an absent phenotype.

## Expression matrices

`ncbi_expression` resolves NCBI-generated RNA-seq counts, Series Matrix and supported
tabular supplementary/sample sources. Pass `output_path` to retain the full matrix
and aligned sample sheet; the MCP response contains a bounded preview and source history.

```bash
pyncbi expression GSE152418 --where "severity == 'Severe'" -o severe_counts.csv --json
```

```python
import PyNCBI

with PyNCBI.Session(cache_dir="./ncbi-cache", max_download_mb=500) as session:
    result = session.load_expression("GSE152418", save_to="counts.csv")
    print(result.source, result.shape)
    print(result.matrix["columns"])
```

For incremental processing of a downloaded table:

```python
from contextlib import closing
from PyNCBI import iter_supplementary_records

with closing(iter_supplementary_records("counts.tsv.gz")) as records:
    for record in records:
        print(record)
        break
```

Use `annotate=true` only when the user needs annotation. Keep original feature IDs and
source values. A Series Matrix may contain metadata without measurements; the resolver
records skipped candidates and tries another supported source.

## Literature and metadata

Use `ncbi_pubmed` for PMIDs or a query. `methods=true` retrieves available PMC Methods;
`sections` selects section kinds or title substrings. Closed-access or missing full text
must be reported. Do not infer processing steps from an abstract alone.

```bash
pyncbi pubmed 32433946 --methods --json
pyncbi metadata GSE152418 -o ./study_record/ --json
```

`ncbi_metadata` preserves raw characteristics, normalized values, provenance, confidence
and metadata quality issues. Use `ncbi_bundle` for a full study overview with sample
metadata, file inventory and linked papers; it performs more work than an inspection.

## Sequences, reads and other databases

- `ncbi_sra` returns RunInfo; `ncbi_fastq` lists ENA FASTQ links unless `download=true`.
  Review the manifest before downloading reads.
- `ncbi_fasta` retrieves nucleotide/protein sequences; plural accessions accept a string
  or an array of strings. Use `save_to` for complete sequences.
- `ncbi_gene`, `ncbi_taxonomy`, `ncbi_variant` and `ncbi_genome` expose Gene, taxonomy,
  ClinVar/dbSNP and assembly operations. Read their tool schemas for supported arguments.
- `ncbi_link` traverses supported Entrez database links. An empty link result does not
  prove that no biological relationship exists.
- `ncbi_blast` supports asynchronous submission with `wait=false`; retain the returned
  RID, then poll with `rid`. A timeout can leave a submitted NCBI job running.

## Recover from errors

`isError` and envelope `ok=false` indicate failure. Read `error_code` and `hint`:

- `invalid_argument`: correct the field name, type, accession or filter before retrying.
- `not_found`, `private`, `no_data`: report the limitation and inspect alternatives when
  they fit the user's task. Do not repeatedly retry the same unavailable record.
- `rate_limited` or transient network errors: honor `retryable` and any retry delay.
  The shared client already retries eligible transport failures; avoid an unbounded outer loop.
- `bot_challenge`: report NCBI's challenge and use a supported alternative source when
  available. Do not treat the challenge HTML as scientific data.
- Budget errors: narrow file selection or obtain an explicitly larger budget; do not
  silently remove a limit.

If a client disconnects or times out, check saved/partial files before repeating a large
operation. The server currently runs calls synchronously and does not promise task cancellation.
