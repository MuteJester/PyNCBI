Using PyNCBI from AI agents
===========================

PyNCBI is designed so an agent never has to scrape NCBI or write E-utilities code.

MCP server
----------

Installed on the host, with no install at all (``uvx``), or fully isolated (Docker):

.. code-block:: json

   {
     "mcpServers": {
       "pyncbi": {"command": "pyncbi", "args": ["mcp"], "env": {"NCBI_API_KEY": "..."}},
       "pyncbi-uvx": {"command": "uvx", "args": ["pyncbi", "mcp"]},
       "pyncbi-docker": {"command": "docker", "args": ["run", "--rm", "-i", "pyncbi", "mcp"]}
     }
   }

Twenty tools: ``ncbi_search``, ``ncbi_screen``, ``ncbi_inspect``, ``ncbi_metadata``, ``ncbi_samples``,
``ncbi_preview``, ``ncbi_expression``, ``ncbi_download``, ``ncbi_bundle``, ``ncbi_pubmed`` (with PMC
Methods), ``ncbi_gene``, ``ncbi_link``, ``ncbi_sra``, ``ncbi_fastq``, ``ncbi_fasta``, ``ncbi_variant``,
``ncbi_genome``, ``ncbi_taxonomy``, ``ncbi_blast``, ``ncbi_status``. Errors come back as ``isError`` results with
``error_code``, ``hint`` and ``retryable``.

A typical agent path is ``ncbi_screen`` (compare candidates) → ``ncbi_metadata`` (harmonized cohort) →
``ncbi_expression`` (the matrix) → ``ncbi_download``/``ncbi_fastq`` (raw files, ``dry_run`` first).

Transports
----------

``pyncbi mcp`` speaks stdio. ``pyncbi mcp --http --host 127.0.0.1 --port 8765`` serves the Streamable HTTP
transport: single-message JSON-RPC over ``POST /mcp``, an ``Mcp-Session-Id`` on initialize,
``DELETE /mcp`` to end a session, and loopback-only ``Origin`` checks. Binding to a non-loopback address
prints a warning; put your own authentication in front of it.

Resources
---------

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - URI
     - Contents
   * - ``pyncbi://schema/study``, ``pyncbi://schema/sample``
     - JSON Schema of the record contracts
   * - ``pyncbi://vocabulary``
     - Canonical fields, synonyms and value normalizers
   * - ``pyncbi://study/{gse_id}/overview``
     - Markdown overview (design, cohort, harmonization, quality)
   * - ``pyncbi://study/{gse_id}/samples``
     - Harmonized sample sheet as CSV
   * - ``pyncbi://study/{gse_id}/record``
     - The full versioned StudyRecord as JSON
   * - ``pyncbi://platform/{gpl_id}/table``
     - First rows of a GPL annotation table as CSV

Prompts
-------

``find_dataset`` (topic, organism, needs), ``study_deep_dive`` (gse_id), ``cohort_download``
(gse_id, filter, pattern) and ``paper_methods`` (pmid) each expand into a short plan naming the exact
tools to call in order, including ``dry_run`` before any large download.

JSON envelope
-------------

``pyncbi <command> --json`` prints one JSON object:

.. code-block:: json

   {"ok": true, "schema_version": "1.1", "pyncbi_version": "1.0.0", "command": "inspect",
    "target": "GSE152418", "summary": "...", "data": {}, "saved_files": [], "next_steps": [],
    "warnings": [], "elapsed_ms": 812.4, "request_count": 7}

Agent skill
-----------

``pyncbi skill install --target claude|antigravity|cursor`` installs ``SKILL.md`` (the
recommended Search -> Inspect -> Download workflow, cohort filtering, annotation, Methods
extraction, bundles, FASTQ, variants). ``PYNCBI_AGENT_MODE=1`` silences progress bars and
colors; ``NO_COLOR`` is honoured.

Recommended workflow
--------------------

1. ``ncbi_search`` / ``pyncbi search geo`` to find candidate series.
2. ``ncbi_bundle`` / ``pyncbi bundle`` to get the full picture of one study in one call.
3. ``ncbi_download`` with ``where`` for exactly the samples and files needed.
4. ``ncbi_pubmed`` with ``methods=true`` to read how the authors processed the data.

Metadata contract
-----------------

``ncbi_metadata`` / ``pyncbi metadata --json`` return a ``StudyRecord`` (``schema_version`` 1.0; JSON Schema
from ``pyncbi schema study``). Each sample carries ``characteristics`` (raw) and ``harmonized`` values:

.. code-block:: json

   {"sex": {"value": "female", "raw": "F", "unit": null, "extra": {},
            "provenance": {"source": "geo:characteristics_ch1", "source_key": "gender",
                           "rule": "sex.synonym", "confidence": "exact"}}}

Confidence levels: ``exact`` (controlled value), ``inferred`` (e.g. range midpoint), ``verbatim`` (kept as
given), ``unmapped`` (key not in vocabulary; served as ``raw:<key>``), ``missing``. Records include a
``quality`` block (0-100 score, typed issues) and ``harmonization`` summary. Samples are paginated with
``page_size`` / ``page_token`` and large payloads are bounded (``_truncated`` marks what was cut).

Sessions and request reuse
--------------------------

Give each agent or task its own :class:`PyNCBI.Session`. A session owns its API key, cache
directory, download budget, rate limiters, metadata store and telemetry, so two workflows in one
process never interfere:

.. code-block:: python

   with PyNCBI.Session( cache_dir="./agent-1", max_download_mb=500) as s:
       record = s.build_study_record("GSE152418")
       matrix = s.load_expression("GSE152418")

Identical metadata responses are reused for ``PYNCBI_HTTP_CACHE_TTL`` seconds (default 300). On a
three-step study workflow that is 4 NCBI requests instead of 9. ``session.metrics()`` reports
``cache_hits`` so an agent can see what it saved. Job polling, file bodies, byte ranges and streams
are never cached.

Rails for unattended runs
-------------------------

Set these before handing PyNCBI to an autonomous agent:

.. code-block:: bash

   export PYNCBI_MAX_DOWNLOAD_MB=500      # default budget per operation
   export PYNCBI_AUDIT_LOG=~/pyncbi.jsonl # append one JSON line per NCBI request (keys redacted)
   export PYNCBI_AGENT_MODE=1             # no progress bars or colors

Raw file downloads check free disk space before and during writes, and ``ncbi_download`` accepts ``dry_run: true`` (or
``pyncbi download --dry-run``) to report the file list, per-file sizes, total and free space without
transferring anything. ``ncbi_screen`` lets an agent compare many candidate studies before committing to
a download at all.

HTTP lifecycle
--------------

Initialize before calling tools. Send the returned ``Mcp-Session-Id`` on every subsequent
request and the negotiated ``MCP-Protocol-Version`` when supported. Missing IDs return 400;
unknown, expired or deleted sessions return 404 and require initialization again. DELETE
ends a session. Origin validation applies to POST, GET and DELETE. Each POST carries one
message; batches are rejected. The server offers JSON responses and returns 405 for GET
SSE streams. See :doc:`configuration` for byte-budget boundaries and :doc:`data_contract`
for strict JSON and completeness-score semantics.
