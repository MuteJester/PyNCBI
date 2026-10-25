Additional workflows
====================

Annotation and cohorts
----------------------

.. code-block:: bash

   pyncbi gpl GPL570 --columns "Gene Symbol,ENTREZ_GENE_ID" --markdown
   pyncbi download GSE152418 --source rnaseq --annotate -o annotated_counts.csv
   pyncbi samples GSE152418 --where "severity == 'Severe'" -o severe_samples.csv
   pyncbi metadata GSE152418 --enrich biosample,sra -o ./study_record/
   pyncbi screen GSE152418 GSE85506 --sort-by sample_count --markdown

Inspect the study's actual columns before writing a cohort filter. Filtering uses raw
characteristic names and values; harmonized columns are an additional representation.
Metadata scores describe completeness and consistency (see :doc:`data_contract`).

Literature and citations
-------------------------

.. code-block:: bash

   pyncbi pubmed 32788292 --methods --markdown
   pyncbi pubmed 32788292 --sections methods,results,data_availability --json
   pyncbi pubmed 32788292 27713574 --format bibtex -o refs.bib
   pyncbi search pubmed "fibromyalgia methylation" -n 20 -o refs.ris

PMC full text depends on availability and access. A linked paper does not guarantee
that open-access Methods can be retrieved.

Reads, sequences and genomes
----------------------------

.. code-block:: bash

   pyncbi sra GSE152418 -o runs.csv
   pyncbi fastq GSE152418 --markdown
   pyncbi fastq GSE152418 --download --runs SRR12007824 --max-download-mb 500 -o ./fastq/
   pyncbi fasta NM_007294.4 --start 1 --stop 300 -o brca1.fa
   pyncbi genome search "Escherichia coli" --json
   pyncbi genome download GCF_000005845.2 --include genome,gff3 --max-download-mb 500 -o ./ecoli/

FASTQ URLs come from ENA's mirror and include sizes and MD5 checksums when available.
Files without a reported checksum cannot be MD5-verified. Genome packages come from NCBI Datasets.

Variants, records and sequence similarity
-----------------------------------------

.. code-block:: bash

   pyncbi variant BRCA1 --significance pathogenic -n 10 --markdown
   pyncbi variant rs7412 --json
   pyncbi taxonomy human "Mus musculus" 511145
   pyncbi search biosample "SAMN15180526" --json
   pyncbi search bioproject "PRJNA639275" --markdown
   pyncbi link GSE85506
   pyncbi gds GDS1234 -o dataset.csv
   pyncbi blast NM_007294.4 --no-wait

BLAST submission returns a request ID. Collect it later with ``pyncbi blast --rid RID``.
Remote jobs can expire; preserve the ID when a local wait times out.
