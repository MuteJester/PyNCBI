"""The MCP tool catalogue: input schemas, behaviour annotations, and the output envelope."""

from __future__ import annotations

from typing import Any

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "ncbi_search",
        "description": (
            "Search NCBI databases ('geo', 'pubmed', 'gene', 'bioproject', 'sra', 'biosample', 'clinvar'). "
            "For 'geo', automatically resolves GDS numeric UIDs into GSE series accessions and supports "
            "filtering by organism, dataset_type ('rnaseq', 'methylation', 'microarray'), and min_samples."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "db": {
                    "type": "string",
                    "description": "Database to search: 'geo', 'pubmed', 'gene', 'sra', 'bioproject', 'biosample', 'clinvar'",
                    "default": "geo",
                },
                "query": {
                    "type": "string",
                    "description": "Search query keywords or Entrez query string",
                },
                "organism": {
                    "type": "string",
                    "description": "Optional organism filter (e.g., 'Homo sapiens', 'Mus musculus')",
                },
                "dataset_type": {
                    "type": "string",
                    "description": "For GEO: 'rnaseq', 'methylation', 'microarray', or full NCBI DataSet Type",
                },
                "min_samples": {
                    "type": "integer",
                    "description": "For GEO: minimum number of samples in series",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 15)",
                    "default": 15,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "ncbi_inspect",
        "description": (
            "Fast preflight inspection of any NCBI GEO accession (GSE, GSM, GPL, or GDS). "
            "Returns study title, summary, organism, platform, sample count, summarized cohort "
            "characteristics breakdown (categorical counts & numeric ranges), and all downloadable "
            "supplementary/matrix/RNA-seq files with their exact byte sizes via HTTP HEAD."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "GEO accession ID (e.g., 'GSE85506', 'GSM1518180', 'GPL13534')",
                },
                "check_file_sizes": {
                    "type": "boolean",
                    "description": "Whether to query remote file byte sizes via HTTP HEAD (default true)",
                    "default": True,
                },
            },
            "required": ["accession"],
        },
    },
    {
        "name": "ncbi_samples",
        "description": (
            "Fetch and organize all sample metadata and unpacked '!Sample_characteristics_ch1' "
            "key:value columns for a GSE series in a single O(1) batch HTTP request. "
            "Optionally saves the full sample table to a local CSV, TSV, Parquet, or JSON file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "gse_id": {
                    "type": "string",
                    "description": "GSE series accession (e.g., 'GSE62003', 'GSE85506')",
                },
                "where": {
                    "type": "string",
                    "description": 'Optional cohort filter expression (e.g., "disease == \'COVID-19\'", "age > 50", or "PBMC")',
                },
                "save_to": {
                    "type": "string",
                    "description": "Optional file path to save the full sample table (.csv, .tsv, .parquet, .json)",
                },
                "characteristics_only": {
                    "type": "boolean",
                    "description": "If true, return only the unpacked key:value characteristics columns",
                    "default": False,
                },
                "max_preview_rows": {
                    "type": "integer",
                    "description": "Number of preview rows to include in the tool output (default 15)",
                    "default": 15,
                },
                "harmonize": {
                    "type": "boolean",
                    "description": "Map characteristics to the controlled vocabulary (sex, age, tissue, disease, ...) keeping raw values as raw:<key>",
                    "default": False,
                },
                "as_json": {
                    "type": "boolean",
                    "description": "Return a JSON envelope with paginated rows instead of Markdown",
                    "default": False,
                },
                "page_size": {
                    "type": "integer",
                    "description": "Rows per page when as_json=true (default 50)",
                    "default": 50,
                },
                "page_token": {
                    "type": "string",
                    "description": "Opaque token from a previous page (as_json=true)",
                },
            },
            "required": ["gse_id"],
        },
    },
    {
        "name": "ncbi_preview",
        "description": (
            "Stream and preview the first few rows, column names, gene/probe identifier format, "
            "or .tar archive members of a remote NCBI GEO file in <0.5s using HTTP Range/streaming "
            "(without downloading the full file)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "GSE, GSM, or GPL accession ID",
                },
                "source": {
                    "type": "string",
                    "description": "'auto', 'supp' (supplementary), 'matrix' (Series Matrix), 'rnaseq' (NCBI RNA-seq counts), or 'table'",
                    "default": "auto",
                },
                "file_index": {
                    "type": "integer",
                    "description": "0-based supplementary file index to preview",
                },
                "pattern": {
                    "type": "string",
                    "description": "Optional filename glob filter for supplementary files",
                },
                "max_rows": {
                    "type": "integer",
                    "description": "Number of rows to preview (default 8)",
                    "default": 8,
                },
            },
            "required": ["accession"],
        },
    },
    {
        "name": "ncbi_download",
        "description": (
            "Download raw supplementary files, Series Matrix tables, NCBI RNA-seq count tables, "
            "or GSM on-page data tables from NCBI to a local folder or file path without altering "
            "scientific values. Always run `ncbi_inspect` first to check file sizes and indices."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "GSE, GSM, or GPL accession ID",
                },
                "output_path": {
                    "type": "string",
                    "description": "Destination directory or file path (.csv, .tsv, .parquet)",
                },
                "source": {
                    "type": "string",
                    "description": "'auto', 'supp' (supplementary), 'matrix' (Series Matrix), 'rnaseq' (NCBI RNA-seq counts), or 'table' (on-page table)",
                    "default": "auto",
                },
                "file_index": {
                    "type": "integer",
                    "description": "0-based supplementary file index to download",
                },
                "pattern": {
                    "type": "string",
                    "description": "Optional filename glob filter for supplementary files (e.g., '*.txt.gz')",
                },
                "unpack": {
                    "type": "boolean",
                    "description": "If true, safely extract .tar archives and gunzip .gz files after downloading",
                    "default": False,
                },
                "where": {
                    "type": "string",
                    "description": "For a GSE: cohort filter; downloads only matching samples' own files",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Report which files would be fetched, with sizes and total, WITHOUT downloading",
                    "default": False,
                },
                "max_download_mb": {
                    "type": "number",
                    "description": "Refuse the download above this many MB (default 2048)",
                },
            },
            "required": ["accession"],
        },
    },
    {
        "name": "ncbi_pubmed",
        "description": (
            "Fetch full PubMed article metadata and abstracts for one or more PMIDs "
            "(comma-separated) or a search query. Set `methods=true` (or `sections`) to also "
            "retrieve the PMC open-access full-text Methods / Data Availability sections, so the "
            "exact preprocessing, genome build, normalization, and cohort definitions can be read."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pmids_or_query": {
                    "type": "string",
                    "description": "One or more PMIDs (e.g. '27713574,31234567') or a PubMed search query",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max articles if searching by query (default 5)",
                    "default": 5,
                },
                "methods": {
                    "type": "boolean",
                    "description": "Also fetch PMC full-text 'methods' and 'data_availability' sections",
                    "default": False,
                },
                "fulltext": {
                    "type": "boolean",
                    "description": "Also fetch the complete PMC open-access full text (all sections)",
                    "default": False,
                },
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "PMC section kinds or title substrings to fetch (e.g., ['methods','results','data_availability'])",
                },
            },
            "required": ["pmids_or_query"],
        },
    },
    {
        "name": "ncbi_gene",
        "description": (
            "Lookup structured NCBI Gene information (official symbol, Entrez Gene ID, "
            "description, aliases, chromosome location, and functional summary)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gene symbol (e.g., 'BRCA1'), Entrez Gene ID (e.g., '672'), or search term",
                },
                "organism": {
                    "type": "string",
                    "description": "Organism filter (default 'Homo sapiens')",
                    "default": "Homo sapiens",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max gene records to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "ncbi_link",
        "description": (
            "Find cross-database links for an NCBI accession: given a GSE/GSM, returns linked "
            "PubMed articles, BioProject, and SRA URLs; given a PMID, finds all GEO datasets "
            "(GSE series) associated with that publication."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "GSE accession (e.g., 'GSE85506') or PubMed ID (e.g., 'PMID:27713574')",
                },
            },
            "required": ["accession"],
        },
    },
    {
        "name": "ncbi_sra",
        "description": (
            "Fetch the NCBI SRA RunInfo table (SRR run IDs, SRX experiment IDs, GSM SampleName, "
            "LibraryStrategy, LibraryLayout PAIRED/SINGLE, instrument Model, spots, size_MB, and "
            "direct download_path) for any GSE, GSM, PRJNA, SRP, SRX, or SRR accession."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "accession_or_query": {
                    "type": "string",
                    "description": "GSE, GSM, PRJNA, SRP, SRX, SRR accession, or SRA query",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max SRA experiments to fetch (default 100)",
                    "default": 100,
                },
                "save_to": {
                    "type": "string",
                    "description": "Optional file path (.csv, .tsv, .parquet) to save the full SRA RunInfo table",
                },
            },
            "required": ["accession_or_query"],
        },
    },
    {
        "name": "ncbi_fasta",
        "description": (
            "Fetch Nucleotide (`db='nuccore'`) or Protein (`db='protein'`) FASTA sequence(s) "
            "from NCBI Entrez by accession (e.g., 'NM_007294.4', 'NP_009225.1'), with optional "
            "1-based region slicing (`seq_start`, `seq_stop`) and file saving."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "accessions": {
                    "type": "string",
                    "description": "One or more comma-separated Nucleotide or Protein accessions",
                },
                "db": {
                    "type": "string",
                    "description": "'nuccore' (default) or 'protein'",
                    "default": "nuccore",
                },
                "seq_start": {
                    "type": "integer",
                    "description": "Optional 1-based start coordinate",
                },
                "seq_stop": {
                    "type": "integer",
                    "description": "Optional 1-based stop coordinate",
                },
                "save_to": {
                    "type": "string",
                    "description": "Optional file path (.fasta, .fa) to save the FASTA sequence(s)",
                },
            },
            "required": ["accessions"],
        },
    },
    {
        "name": "ncbi_bundle",
        "description": (
            "Build a complete, self-contained local study directory for a GEO Series in one call: "
            "STUDY_OVERVIEW.md (design, cohort breakdown, file inventory with sizes, linked paper abstract + "
            "PMC Methods), samples_metadata.csv, files_manifest.json, sra_runs.csv (if sequencing runs exist), "
            "pubmed_articles.json, paper_methods.md, and optionally the primary data table. Replaces running "
            "inspect/samples/files/sra/pubmed separately."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "gse_id": {"type": "string", "description": "GSE accession (e.g., 'GSE152418')"},
                "output_dir": {
                    "type": "string",
                    "description": "Destination directory (default ./<GSE>_bundle)",
                },
                "where": {
                    "type": "string",
                    "description": "Optional cohort filter applied to samples_metadata.csv",
                },
                "include_matrix": {
                    "type": "boolean",
                    "description": "Also download the primary data table (annotated RNA-seq counts or Series Matrix)",
                    "default": False,
                },
                "include_sra": {
                    "type": "boolean",
                    "description": "Fetch the SRA RunInfo table (default true)",
                    "default": True,
                },
                "include_methods": {
                    "type": "boolean",
                    "description": "Fetch PMC open-access Methods sections for linked papers (default true)",
                    "default": True,
                },
                "expand_subseries": {
                    "type": "boolean",
                    "description": "For a SuperSeries, also bundle each SubSeries under subseries/<GSE>/",
                    "default": False,
                },
            },
            "required": ["gse_id"],
        },
    },
    {
        "name": "ncbi_variant",
        "description": (
            "Structured ClinVar and dbSNP variant lookup. Accepts a gene symbol ('BRCA1'), rsID "
            "('rs7412'), HGVS ('NM_007294.4:c.5266dupC'), VCV accession, or free text and returns clean "
            "records: accession, title, gene, protein change, clinical significance, review status/stars, "
            "conditions, GRCh38/GRCh37 location, rsID (ClinVar) plus position/alleles/genes/frequencies (dbSNP)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gene symbol, rsID, HGVS expression, VCV accession, or text",
                },
                "source": {
                    "type": "string",
                    "description": "'auto' (default), 'clinvar', or 'dbsnp'",
                    "default": "auto",
                },
                "significance": {
                    "type": "string",
                    "description": "ClinVar significance filter, e.g. 'pathogenic', 'likely pathogenic', 'benign'",
                },
                "gene": {
                    "type": "string",
                    "description": "Restrict ClinVar results to this gene symbol",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max ClinVar variants (default 20)",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "ncbi_metadata",
        "description": (
            "Build the versioned PyNCBI study record for a GEO series: series facts, every sample with raw "
            "characteristics AND harmonized values (sex, age with units, tissue, disease with is_control, ...) each "
            "carrying provenance and confidence, cross-database links (BioSample, SRA, BioProject, PubMed), file "
            "inventory, harmonization summary and a 0-100 quality score with typed issues. Samples are paginated "
            "(page_size/page_token) and payloads are bounded for context windows. Schema: `pyncbi schema study`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "gse_id": {"type": "string", "description": "GSE accession"},
                "where": {
                    "type": "string",
                    "description": "Optional cohort filter applied to the samples",
                },
                "enrich": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional enrichments: 'biosample', 'sra', 'pubmed'",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Samples per page (default 50)",
                    "default": 50,
                },
                "page_token": {
                    "type": "string",
                    "description": "Opaque token from a previous page",
                },
                "save_to": {
                    "type": "string",
                    "description": "Directory to write the study layout (study.json, samples_harmonized.csv, provenance.csv, quality.json, manifest.json)",
                },
            },
            "required": ["gse_id"],
        },
    },
    {
        "name": "ncbi_screen",
        "description": (
            "Screen MANY GEO series at once into one comparison table, so a dataset can be chosen without "
            "downloading anything: sample count, which data sources exist (NCBI counts / Series Matrix / "
            "per-sample files), exact total download size, which harmonized metadata fields are present and how "
            "well they are populated (sex/age/disease/tissue coverage), and a 0-100 quality score. Accepts a GEO "
            "query or explicit accessions, and `require` keeps only studies carrying the fields you need."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "GEO search query (use this or `accessions`)",
                },
                "accessions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit GSE accessions to screen",
                },
                "require": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keep only studies harmonizing all of these fields, e.g. ['sex','age']",
                },
                "organism": {"type": "string", "description": "Search filter (query mode)"},
                "dataset_type": {
                    "type": "string",
                    "description": "Search filter: 'rnaseq', 'methylation', 'microarray', ...",
                },
                "min_samples": {"type": "integer", "description": "Search filter: minimum samples"},
                "limit": {
                    "type": "integer",
                    "description": "Max studies to screen when searching (default 20)",
                    "default": 20,
                },
                "sort_by": {
                    "type": "string",
                    "description": "Sort descending by a column, e.g. 'sample_count', 'quality_score'",
                },
                "save_to": {
                    "type": "string",
                    "description": "Optional path to save the screening table",
                },
            },
        },
    },
    {
        "name": "ncbi_expression",
        "description": (
            "Get the analysis-ready expression matrix for a GEO series in ONE call. Tries every place GEO keeps "
            "measurements in priority order (NCBI-generated counts, Series Matrix, tabular supplementary file, "
            "per-sample supplementary tables, GSM on-page tables, per-sample tables inside *_RAW.tar), maps the "
            "columns back to GSM IDs (by accession or sample title), returns the aligned sample sheet, and reports "
            "which source was used and which candidates were skipped and why. Measurements are never transformed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "gse_id": {"type": "string", "description": "GSE accession"},
                "output_path": {
                    "type": "string",
                    "description": "Save the matrix here (.csv/.tsv/.parquet); the sample sheet is written as <stem>_samples.csv",
                },
                "prefer": {
                    "type": "string",
                    "description": "'auto' (default) or force: 'rnaseq', 'matrix', 'supp', 'per_sample', 'table', 'tar'",
                    "default": "auto",
                },
                "count_type": {
                    "type": "string",
                    "description": "For NCBI counts: 'raw_counts' (default), 'norm_counts_TPM', 'norm_counts_FPKM'",
                    "default": "raw_counts",
                },
                "annotate": {
                    "type": "boolean",
                    "description": "Prepend gene/probe annotation columns",
                    "default": False,
                },
                "where": {
                    "type": "string",
                    "description": "Cohort filter restricting the sample sheet and matrix columns",
                },
                "max_download_mb": {
                    "type": "number",
                    "description": "Refuse candidate files larger than this (default 2048)",
                    "default": 2048,
                },
                "max_rows": {
                    "type": "integer",
                    "description": "Rows of matrix preview to include (default 8)",
                    "default": 8,
                },
            },
            "required": ["gse_id"],
        },
    },
    {
        "name": "ncbi_fastq",
        "description": (
            "Resolve any GEO/SRA accession (GSE, GSM, PRJNA, SRP, SRX, SRS, SAMN, SRR) to its FASTQ files "
            "with direct HTTPS URLs, byte sizes, MD5 checksums, layout, and GSM sample aliases (via the ENA "
            "mirror of SRA), and optionally download them in parallel with MD5 verification — no SRA Toolkit needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_download_mb": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Operation transfer budget in MB (default PYNCBI_MAX_DOWNLOAD_MB, 2048); 0 disables.",
                },
                "no_disk_check": {
                    "type": "boolean",
                    "default": False,
                    "description": "Skip free-space checks when downloading.",
                },
                "accession": {
                    "type": "string",
                    "description": "GSE, GSM, PRJNA, SRP, SRX, SRS, SAMN, or SRR accession",
                },
                "download": {
                    "type": "boolean",
                    "description": "Download the files into output_dir (default false: list only)",
                    "default": False,
                },
                "output_dir": {
                    "type": "string",
                    "description": "Destination folder when downloading",
                },
                "runs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only these SRR run accessions when downloading",
                },
                "max_workers": {
                    "type": "integer",
                    "description": "Parallel downloads (default 4)",
                    "default": 4,
                },
                "save_to": {
                    "type": "string",
                    "description": "Optional path to save the FASTQ manifest (.csv/.tsv/.json)",
                },
            },
            "required": ["accession"],
        },
    },
    {
        "name": "ncbi_genome",
        "description": (
            "Search reference genome assemblies for an organism/TaxID (or fetch one GCF_/GCA_ accession) and "
            "optionally download the genome package (FASTA, GFF3, GTF, proteins) via NCBI Datasets, unpacked safely."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_download_mb": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Operation transfer budget in MB (default PYNCBI_MAX_DOWNLOAD_MB, 2048); 0 disables.",
                },
                "no_disk_check": {
                    "type": "boolean",
                    "default": False,
                    "description": "Skip free-space checks when downloading.",
                },
                "target": {
                    "type": "string",
                    "description": "Organism name, TaxID, or GCF_/GCA_ accession",
                },
                "download": {
                    "type": "boolean",
                    "description": "Download the package for a GCF_/GCA_ accession",
                    "default": False,
                },
                "include": {
                    "type": "string",
                    "description": "Comma-separated: genome,gff3,gtf,gbff,rna,protein,cds (default 'genome')",
                    "default": "genome",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Destination folder when downloading",
                },
                "reference_only": {
                    "type": "boolean",
                    "description": "Only reference assemblies when searching (default true)",
                    "default": True,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max assemblies when searching (default 10)",
                    "default": 10,
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "ncbi_taxonomy",
        "description": "Lookup NCBI Taxonomy records (TaxID, scientific/common names, rank, genetic code, full lineage) by organism name or TaxID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Organism name (e.g., 'human', 'Mus musculus') or TaxID (9606)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max matches for name queries (default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "ncbi_blast",
        "description": (
            "Run a BLAST sequence similarity search against NCBI. BLAST is asynchronous: with wait=false "
            "(or when a search is slow) you get a Request ID (RID) back and call again with rid=<RID> to "
            "collect results, so the tool never blocks for long. Returns clean hits (accession, description, "
            "bit score, E-value, percent identity, coverage) instead of raw BLAST XML. Accepts a sequence, "
            "FASTA text or an accession."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Nucleotide/protein sequence, FASTA text, or an accession",
                },
                "rid": {
                    "type": "string",
                    "description": "Poll/fetch this existing BLAST Request ID instead of submitting",
                },
                "program": {
                    "type": "string",
                    "description": "'blastn' (default), 'blastp', 'blastx', 'tblastn', 'tblastx'",
                    "default": "blastn",
                },
                "database": {
                    "type": "string",
                    "description": "Target database (default nt for nucleotide, nr for protein)",
                },
                "evalue": {"type": "number", "description": "Expect-value threshold"},
                "hitlist_size": {
                    "type": "integer",
                    "description": "Max hits to keep (default 50)",
                    "default": 50,
                },
                "entrez_query": {
                    "type": "string",
                    "description": 'Entrez filter, e.g. "Homo sapiens"[organism]',
                },
                "wait": {
                    "type": "boolean",
                    "description": "Poll until results are ready (default true, bounded by timeout_seconds)",
                    "default": True,
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": "Stop waiting after this long and return the RID (default 120)",
                    "default": 120,
                },
            },
        },
    },
    {
        "name": "ncbi_status",
        "description": (
            "Check PyNCBI backend health, NCBI E-Utilities / FTP connectivity and latency, "
            "NCBI_API_KEY configuration (3 vs 10 req/s), realm rate-limiter states, cache size, "
            "and session request telemetry."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "probe_network": {
                    "type": "boolean",
                    "description": "Whether to probe live NCBI endpoints for latency and API key validation (default true)",
                    "default": True,
                },
            },
        },
    },
]


# name -> (human title, read-only?)
_TOOL_BEHAVIOUR: dict[str, tuple[str, bool]] = {
    "ncbi_search": ("Search NCBI", True),
    "ncbi_screen": ("Screen studies", True),
    "ncbi_inspect": ("Inspect accession", True),
    "ncbi_metadata": ("Build study record", True),
    "ncbi_samples": ("Sample metadata table", True),
    "ncbi_preview": ("Preview remote file", True),
    "ncbi_expression": ("Load expression matrix", False),
    "ncbi_download": ("Download GEO files", False),
    "ncbi_bundle": ("Build study bundle", False),
    "ncbi_pubmed": ("PubMed & PMC full text", True),
    "ncbi_gene": ("Gene lookup", True),
    "ncbi_link": ("Cross-database links", True),
    "ncbi_sra": ("SRA RunInfo", True),
    "ncbi_fastq": ("FASTQ files", False),
    "ncbi_fasta": ("FASTA sequences", True),
    "ncbi_variant": ("ClinVar & dbSNP", True),
    "ncbi_genome": ("Genome assemblies", False),
    "ncbi_taxonomy": ("Taxonomy lookup", True),
    "ncbi_blast": ("BLAST sequence search", True),
    "ncbi_status": ("Backend status", True),
}


# Every tool answers with the PyNCBI agent envelope, so hosts can validate structuredContent.
ENVELOPE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "The PyNCBI agent envelope: a stable wrapper around every tool's payload.",
    "properties": {
        "ok": {"type": "boolean", "description": "False when the call failed"},
        "schema_version": {"type": "string", "description": "Envelope schema version"},
        "pyncbi_version": {"type": "string", "description": "PyNCBI version that produced this"},
        "command": {"type": "string", "description": "The tool that produced this envelope"},
        "target": {"type": "string", "description": "Accession, query or identifier acted on"},
        "summary": {"type": "string", "description": "One-line human summary of the result"},
        "data": {"description": "The tool's payload (shape depends on the tool)"},
        "saved_files": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Files written",
        },
        "next_steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Suggested follow-up calls",
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Non-fatal problems",
        },
        "elapsed_ms": {"type": "number", "description": "Wall time spent on this call"},
        "request_count": {"type": "integer", "description": "NCBI requests this call issued"},
    },
    "required": ["ok", "command", "summary", "data"],
}


def _annotate_tools() -> None:
    """Attach MCP behaviour annotations so hosts can reason about each tool."""
    for tool in MCP_TOOLS:
        title, read_only = _TOOL_BEHAVIOUR.get(tool["name"], (tool["name"], True))
        annotations: dict[str, Any] = {
            "title": title,
            "readOnlyHint": read_only,
            "openWorldHint": True,  # every tool queries NCBI
        }
        if not read_only:
            # PyNCBI only ever creates files; it never deletes or overwrites unrelated data
            annotations["destructiveHint"] = False
            annotations["idempotentHint"] = True
        tool["annotations"] = annotations
        tool["outputSchema"] = ENVELOPE_OUTPUT_SCHEMA


_annotate_tools()
