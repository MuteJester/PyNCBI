"""The full `pyncbi` argument parser: every command, flag, and help string."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyNCBI.cli.console import _add_output_flags, _Parser, _version_string

if TYPE_CHECKING:
    import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="pyncbi",
        description="PyNCBI — Programmatic & Agent-Ready CLI and MCP Server for NCBI (GEO, Entrez, PubMed, Gene, SRA)",
        epilog="Set PYNCBI_AGENT_MODE=1 for machine-oriented output (no progress bars/colors); NO_COLOR is honoured.",
    )
    parser.add_argument("--version", action="version", version=_version_string())
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors in all output")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Enable step-by-step verbose progress reporting on stderr (-vv for full HTTP debug trace)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress all progress bars and status messages on stderr",
    )
    sub = parser.add_subparsers(dest="subcommand")

    # 1. search
    p_search = sub.add_parser(
        "search", help="Search NCBI databases (geo, pubmed, gene, sra, bioproject)"
    )
    p_search.add_argument(
        "db", help="Database: 'geo', 'pubmed', 'gene', 'sra', 'bioproject', 'biosample', 'clinvar'"
    )
    p_search.add_argument("query", nargs="+", help="Search keywords or Entrez query")
    p_search.add_argument(
        "--organism", help="Filter by organism (e.g., 'Homo sapiens', 'Mus musculus')"
    )
    p_search.add_argument(
        "--type", help="For GEO: dataset type ('rnaseq', 'methylation', 'microarray')"
    )
    p_search.add_argument(
        "--entry-type", default="gse", help="For GEO: 'gse' (default), 'gsm', 'gpl', or 'all'"
    )
    p_search.add_argument("--min-samples", type=int, help="For GEO: minimum number of samples")
    p_search.add_argument(
        "-n", "--limit", type=int, default=15, help="Max results to return (default 15)"
    )
    p_search.add_argument(
        "--all",
        action="store_true",
        help="Return every match (paginates through NCBI; use with -o for large result sets)",
    )
    _add_output_flags(p_search)

    # 2. inspect
    p_inspect = sub.add_parser(
        "inspect",
        help="Inspect any GSE, GSM, or GPL record (metadata, sample breakdown & file sizes)",
    )
    p_inspect.add_argument(
        "accession", help="GEO accession (e.g., GSE85506, GSM1518180, GPL13534, GDS1234)"
    )
    p_inspect.add_argument(
        "--no-sizes", action="store_true", help="Skip HTTP HEAD file size checks"
    )
    _add_output_flags(p_inspect)

    # 3. samples
    p_samples = sub.add_parser(
        "samples",
        help="Fetch & organize all sample metadata and characteristics for a GSE (1 batch request)",
    )
    p_samples.add_argument("gse_id", help="GSE accession (e.g., GSE62003, GSE85506)")
    p_samples.add_argument(
        "-w",
        "--where",
        help='Filter sample cohort (e.g., "disease == \'Control\'", "age > 50", or "PBMC")',
    )
    p_samples.add_argument(
        "--characteristics-only",
        action="store_true",
        help="Only return unpacked key:value characteristics columns",
    )
    p_samples.add_argument(
        "--harmonize",
        action="store_true",
        help="Map characteristics to the controlled vocabulary (sex, age, tissue, disease, ...) keeping raw values as raw:<key>",
    )
    p_samples.add_argument(
        "--summary",
        action="store_true",
        help="Only display the cohort characteristics breakdown summary",
    )
    p_samples.add_argument(
        "--max-rows", type=int, default=15, help="Max preview rows in terminal (default 15)"
    )
    _add_output_flags(p_samples)

    # 4. preview
    p_prev = sub.add_parser(
        "preview",
        help="Stream & preview remote GEO file columns, row IDs, or archive members from a bounded prefix without full download",
    )
    p_prev.add_argument("accession", help="GSE, GSM, or GPL accession")
    p_prev.add_argument(
        "--source",
        default="auto",
        choices=["auto", "supp", "matrix", "rnaseq", "table"],
        help="File source to preview (default: auto)",
    )
    p_prev.add_argument(
        "--file-index", type=int, help="0-based supplementary file index to preview"
    )
    p_prev.add_argument("--pattern", help="Filename glob filter for supplementary files")
    p_prev.add_argument(
        "-n", "--rows", type=int, default=8, help="Number of preview rows (default 8)"
    )
    _add_output_flags(p_prev)

    # 5. files
    p_files = sub.add_parser(
        "files", help="List all downloadable files and byte sizes for a GSE, GSM, or GPL"
    )
    p_files.add_argument("accession", help="GEO accession (e.g., GSE85506, GSM1518180)")
    p_files.add_argument("--no-sizes", action="store_true", help="Skip HTTP HEAD file size checks")
    _add_output_flags(p_files)

    # 6. download
    p_dl = sub.add_parser(
        "download",
        help="Download raw files or data tables from NCBI GEO (one or many accessions, optional cohort filter)",
    )
    p_dl.add_argument(
        "accessions",
        nargs="+",
        metavar="ACCESSION",
        help="One or more GSE, GSM, or GPL accessions (multiple run in parallel)",
    )
    p_dl.add_argument(
        "-w",
        "--where",
        help="For a GSE: cohort filter; downloads only matching samples' own files (e.g., \"severity == 'Severe'\")",
    )
    p_dl.add_argument(
        "--workers", type=int, default=4, help="Parallel download workers (default 4)"
    )
    p_dl.add_argument(
        "--annotate",
        action="store_true",
        help="For --source rnaseq/matrix: prepend gene symbol annotation columns to the exported table",
    )
    p_dl.add_argument(
        "--dry-run",
        action="store_true",
        help="Report exactly which files would be fetched, their sizes and the total, without downloading",
    )
    p_dl.add_argument(
        "--max-download-mb",
        type=float,
        default=None,
        help="Refuse the download above this many MB (default: PYNCBI_MAX_DOWNLOAD_MB or 2048)",
    )
    p_dl.add_argument(
        "--no-disk-check",
        action="store_true",
        help="Skip the free-disk-space check before downloading",
    )
    p_dl.add_argument(
        "--subseries",
        action="store_true",
        help="For a SuperSeries: download every SubSeries into its own sub-folder",
    )
    p_dl.add_argument(
        "--source",
        default="auto",
        choices=["auto", "supp", "matrix", "rnaseq", "table"],
        help="File source to download (default: auto)",
    )
    p_dl.add_argument("--file-index", type=int, help="0-based supplementary file index to download")
    p_dl.add_argument(
        "--pattern", help="Filename glob filter for supplementary files (e.g., '*.txt.gz')"
    )
    p_dl.add_argument(
        "--unpack", action="store_true", help="Safely extract .tar archives and gunzip .gz files"
    )
    _add_output_flags(p_dl)

    # 7. pubmed
    p_pm = sub.add_parser(
        "pubmed",
        help="Fetch PubMed article metadata, abstracts, and (optionally) PMC full-text Methods sections by PMID",
    )
    p_pm.add_argument("pmids", nargs="+", help="One or more PubMed IDs (e.g., 27713574)")
    p_pm.add_argument(
        "--methods",
        action="store_true",
        help="Also fetch the PMC open-access Methods and Data Availability sections",
    )
    p_pm.add_argument(
        "--fulltext",
        action="store_true",
        help="Also fetch the complete PMC open-access full text (all sections)",
    )
    p_pm.add_argument(
        "--sections",
        help="Comma-separated PMC section kinds or title substrings to fetch (e.g., 'methods,results,data_availability')",
    )
    p_pm.add_argument(
        "--format",
        choices=["bibtex", "ris"],
        help="Export citations as BibTeX or RIS (prints to stdout; with -o refs.bib/.ris writes the file)",
    )
    _add_output_flags(p_pm)

    # 8. gene
    p_gene = sub.add_parser("gene", help="Lookup NCBI Gene records by symbol or Entrez Gene ID")
    p_gene.add_argument(
        "queries", nargs="+", help="Gene symbol(s) or Entrez Gene ID(s) (e.g., BRCA1 TP53)"
    )
    p_gene.add_argument(
        "--organism", default="Homo sapiens", help="Organism filter (default: 'Homo sapiens')"
    )
    p_gene.add_argument(
        "-n", "--limit", type=int, default=5, help="Max matches per query (default 5)"
    )
    _add_output_flags(p_gene)

    # 9. sra
    p_sra = sub.add_parser(
        "sra",
        help="Fetch SRA RunInfo table (SRR runs, layout, instrument, size_MB, download_path) for any GSE/GSM/PRJNA/SRP",
    )
    p_sra.add_argument(
        "query", nargs="+", help="GSE, GSM, PRJNA, SRP, SRX, SRR accession, or SRA query"
    )
    p_sra.add_argument(
        "-n", "--limit", type=int, default=200, help="Max SRA experiments to query (default 200)"
    )
    p_sra.add_argument(
        "--max-rows", type=int, default=20, help="Max rows to display in terminal (default 20)"
    )
    _add_output_flags(p_sra)

    # 10. fasta
    p_fasta = sub.add_parser(
        "fasta", help="Fetch Nucleotide or Protein FASTA sequence(s) from NCBI Entrez"
    )
    p_fasta.add_argument(
        "accessions",
        nargs="+",
        help="Nucleotide or Protein accession(s) (e.g., NM_007294.4 NP_009225.1)",
    )
    p_fasta.add_argument(
        "--db",
        default="nuccore",
        choices=["nuccore", "nucleotide", "protein"],
        help="Entrez database (default: nuccore)",
    )
    p_fasta.add_argument("--start", type=int, help="Optional 1-based sequence start coordinate")
    p_fasta.add_argument("--stop", type=int, help="Optional 1-based sequence stop coordinate")
    _add_output_flags(p_fasta)

    # 11. link
    p_link = sub.add_parser(
        "link", help="Find cross-database links (GSE <-> PMID <-> BioProject <-> SRA)"
    )
    p_link.add_argument(
        "accession", help="GSE/GSM accession or PMID (e.g., GSE85506 or PMID:27713574)"
    )
    _add_output_flags(p_link)

    # 11b. gpl
    p_gpl = sub.add_parser(
        "gpl",
        help="Fetch a GPL platform annotation table (probe -> gene symbol / Entrez ID / chromosome)",
    )
    p_gpl.add_argument("gpl_id", help="Platform accession (e.g., GPL570, GPL13534)")
    p_gpl.add_argument(
        "--columns", help="Comma-separated subset of columns (e.g., 'Gene Symbol,ENTREZ_GENE_ID')"
    )
    p_gpl.add_argument(
        "-n", "--limit", type=int, help="Read only the first N probe rows (stops streaming early)"
    )
    p_gpl.add_argument(
        "--source",
        default="auto",
        choices=["auto", "data", "annot"],
        help="'data' (GEO table, universal) or 'annot' (NCBI-curated .annot.gz)",
    )
    p_gpl.add_argument(
        "--max-rows", type=int, default=10, help="Max rows to display in terminal (default 10)"
    )
    _add_output_flags(p_gpl)

    # 11c. variant / clinvar
    for name, help_text in (
        (
            "variant",
            "Structured ClinVar + dbSNP lookup for a gene symbol, rsID, HGVS, or VCV accession",
        ),
        ("clinvar", "Structured ClinVar lookup (alias of `variant --source clinvar`)"),
    ):
        p_var = sub.add_parser(name, help=help_text)
        p_var.add_argument(
            "query",
            nargs="+",
            help="Gene symbol (BRCA1), rsID (rs7412), HGVS (NM_007294.4:c.5266dupC), VCV accession, or text",
        )
        p_var.add_argument(
            "--source",
            default="auto",
            choices=["auto", "clinvar", "dbsnp"],
            help="Database(s) to query (default: auto)",
        )
        p_var.add_argument(
            "--significance",
            help="ClinVar clinical significance filter (e.g., 'pathogenic', 'likely pathogenic', 'benign')",
        )
        p_var.add_argument("--gene", help="Restrict ClinVar results to a gene symbol")
        p_var.add_argument(
            "-n", "--limit", type=int, default=20, help="Max ClinVar variants (default 20)"
        )
        _add_output_flags(p_var)

    # 11d. bundle
    p_bundle = sub.add_parser(
        "bundle",
        help="Build a self-contained local study directory (overview, samples, files, SRA runs, paper + methods) for a GSE in one command",
    )
    p_bundle.add_argument("gse_id", help="GSE accession (e.g., GSE152418)")
    p_bundle.add_argument(
        "-w", "--where", help="Optional cohort filter applied to samples_metadata.csv"
    )
    p_bundle.add_argument(
        "--matrix",
        action="store_true",
        help="Also download the primary data table (annotated RNA-seq counts or Series Matrix)",
    )
    p_bundle.add_argument("--no-sra", action="store_true", help="Skip the SRA RunInfo table")
    p_bundle.add_argument("--no-pubmed", action="store_true", help="Skip linked PubMed articles")
    p_bundle.add_argument(
        "--no-methods", action="store_true", help="Skip PMC full-text Methods extraction"
    )
    p_bundle.add_argument("--no-sizes", action="store_true", help="Skip HTTP HEAD file size checks")
    p_bundle.add_argument(
        "--subseries",
        action="store_true",
        help="For a SuperSeries: also build one bundle per SubSeries under subseries/<GSE>/",
    )
    _add_output_flags(p_bundle)

    # 11e. taxonomy
    p_tax = sub.add_parser(
        "taxonomy",
        help="Lookup NCBI Taxonomy records (TaxID, names, rank, full lineage) by name or TaxID",
    )
    p_tax.add_argument(
        "queries", nargs="+", help="Organism name(s) or TaxID(s) (e.g., 'Homo sapiens' human 10090)"
    )
    p_tax.add_argument(
        "-n", "--limit", type=int, default=5, help="Max matches per name query (default 5)"
    )
    _add_output_flags(p_tax)

    # 11f. fastq
    p_fq = sub.add_parser(
        "fastq",
        help="Resolve any GSE/GSM/PRJNA/SRP/SRX/SRR to FASTQ files (URLs, sizes, MD5) and optionally download them in parallel",
    )
    p_fq.add_argument("accession", help="GSE, GSM, PRJNA, SRP, SRX, SRS, SAMN, or SRR accession")
    p_fq.add_argument(
        "--download",
        action="store_true",
        help="Download the FASTQ files (MD5-verified) into -o DIR",
    )
    p_fq.add_argument(
        "--runs", nargs="+", help="Only these run accessions (SRR...) when downloading"
    )
    p_fq.add_argument("--workers", type=int, default=4, help="Parallel downloads (default 4)")
    p_fq.add_argument(
        "--no-verify", action="store_true", help="Skip MD5 verification after download"
    )
    p_fq.add_argument(
        "--max-rows", type=int, default=20, help="Max rows to display in terminal (default 20)"
    )
    p_fq.add_argument(
        "--max-download-mb",
        type=float,
        default=None,
        help="Operation byte budget in MB; 0 disables",
    )
    p_fq.add_argument("--no-disk-check", action="store_true", help="Skip free-space checks")
    _add_output_flags(p_fq)

    # 11g. genome
    p_gen = sub.add_parser(
        "genome",
        help="Search reference genome assemblies or download genome FASTA/GFF3/protein packages (NCBI Datasets)",
    )
    gen_sub = p_gen.add_subparsers(dest="genome_action", required=True)
    p_gs = gen_sub.add_parser(
        "search", help="List assemblies for an organism name, TaxID, or GCF_/GCA_ accession"
    )
    p_gs.add_argument(
        "target",
        help="Organism name (e.g., 'Escherichia coli'), TaxID (9606), or assembly accession",
    )
    p_gs.add_argument(
        "--all-assemblies", action="store_true", help="Include non-reference assemblies"
    )
    p_gs.add_argument("-n", "--limit", type=int, default=10, help="Max assemblies (default 10)")
    _add_output_flags(p_gs)
    p_gd = gen_sub.add_parser(
        "download", help="Download a genome data package by GCF_/GCA_ accession"
    )
    p_gd.add_argument("target", help="Assembly accession (e.g., GCF_000001405.40)")
    p_gd.add_argument(
        "--include",
        default="genome",
        help="Comma-separated: genome,gff3,gtf,gbff,rna,protein,cds,seq-report (default genome)",
    )
    p_gd.add_argument(
        "--keep-zip", action="store_true", help="Keep the raw NCBI zip instead of unpacking"
    )
    p_gd.add_argument(
        "--max-download-mb",
        type=float,
        default=None,
        help="Operation byte budget in MB; 0 disables",
    )
    p_gd.add_argument("--no-disk-check", action="store_true", help="Skip free-space checks")
    _add_output_flags(p_gd)

    # 11h. gds
    p_gds = sub.add_parser(
        "gds", help="Load a curated GEO DataSet (GDS) value table with its experimental subsets"
    )
    p_gds.add_argument("gds_id", help="GDS accession (e.g., GDS1234)")
    p_gds.add_argument(
        "--full",
        action="store_true",
        help="Fetch the _full.soft.gz variant with extra annotation columns",
    )
    p_gds.add_argument(
        "--max-rows", type=int, default=10, help="Max rows to display in terminal (default 10)"
    )
    _add_output_flags(p_gds)

    # 11i. metadata / schema
    p_meta = sub.add_parser(
        "metadata",
        help="Build the versioned study record (samples, harmonized characteristics with provenance, links, files, quality) for a GSE",
    )
    p_meta.add_argument("gse_id", help="GSE accession")
    p_meta.add_argument("-w", "--where", help="Optional cohort filter applied to the samples")
    p_meta.add_argument("--enrich", help="Comma-separated enrichments: biosample,sra,pubmed")
    p_meta.add_argument("--no-sizes", action="store_true", help="Skip HTTP HEAD file size checks")
    p_meta.add_argument(
        "--no-samples",
        action="store_true",
        help="Omit per-sample records from --json output (summary only)",
    )
    p_meta.add_argument(
        "--max-rows", type=int, default=15, help="Max samples in Markdown/text views (default 15)"
    )
    _add_output_flags(p_meta)
    p_schema = sub.add_parser(
        "schema", help="Print the JSON Schema of PyNCBI records, or the controlled vocabulary"
    )
    p_schema.add_argument(
        "name",
        choices=["study", "sample", "vocabulary", "mcp"],
        help="'study'/'sample' (record JSON Schema), 'vocabulary' (canonical fields) or 'mcp' (full agent contract)",
    )
    _add_output_flags(p_schema)

    # 11j. expression
    p_expr = sub.add_parser(
        "expression",
        help="Resolve and load the best analysis-ready expression matrix for a GSE (NCBI counts -> Series Matrix -> supplementary -> per-sample -> on-page -> tar), aligned to GSM IDs",
    )
    p_expr.add_argument("gse_id", help="GSE accession")
    p_expr.add_argument(
        "--prefer",
        default="auto",
        choices=["auto", "rnaseq", "matrix", "supp", "per_sample", "table", "tar"],
        help="Force a source instead of the automatic cascade (default: auto)",
    )
    p_expr.add_argument(
        "--count-type",
        default="raw_counts",
        help="For NCBI counts: raw_counts (default), norm_counts_TPM, norm_counts_FPKM",
    )
    p_expr.add_argument(
        "--annotate",
        action="store_true",
        help="Prepend gene/probe annotation columns to the matrix",
    )
    p_expr.add_argument(
        "-w", "--where", help="Cohort filter restricting the sample sheet and matrix columns"
    )
    p_expr.add_argument(
        "--max-download-mb",
        type=float,
        default=2048.0,
        help="Refuse candidate files larger than this (default 2048)",
    )
    p_expr.add_argument(
        "--workers", type=int, default=4, help="Parallel workers for per-sample sources (default 4)"
    )
    p_expr.add_argument(
        "--max-rows", type=int, default=8, help="Rows to show in terminal/JSON preview (default 8)"
    )
    _add_output_flags(p_expr)

    # 11k. screen
    p_screen = sub.add_parser(
        "screen",
        help="Screen many GSEs (or a GEO query) into one comparison table: samples, available sources, download size, metadata coverage and quality",
    )
    p_screen.add_argument(
        "targets", nargs="+", metavar="GSE_OR_QUERY", help="GSE accessions, or a GEO search query"
    )
    p_screen.add_argument(
        "--require", help="Keep only studies harmonizing these fields (e.g. 'sex,age')"
    )
    p_screen.add_argument("--organism", help="Search filter when screening a query")
    p_screen.add_argument("--type", help="Search filter: rnaseq, methylation, microarray, ...")
    p_screen.add_argument("--min-samples", type=int, help="Search filter: minimum samples")
    p_screen.add_argument(
        "--sort-by", help="Sort descending by this column (e.g. sample_count, quality_score)"
    )
    p_screen.add_argument(
        "-n",
        "--limit",
        type=int,
        default=20,
        help="Max studies to screen when searching (default 20)",
    )
    p_screen.add_argument(
        "--no-sizes",
        action="store_true",
        help="Skip HTTP HEAD size checks (faster, no download_bytes)",
    )
    p_screen.add_argument(
        "--workers", type=int, default=4, help="Studies screened in parallel (default 4)"
    )
    _add_output_flags(p_screen)

    # 11l. blast
    p_blast = sub.add_parser(
        "blast",
        help="Run a BLAST sequence similarity search (submit, poll, fetch), or fetch an existing job with --rid",
    )
    p_blast.add_argument(
        "query", nargs="?", help="Sequence, FASTA text, accession, or path to a file"
    )
    p_blast.add_argument(
        "--rid", help="Fetch/poll an existing BLAST Request ID instead of submitting"
    )
    p_blast.add_argument(
        "--program",
        default="blastn",
        choices=["blastn", "blastp", "blastx", "tblastn", "tblastx"],
        help="BLAST program (default blastn)",
    )
    p_blast.add_argument(
        "--database", help="Target database (default: nt for nucleotide, nr for protein)"
    )
    p_blast.add_argument("--evalue", type=float, help="Expect-value threshold")
    p_blast.add_argument(
        "--hitlist-size", type=int, default=50, help="Max hits to keep (default 50)"
    )
    p_blast.add_argument("--entrez-query", help='Entrez filter, e.g. "Homo sapiens"[organism]')
    p_blast.add_argument(
        "--no-megablast",
        action="store_true",
        help="For blastn: use the slower, more sensitive algorithm",
    )
    p_blast.add_argument(
        "--no-wait", action="store_true", help="Submit and print the RID instead of waiting"
    )
    p_blast.add_argument(
        "--poll-interval",
        type=float,
        default=60.0,
        help="Seconds between status checks (default 60)",
    )
    p_blast.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help="Give up waiting after this many seconds (default 900)",
    )
    p_blast.add_argument(
        "--max-rows", type=int, default=20, help="Max hits to show in terminal (default 20)"
    )
    _add_output_flags(p_blast)

    # 12. cache
    p_cache = sub.add_parser("cache", help="Inspect or clear the local PyNCBI cache")
    p_cache.add_argument("action", choices=["status", "list", "clear"], help="Cache action")
    p_cache.add_argument(
        "--pattern", default="*", help="Glob pattern when clearing cache (default '*')"
    )
    _add_output_flags(p_cache)

    # 13. skill
    p_skill = sub.add_parser("skill", help="Show or install the PyNCBI AI Agent Skill (SKILL.md)")
    p_skill.add_argument("action", choices=["show", "install"], help="'show' or 'install'")
    p_skill.add_argument(
        "--target",
        default="all",
        choices=["antigravity", "claude", "cursor", "all"],
        help="Agent platform to install skill into (default: all)",
    )
    p_skill.add_argument("--dir", help="Custom directory to write SKILL.md into")

    # 14. status
    p_status = sub.add_parser(
        "status",
        help="Diagnose NCBI backend connectivity, API key status, rate limiters, cache, and telemetry",
    )
    p_status.add_argument(
        "--offline",
        action="store_true",
        help="Skip live network probes and only report local configuration, cache, and telemetry",
    )
    _add_output_flags(p_status)

    # 15. mcp
    p_mcp = sub.add_parser(
        "mcp",
        help="Start the PyNCBI Model Context Protocol (MCP) server (stdio by default, or --http)",
    )
    p_mcp.add_argument(
        "--http",
        action="store_true",
        help="Serve the Streamable HTTP transport (POST /mcp) instead of stdio",
    )
    p_mcp.add_argument(
        "--host", default="127.0.0.1", help="Bind address for --http (default 127.0.0.1)"
    )
    p_mcp.add_argument("--port", type=int, default=8765, help="Port for --http (default 8765)")

    return parser
