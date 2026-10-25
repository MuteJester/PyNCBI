Quickstart
==========

Why PyNCBI
----------

Working with NCBI by hand means juggling E-utilities rate limits, SOFT and Series Matrix
formats, GEO's CGI download endpoints (some browser-gated), ``ftp://`` links, ragged sample
characteristics, and five different JSON schemas for ``esummary``. PyNCBI turns all of that
into a handful of calls that return pandas DataFrames or small dataclasses, preserve parsed measurements through caching/export and report operational failures with
clear exceptions, ``hint`` and stable ``error_code`` values.

Five-minute tour (CLI)
----------------------

.. code-block:: bash

   # 1. Discover
   pyncbi search geo "fibromyalgia methylation" --organism "Homo sapiens" --markdown

   # 2. Everything about a study in one directory (overview, samples, files, SRA, paper + Methods)
   pyncbi bundle GSE152418 -o ./GSE152418/

   # 3. Only the files you need, for the samples you need, in parallel
   pyncbi download GSE85506 --where "group == 'case'" --pattern "*.idat.gz" -o ./fm/

   # 4a. The matrix, wherever the study keeps it (counts / Series Matrix / supplementary / tar)
   pyncbi expression GSE152418 -o counts.csv

   # 4. Tables with gene symbols, not bare GeneIDs / probe IDs
   pyncbi download GSE152418 --source rnaseq --annotate -o counts_annotated.csv

   # 5. Reads, variants, genomes, taxonomy
   pyncbi fastq GSE152418 --download -o ./fastq/
   pyncbi variant BRCA1 --significance pathogenic
   pyncbi genome search "Escherichia coli" ; pyncbi genome download GCF_000005845.2 --include genome,gff3
   pyncbi taxonomy human

Five-minute tour (Python)
-------------------------

.. code-block:: python

   import PyNCBI

   report = PyNCBI.inspect_accession("GSE152418")          # design, cohort breakdown, files + sizes
   samples, chars, summary = PyNCBI.extract_gse_samples("GSE152418", where="severity == 'Severe'")
   matrix = PyNCBI.load_expression("GSE152418")            # resolves whichever source the study actually uses
   counts = PyNCBI.load_rnaseq_counts("GSE152418", annotate=True)
   paper = PyNCBI.fetch_pmc_text("32788292", sections=["methods", "data_availability"])
   fastq = PyNCBI.fetch_fastq_urls("GSE152418")            # one row per FASTQ file, with MD5s
   variants = PyNCBI.search_clinvar("BRCA1", significance="pathogenic", limit=None)  # paginated
   bundle = PyNCBI.build_study_bundle("GSE152418", "./GSE152418/")

Errors you can branch on
------------------------

Operational exceptions derive from :class:`PyNCBI.PyNCBIError` and carries ``.message`` and
``.hint``; ``PyNCBI.error_code_for(exc)`` maps it to a stable code (``not_found``,
``private``, ``no_data``, ``rate_limited``, ``bot_challenge``, ``timeout`` ...) and
``PyNCBI.is_retryable(exc)`` says whether to retry. The CLI ``--json`` error envelope and the
MCP server return the same fields.
