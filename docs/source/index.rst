PyNCBI documentation
====================

**PyNCBI** is a programmatic, AI-agent-ready Python engine, CLI (``pyncbi``) and Model Context
Protocol server (``pyncbi mcp``) for NCBI: GEO (Series, Samples, Platforms, DataSets), Entrez
search with pagination, PubMed abstracts and PMC full-text Methods, Gene and platform annotation,
SRA runs and FASTQ files, BioSample / BioProject / Taxonomy records, ClinVar and dbSNP variants,
and genome assemblies via NCBI Datasets — all with only ``pandas``, ``numpy``, ``requests``,
``tqdm`` and ``termcolor`` as dependencies.

.. toctree::
   :maxdepth: 2
   :caption: Guides

   quickstart
   workflows
   data_contract
   configuration
   migration
   releasing
   agents
   cli

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   PyNCBI

Installation
------------

.. code-block:: bash

   pip install PyNCBI

Optional: set ``NCBI_API_KEY`` for 10 requests/second (default 3) and ``PYNCBI_AGENT_MODE=1``
for machine-oriented output (no progress bars or colors).

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
