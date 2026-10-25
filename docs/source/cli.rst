CLI Reference
=============

Generated from the ``pyncbi`` argparse definition. Every command supports ``--json`` (agent envelope), ``--markdown``, ``-o`` (save), ``-v``/``-q``.

.. code-block:: text

   usage: pyncbi [-h] [--version] [--no-color] [-v] [-q]
                 {search,inspect,samples,preview,files,download,pubmed,gene,sra,fasta,link,gpl,variant,clinvar,bundle,taxonomy,fastq,genome,gds,metadata,schema,expression,screen,blast,cache,skill,status,mcp}
                 ...

   PyNCBI — Programmatic & Agent-Ready CLI and MCP Server for NCBI (GEO, Entrez,
   PubMed, Gene, SRA)

   positional arguments:
     {search,inspect,samples,preview,files,download,pubmed,gene,sra,fasta,link,gpl,variant,clinvar,bundle,taxonomy,fastq,genome,gds,metadata,schema,expression,screen,blast,cache,skill,status,mcp}
       search              Search NCBI databases (geo, pubmed, gene, sra,
                           bioproject)
       inspect             Inspect any GSE, GSM, or GPL record (metadata, sample
                           breakdown & file sizes)
       samples             Fetch & organize all sample metadata and
                           characteristics for a GSE (1 batch request)
       preview             Stream & preview remote GEO file columns, row IDs, or
                           archive members from a bounded prefix without full
                           download
       files               List all downloadable files and byte sizes for a GSE,
                           GSM, or GPL
       download            Download raw files or data tables from NCBI GEO (one
                           or many accessions, optional cohort filter)
       pubmed              Fetch PubMed article metadata, abstracts, and
                           (optionally) PMC full-text Methods sections by PMID
       gene                Lookup NCBI Gene records by symbol or Entrez Gene ID
       sra                 Fetch SRA RunInfo table (SRR runs, layout, instrument,
                           size_MB, download_path) for any GSE/GSM/PRJNA/SRP
       fasta               Fetch Nucleotide or Protein FASTA sequence(s) from
                           NCBI Entrez
       link                Find cross-database links (GSE <-> PMID <-> BioProject
                           <-> SRA)
       gpl                 Fetch a GPL platform annotation table (probe -> gene
                           symbol / Entrez ID / chromosome)
       variant             Structured ClinVar + dbSNP lookup for a gene symbol,
                           rsID, HGVS, or VCV accession
       clinvar             Structured ClinVar lookup (alias of `variant --source
                           clinvar`)
       bundle              Build a self-contained local study directory
                           (overview, samples, files, SRA runs, paper + methods)
                           for a GSE in one command
       taxonomy            Lookup NCBI Taxonomy records (TaxID, names, rank, full
                           lineage) by name or TaxID
       fastq               Resolve any GSE/GSM/PRJNA/SRP/SRX/SRR to FASTQ files
                           (URLs, sizes, MD5) and optionally download them in
                           parallel
       genome              Search reference genome assemblies or download genome
                           FASTA/GFF3/protein packages (NCBI Datasets)
       gds                 Load a curated GEO DataSet (GDS) value table with its
                           experimental subsets
       metadata            Build the versioned study record (samples, harmonized
                           characteristics with provenance, links, files,
                           quality) for a GSE
       schema              Print the JSON Schema of PyNCBI records, or the
                           controlled vocabulary
       expression          Resolve and load the best analysis-ready expression
                           matrix for a GSE (NCBI counts -> Series Matrix ->
                           supplementary -> per-sample -> on-page -> tar),
                           aligned to GSM IDs
       screen              Screen many GSEs (or a GEO query) into one comparison
                           table: samples, available sources, download size,
                           metadata coverage and quality
       blast               Run a BLAST sequence similarity search (submit, poll,
                           fetch), or fetch an existing job with --rid
       cache               Inspect or clear the local PyNCBI cache
       skill               Show or install the PyNCBI AI Agent Skill (SKILL.md)
       status              Diagnose NCBI backend connectivity, API key status,
                           rate limiters, cache, and telemetry
       mcp                 Start the PyNCBI Model Context Protocol (MCP) server
                           (stdio by default, or --http)

   options:
     -h, --help            show this help message and exit
     --version             show program's version number and exit
     --no-color            Disable ANSI colors in all output
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

   Set PYNCBI_AGENT_MODE=1 for machine-oriented output (no progress bars/colors);
   NO_COLOR is honoured.

``pyncbi search``
-----------------

.. code-block:: text

   usage: pyncbi search [-h] [--organism ORGANISM] [--type TYPE]
                        [--entry-type ENTRY_TYPE] [--min-samples MIN_SAMPLES]
                        [-n LIMIT] [--all] [-o OUTPUT] [--json] [--markdown] [-v]
                        [-q]
                        db query [query ...]

   positional arguments:
     db                    Database: 'geo', 'pubmed', 'gene', 'sra',
                           'bioproject', 'biosample', 'clinvar'
     query                 Search keywords or Entrez query

   options:
     -h, --help            show this help message and exit
     --organism ORGANISM   Filter by organism (e.g., 'Homo sapiens', 'Mus
                           musculus')
     --type TYPE           For GEO: dataset type ('rnaseq', 'methylation',
                           'microarray')
     --entry-type ENTRY_TYPE
                           For GEO: 'gse' (default), 'gsm', 'gpl', or 'all'
     --min-samples MIN_SAMPLES
                           For GEO: minimum number of samples
     -n LIMIT, --limit LIMIT
                           Max results to return (default 15)
     --all                 Return every match (paginates through NCBI; use with
                           -o for large result sets)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi inspect``
------------------

.. code-block:: text

   usage: pyncbi inspect [-h] [--no-sizes] [-o OUTPUT] [--json] [--markdown] [-v]
                         [-q]
                         accession

   positional arguments:
     accession             GEO accession (e.g., GSE85506, GSM1518180, GPL13534,
                           GDS1234)

   options:
     -h, --help            show this help message and exit
     --no-sizes            Skip HTTP HEAD file size checks
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi samples``
------------------

.. code-block:: text

   usage: pyncbi samples [-h] [-w WHERE] [--characteristics-only] [--harmonize]
                         [--summary] [--max-rows MAX_ROWS] [-o OUTPUT] [--json]
                         [--markdown] [-v] [-q]
                         gse_id

   positional arguments:
     gse_id                GSE accession (e.g., GSE62003, GSE85506)

   options:
     -h, --help            show this help message and exit
     -w WHERE, --where WHERE
                           Filter sample cohort (e.g., "disease == 'Control'",
                           "age > 50", or "PBMC")
     --characteristics-only
                           Only return unpacked key:value characteristics columns
     --harmonize           Map characteristics to the controlled vocabulary (sex,
                           age, tissue, disease, ...) keeping raw values as
                           raw:<key>
     --summary             Only display the cohort characteristics breakdown
                           summary
     --max-rows MAX_ROWS   Max preview rows in terminal (default 15)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi preview``
------------------

.. code-block:: text

   usage: pyncbi preview [-h] [--source {auto,supp,matrix,rnaseq,table}]
                         [--file-index FILE_INDEX] [--pattern PATTERN] [-n ROWS]
                         [-o OUTPUT] [--json] [--markdown] [-v] [-q]
                         accession

   positional arguments:
     accession             GSE, GSM, or GPL accession

   options:
     -h, --help            show this help message and exit
     --source {auto,supp,matrix,rnaseq,table}
                           File source to preview (default: auto)
     --file-index FILE_INDEX
                           0-based supplementary file index to preview
     --pattern PATTERN     Filename glob filter for supplementary files
     -n ROWS, --rows ROWS  Number of preview rows (default 8)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi files``
----------------

.. code-block:: text

   usage: pyncbi files [-h] [--no-sizes] [-o OUTPUT] [--json] [--markdown] [-v]
                       [-q]
                       accession

   positional arguments:
     accession             GEO accession (e.g., GSE85506, GSM1518180)

   options:
     -h, --help            show this help message and exit
     --no-sizes            Skip HTTP HEAD file size checks
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi download``
-------------------

.. code-block:: text

   usage: pyncbi download [-h] [-w WHERE] [--workers WORKERS] [--annotate]
                          [--dry-run] [--max-download-mb MAX_DOWNLOAD_MB]
                          [--no-disk-check] [--subseries]
                          [--source {auto,supp,matrix,rnaseq,table}]
                          [--file-index FILE_INDEX] [--pattern PATTERN]
                          [--unpack] [-o OUTPUT] [--json] [--markdown] [-v] [-q]
                          ACCESSION [ACCESSION ...]

   positional arguments:
     ACCESSION             One or more GSE, GSM, or GPL accessions (multiple run
                           in parallel)

   options:
     -h, --help            show this help message and exit
     -w WHERE, --where WHERE
                           For a GSE: cohort filter; downloads only matching
                           samples' own files (e.g., "severity == 'Severe'")
     --workers WORKERS     Parallel download workers (default 4)
     --annotate            For --source rnaseq/matrix: prepend gene symbol
                           annotation columns to the exported table
     --dry-run             Report exactly which files would be fetched, their
                           sizes and the total, without downloading
     --max-download-mb MAX_DOWNLOAD_MB
                           Refuse the download above this many MB (default:
                           PYNCBI_MAX_DOWNLOAD_MB or 2048)
     --no-disk-check       Skip the free-disk-space check before downloading
     --subseries           For a SuperSeries: download every SubSeries into its
                           own sub-folder
     --source {auto,supp,matrix,rnaseq,table}
                           File source to download (default: auto)
     --file-index FILE_INDEX
                           0-based supplementary file index to download
     --pattern PATTERN     Filename glob filter for supplementary files (e.g.,
                           '*.txt.gz')
     --unpack              Safely extract .tar archives and gunzip .gz files
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi pubmed``
-----------------

.. code-block:: text

   usage: pyncbi pubmed [-h] [--methods] [--fulltext] [--sections SECTIONS]
                        [--format {bibtex,ris}] [-o OUTPUT] [--json] [--markdown]
                        [-v] [-q]
                        pmids [pmids ...]

   positional arguments:
     pmids                 One or more PubMed IDs (e.g., 27713574)

   options:
     -h, --help            show this help message and exit
     --methods             Also fetch the PMC open-access Methods and Data
                           Availability sections
     --fulltext            Also fetch the complete PMC open-access full text (all
                           sections)
     --sections SECTIONS   Comma-separated PMC section kinds or title substrings
                           to fetch (e.g., 'methods,results,data_availability')
     --format {bibtex,ris}
                           Export citations as BibTeX or RIS (prints to stdout;
                           with -o refs.bib/.ris writes the file)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi gene``
---------------

.. code-block:: text

   usage: pyncbi gene [-h] [--organism ORGANISM] [-n LIMIT] [-o OUTPUT] [--json]
                      [--markdown] [-v] [-q]
                      queries [queries ...]

   positional arguments:
     queries               Gene symbol(s) or Entrez Gene ID(s) (e.g., BRCA1 TP53)

   options:
     -h, --help            show this help message and exit
     --organism ORGANISM   Organism filter (default: 'Homo sapiens')
     -n LIMIT, --limit LIMIT
                           Max matches per query (default 5)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi sra``
--------------

.. code-block:: text

   usage: pyncbi sra [-h] [-n LIMIT] [--max-rows MAX_ROWS] [-o OUTPUT] [--json]
                     [--markdown] [-v] [-q]
                     query [query ...]

   positional arguments:
     query                 GSE, GSM, PRJNA, SRP, SRX, SRR accession, or SRA query

   options:
     -h, --help            show this help message and exit
     -n LIMIT, --limit LIMIT
                           Max SRA experiments to query (default 200)
     --max-rows MAX_ROWS   Max rows to display in terminal (default 20)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi fasta``
----------------

.. code-block:: text

   usage: pyncbi fasta [-h] [--db {nuccore,nucleotide,protein}] [--start START]
                       [--stop STOP] [-o OUTPUT] [--json] [--markdown] [-v] [-q]
                       accessions [accessions ...]

   positional arguments:
     accessions            Nucleotide or Protein accession(s) (e.g., NM_007294.4
                           NP_009225.1)

   options:
     -h, --help            show this help message and exit
     --db {nuccore,nucleotide,protein}
                           Entrez database (default: nuccore)
     --start START         Optional 1-based sequence start coordinate
     --stop STOP           Optional 1-based sequence stop coordinate
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi link``
---------------

.. code-block:: text

   usage: pyncbi link [-h] [-o OUTPUT] [--json] [--markdown] [-v] [-q] accession

   positional arguments:
     accession             GSE/GSM accession or PMID (e.g., GSE85506 or
                           PMID:27713574)

   options:
     -h, --help            show this help message and exit
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi gpl``
--------------

.. code-block:: text

   usage: pyncbi gpl [-h] [--columns COLUMNS] [-n LIMIT]
                     [--source {auto,data,annot}] [--max-rows MAX_ROWS]
                     [-o OUTPUT] [--json] [--markdown] [-v] [-q]
                     gpl_id

   positional arguments:
     gpl_id                Platform accession (e.g., GPL570, GPL13534)

   options:
     -h, --help            show this help message and exit
     --columns COLUMNS     Comma-separated subset of columns (e.g., 'Gene
                           Symbol,ENTREZ_GENE_ID')
     -n LIMIT, --limit LIMIT
                           Read only the first N probe rows (stops streaming
                           early)
     --source {auto,data,annot}
                           'data' (GEO table, universal) or 'annot' (NCBI-curated
                           .annot.gz)
     --max-rows MAX_ROWS   Max rows to display in terminal (default 10)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi variant``
------------------

.. code-block:: text

   usage: pyncbi variant [-h] [--source {auto,clinvar,dbsnp}]
                         [--significance SIGNIFICANCE] [--gene GENE] [-n LIMIT]
                         [-o OUTPUT] [--json] [--markdown] [-v] [-q]
                         query [query ...]

   positional arguments:
     query                 Gene symbol (BRCA1), rsID (rs7412), HGVS
                           (NM_007294.4:c.5266dupC), VCV accession, or text

   options:
     -h, --help            show this help message and exit
     --source {auto,clinvar,dbsnp}
                           Database(s) to query (default: auto)
     --significance SIGNIFICANCE
                           ClinVar clinical significance filter (e.g.,
                           'pathogenic', 'likely pathogenic', 'benign')
     --gene GENE           Restrict ClinVar results to a gene symbol
     -n LIMIT, --limit LIMIT
                           Max ClinVar variants (default 20)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi clinvar``
------------------

.. code-block:: text

   usage: pyncbi clinvar [-h] [--source {auto,clinvar,dbsnp}]
                         [--significance SIGNIFICANCE] [--gene GENE] [-n LIMIT]
                         [-o OUTPUT] [--json] [--markdown] [-v] [-q]
                         query [query ...]

   positional arguments:
     query                 Gene symbol (BRCA1), rsID (rs7412), HGVS
                           (NM_007294.4:c.5266dupC), VCV accession, or text

   options:
     -h, --help            show this help message and exit
     --source {auto,clinvar,dbsnp}
                           Database(s) to query (default: auto)
     --significance SIGNIFICANCE
                           ClinVar clinical significance filter (e.g.,
                           'pathogenic', 'likely pathogenic', 'benign')
     --gene GENE           Restrict ClinVar results to a gene symbol
     -n LIMIT, --limit LIMIT
                           Max ClinVar variants (default 20)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi bundle``
-----------------

.. code-block:: text

   usage: pyncbi bundle [-h] [-w WHERE] [--matrix] [--no-sra] [--no-pubmed]
                        [--no-methods] [--no-sizes] [--subseries] [-o OUTPUT]
                        [--json] [--markdown] [-v] [-q]
                        gse_id

   positional arguments:
     gse_id                GSE accession (e.g., GSE152418)

   options:
     -h, --help            show this help message and exit
     -w WHERE, --where WHERE
                           Optional cohort filter applied to samples_metadata.csv
     --matrix              Also download the primary data table (annotated RNA-
                           seq counts or Series Matrix)
     --no-sra              Skip the SRA RunInfo table
     --no-pubmed           Skip linked PubMed articles
     --no-methods          Skip PMC full-text Methods extraction
     --no-sizes            Skip HTTP HEAD file size checks
     --subseries           For a SuperSeries: also build one bundle per SubSeries
                           under subseries/<GSE>/
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi taxonomy``
-------------------

.. code-block:: text

   usage: pyncbi taxonomy [-h] [-n LIMIT] [-o OUTPUT] [--json] [--markdown] [-v]
                          [-q]
                          queries [queries ...]

   positional arguments:
     queries               Organism name(s) or TaxID(s) (e.g., 'Homo sapiens'
                           human 10090)

   options:
     -h, --help            show this help message and exit
     -n LIMIT, --limit LIMIT
                           Max matches per name query (default 5)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi fastq``
----------------

.. code-block:: text

   usage: pyncbi fastq [-h] [--download] [--runs RUNS [RUNS ...]]
                       [--workers WORKERS] [--no-verify] [--max-rows MAX_ROWS]
                       [--max-download-mb MAX_DOWNLOAD_MB] [--no-disk-check]
                       [-o OUTPUT] [--json] [--markdown] [-v] [-q]
                       accession

   positional arguments:
     accession             GSE, GSM, PRJNA, SRP, SRX, SRS, SAMN, or SRR accession

   options:
     -h, --help            show this help message and exit
     --download            Download the FASTQ files (MD5-verified) into -o DIR
     --runs RUNS [RUNS ...]
                           Only these run accessions (SRR...) when downloading
     --workers WORKERS     Parallel downloads (default 4)
     --no-verify           Skip MD5 verification after download
     --max-rows MAX_ROWS   Max rows to display in terminal (default 20)
     --max-download-mb MAX_DOWNLOAD_MB
                           Operation byte budget in MB; 0 disables
     --no-disk-check       Skip free-space checks
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi genome``
-----------------

.. code-block:: text

   usage: pyncbi genome [-h] {search,download} ...

   positional arguments:
     {search,download}
       search           List assemblies for an organism name, TaxID, or GCF_/GCA_
                        accession
       download         Download a genome data package by GCF_/GCA_ accession

   options:
     -h, --help         show this help message and exit

``pyncbi genome search``
^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: text

   usage: pyncbi genome search [-h] [--all-assemblies] [-n LIMIT] [-o OUTPUT]
                               [--json] [--markdown] [-v] [-q]
                               target

   positional arguments:
     target                Organism name (e.g., 'Escherichia coli'), TaxID
                           (9606), or assembly accession

   options:
     -h, --help            show this help message and exit
     --all-assemblies      Include non-reference assemblies
     -n LIMIT, --limit LIMIT
                           Max assemblies (default 10)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi genome download``
^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: text

   usage: pyncbi genome download [-h] [--include INCLUDE] [--keep-zip]
                                 [--max-download-mb MAX_DOWNLOAD_MB]
                                 [--no-disk-check] [-o OUTPUT] [--json]
                                 [--markdown] [-v] [-q]
                                 target

   positional arguments:
     target                Assembly accession (e.g., GCF_000001405.40)

   options:
     -h, --help            show this help message and exit
     --include INCLUDE     Comma-separated:
                           genome,gff3,gtf,gbff,rna,protein,cds,seq-report
                           (default genome)
     --keep-zip            Keep the raw NCBI zip instead of unpacking
     --max-download-mb MAX_DOWNLOAD_MB
                           Operation byte budget in MB; 0 disables
     --no-disk-check       Skip free-space checks
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi gds``
--------------

.. code-block:: text

   usage: pyncbi gds [-h] [--full] [--max-rows MAX_ROWS] [-o OUTPUT] [--json]
                     [--markdown] [-v] [-q]
                     gds_id

   positional arguments:
     gds_id                GDS accession (e.g., GDS1234)

   options:
     -h, --help            show this help message and exit
     --full                Fetch the _full.soft.gz variant with extra annotation
                           columns
     --max-rows MAX_ROWS   Max rows to display in terminal (default 10)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi metadata``
-------------------

.. code-block:: text

   usage: pyncbi metadata [-h] [-w WHERE] [--enrich ENRICH] [--no-sizes]
                          [--no-samples] [--max-rows MAX_ROWS] [-o OUTPUT]
                          [--json] [--markdown] [-v] [-q]
                          gse_id

   positional arguments:
     gse_id                GSE accession

   options:
     -h, --help            show this help message and exit
     -w WHERE, --where WHERE
                           Optional cohort filter applied to the samples
     --enrich ENRICH       Comma-separated enrichments: biosample,sra,pubmed
     --no-sizes            Skip HTTP HEAD file size checks
     --no-samples          Omit per-sample records from --json output (summary
                           only)
     --max-rows MAX_ROWS   Max samples in Markdown/text views (default 15)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi schema``
-----------------

.. code-block:: text

   usage: pyncbi schema [-h] [-o OUTPUT] [--json] [--markdown] [-v] [-q]
                        {study,sample,vocabulary,mcp}

   positional arguments:
     {study,sample,vocabulary,mcp}
                           'study'/'sample' (record JSON Schema), 'vocabulary'
                           (canonical fields) or 'mcp' (full agent contract)

   options:
     -h, --help            show this help message and exit
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi expression``
---------------------

.. code-block:: text

   usage: pyncbi expression [-h]
                            [--prefer {auto,rnaseq,matrix,supp,per_sample,table,tar}]
                            [--count-type COUNT_TYPE] [--annotate] [-w WHERE]
                            [--max-download-mb MAX_DOWNLOAD_MB]
                            [--workers WORKERS] [--max-rows MAX_ROWS] [-o OUTPUT]
                            [--json] [--markdown] [-v] [-q]
                            gse_id

   positional arguments:
     gse_id                GSE accession

   options:
     -h, --help            show this help message and exit
     --prefer {auto,rnaseq,matrix,supp,per_sample,table,tar}
                           Force a source instead of the automatic cascade
                           (default: auto)
     --count-type COUNT_TYPE
                           For NCBI counts: raw_counts (default),
                           norm_counts_TPM, norm_counts_FPKM
     --annotate            Prepend gene/probe annotation columns to the matrix
     -w WHERE, --where WHERE
                           Cohort filter restricting the sample sheet and matrix
                           columns
     --max-download-mb MAX_DOWNLOAD_MB
                           Refuse candidate files larger than this (default 2048)
     --workers WORKERS     Parallel workers for per-sample sources (default 4)
     --max-rows MAX_ROWS   Rows to show in terminal/JSON preview (default 8)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi screen``
-----------------

.. code-block:: text

   usage: pyncbi screen [-h] [--require REQUIRE] [--organism ORGANISM]
                        [--type TYPE] [--min-samples MIN_SAMPLES]
                        [--sort-by SORT_BY] [-n LIMIT] [--no-sizes]
                        [--workers WORKERS] [-o OUTPUT] [--json] [--markdown]
                        [-v] [-q]
                        GSE_OR_QUERY [GSE_OR_QUERY ...]

   positional arguments:
     GSE_OR_QUERY          GSE accessions, or a GEO search query

   options:
     -h, --help            show this help message and exit
     --require REQUIRE     Keep only studies harmonizing these fields (e.g.
                           'sex,age')
     --organism ORGANISM   Search filter when screening a query
     --type TYPE           Search filter: rnaseq, methylation, microarray, ...
     --min-samples MIN_SAMPLES
                           Search filter: minimum samples
     --sort-by SORT_BY     Sort descending by this column (e.g. sample_count,
                           quality_score)
     -n LIMIT, --limit LIMIT
                           Max studies to screen when searching (default 20)
     --no-sizes            Skip HTTP HEAD size checks (faster, no download_bytes)
     --workers WORKERS     Studies screened in parallel (default 4)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi blast``
----------------

.. code-block:: text

   usage: pyncbi blast [-h] [--rid RID]
                       [--program {blastn,blastp,blastx,tblastn,tblastx}]
                       [--database DATABASE] [--evalue EVALUE]
                       [--hitlist-size HITLIST_SIZE]
                       [--entrez-query ENTREZ_QUERY] [--no-megablast] [--no-wait]
                       [--poll-interval POLL_INTERVAL] [--timeout TIMEOUT]
                       [--max-rows MAX_ROWS] [-o OUTPUT] [--json] [--markdown]
                       [-v] [-q]
                       [query]

   positional arguments:
     query                 Sequence, FASTA text, accession, or path to a file

   options:
     -h, --help            show this help message and exit
     --rid RID             Fetch/poll an existing BLAST Request ID instead of
                           submitting
     --program {blastn,blastp,blastx,tblastn,tblastx}
                           BLAST program (default blastn)
     --database DATABASE   Target database (default: nt for nucleotide, nr for
                           protein)
     --evalue EVALUE       Expect-value threshold
     --hitlist-size HITLIST_SIZE
                           Max hits to keep (default 50)
     --entrez-query ENTREZ_QUERY
                           Entrez filter, e.g. "Homo sapiens"[organism]
     --no-megablast        For blastn: use the slower, more sensitive algorithm
     --no-wait             Submit and print the RID instead of waiting
     --poll-interval POLL_INTERVAL
                           Seconds between status checks (default 60)
     --timeout TIMEOUT     Give up waiting after this many seconds (default 900)
     --max-rows MAX_ROWS   Max hits to show in terminal (default 20)
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi cache``
----------------

.. code-block:: text

   usage: pyncbi cache [-h] [--pattern PATTERN] [-o OUTPUT] [--json] [--markdown]
                       [-v] [-q]
                       {status,list,clear}

   positional arguments:
     {status,list,clear}   Cache action

   options:
     -h, --help            show this help message and exit
     --pattern PATTERN     Glob pattern when clearing cache (default '*')
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi skill``
----------------

.. code-block:: text

   usage: pyncbi skill [-h] [--target {codex,antigravity,claude,cursor,all}]
                       [--dir DIR]
                       {show,install}

   positional arguments:
     {show,install}        'show' or 'install'

   options:
     -h, --help            show this help message and exit
     --target {codex,antigravity,claude,cursor,all}
                           Skill destination; all installs user skills for Codex,
                           Claude and Antigravity (Cursor is project-local)
     --dir DIR             Custom directory to write SKILL.md into

``pyncbi status``
-----------------

.. code-block:: text

   usage: pyncbi status [-h] [--offline] [-o OUTPUT] [--json] [--markdown] [-v]
                        [-q]

   options:
     -h, --help            show this help message and exit
     --offline             Skip live network probes and only report local
                           configuration, cache, and telemetry
     -o OUTPUT, --output OUTPUT
                           Save output directly to a file (.csv, .tsv, .json) or
                           directory
     --json                Format terminal output as an agent-friendly JSON
                           envelope
     --markdown, --md      Format terminal output as clean GitHub-Flavored
                           Markdown
     -v, --verbose         Enable step-by-step verbose progress reporting on
                           stderr (-vv for full HTTP debug trace)
     -q, --quiet           Suppress all progress bars and status messages on
                           stderr

``pyncbi mcp``
--------------

.. code-block:: text

   usage: pyncbi mcp [-h] [--http] [--host HOST] [--port PORT]

   options:
     -h, --help   show this help message and exit
     --http       Serve the Streamable HTTP transport (POST /mcp) instead of
                  stdio
     --host HOST  Bind address for --http (default 127.0.0.1)
     --port PORT  Port for --http (default 8765)
