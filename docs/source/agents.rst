Using PyNCBI from AI agents
===========================

PyNCBI is designed so an agent never has to scrape NCBI or write E-utilities code.

Install and connect
-------------------

Publishing or installing a Python package does not register it with an agent. Connect
PyNCBI once, then install its workflow skill if you want reusable retrieval guidance.
An NCBI API key is optional; no model-provider API key is required by PyNCBI.

After installing ``PyNCBI`` into the environment used by the agent:

.. code-block:: bash

   # Codex
   codex mcp add pyncbi -- pyncbi mcp
   pyncbi skill install --target codex
   codex mcp list

   # Claude Code (available across projects)
   claude mcp add --transport stdio --scope user pyncbi -- pyncbi mcp
   pyncbi skill install --target claude
   claude mcp list

The ``pyncbi`` executable must be on the MCP client's PATH. If the package lives in a
virtual environment, use the absolute path to its executable as the server command.
For an isolated launcher after 1.0.0 is published, substitute
``uvx --from PyNCBI==1.0.0 pyncbi mcp`` for ``pyncbi mcp``. ``uvx`` installs the package
into a managed environment; it is optional and is not part of PyNCBI's runtime.

The Codex skill goes to ``~/.agents/skills/pyncbi/``; Claude uses
``~/.claude/skills/pyncbi/``. ``--target cursor`` installs a project skill under
``.cursor/skills/pyncbi/``. ``--target antigravity`` remains available.
The default ``--target all`` installs the three user-scoped targets (Codex, Claude,
Antigravity), while Cursor requires an explicit project-local target.
Use ``--dir ./.agents/skills/pyncbi`` for a repository-scoped Codex skill.
Installers include supporting references; copying only ``SKILL.md`` is insufficient.
Existing legacy Cursor ``.cursor/rules/pyncbi.mdc`` files can be removed after switching
to the skill, to avoid loading the same instructions twice.

For clients accepting an ``mcpServers`` JSON configuration:

.. code-block:: json

   {
     "mcpServers": {
       "pyncbi": {
         "command": "pyncbi",
         "args": ["mcp"],
         "env": {"PYNCBI_AGENT_MODE": "1", "PYNCBI_MAX_DOWNLOAD_MB": "500"}
       }
     }
   }

Use one launch method per client. For Docker, first build the local image with
``docker build -t pyncbi .``, then use ``docker run --rm -i pyncbi mcp`` as the command.

The skill selects connected MCP tools first, and uses CLI/Python for local automation
or streaming. It does not require installing the CLI merely to use a connected server.
Automatic selection depends on the agent, its configuration and the user's task;
PyPI publication alone does not guarantee recommendations or usage.

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

   {"ok": true, "schema_version": "2.0", "pyncbi_version": "1.0.0", "command": "inspect",
    "target": "GSE152418", "summary": "...", "data": {}, "saved_files": [], "next_steps": [],
    "warnings": [], "elapsed_ms": 812.4, "request_count": 7}

Agent skill
-----------

``pyncbi skill show`` prints the concise entry point. The skill retains native data,
scientific provenance and error-recovery guidance, with detailed workflows loaded from
``references/workflows.md`` only when needed. ``PYNCBI_AGENT_MODE=1`` silences progress
and colors; ``NO_COLOR`` is honoured. The server exposes structured input/output schemas
and rejects unknown arguments, missing required fields and wrong types before execution.
For example, use a JSON boolean for ``dry_run``; ``"false"`` is a string and is rejected.

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

Identical metadata responses within the same client are reused for ``PYNCBI_HTTP_CACHE_TTL``
seconds (default 300). ``session.metrics()`` reports
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

Long operations and output files
--------------------------------

Client call timeouts and PyNCBI's per-request HTTP timeout are separate settings. A large
matrix or raw-file transfer can exceed a client's call timeout despite healthy HTTP I/O.
For Codex, add a suitable call timeout to ``~/.codex/config.toml`` (or project configuration):

.. code-block:: toml

   [mcp_servers.pyncbi]
   command = "pyncbi"
   args = ["mcp"]
   tool_timeout_sec = 600
   env_vars = ["NCBI_API_KEY", "NCBI_EMAIL"]

   [mcp_servers.pyncbi.env]
   PYNCBI_AGENT_MODE = "1"
   PYNCBI_MAX_DOWNLOAD_MB = "500"

For Claude Code, a per-server ``"timeout": 600000`` in its MCP configuration sets a
ten-minute limit in milliseconds. Choose a value appropriate to the task, rather than
retrying timed-out transfers repeatedly. For BLAST, prefer ``wait=false`` and RID polling.

PyNCBI currently runs tool calls synchronously. Client cancellation or a timeout does not
guarantee that server-side work stopped. Inspect output and partial files before retrying.
``saved_files`` paths belong to the server's filesystem: remote HTTP servers do not
automatically transfer those artifacts to the client. Run the server alongside the agent
or arrange access to its output directory. Keep full matrices in files and use the bounded
response to check source, sample alignment and dimensions.

Validation and discovery
------------------------

``python scripts/check_mcp_client.py`` runs deterministic research workflows through the
independent MCP SDK over stdio and HTTP against a loopback NCBI fixture. It checks sample
pagination/filtering, exact matrix values and saved artifacts, download plans, invalid-input
recovery and response sizes. It requires the development ``protocol-test`` extra, not a
model-provider key or live NCBI access. CI also runs offline and scheduled live NCBI tests.

These checks establish protocol and workflow behavior; they do not measure how often a
particular model chooses PyNCBI. For agent acceptance testing, connect the server and skill
in a fresh client, ask the tasks in ``tests/fixtures/agent_tasks.json`` without naming a tool,
and inspect tool traces and saved artifacts against the acceptance criteria. Include the
non-NCBI task to check that the skill does not take over unrelated analysis. Record client,
model, result correctness, request count, elapsed time and output usage; do not infer speed
or token savings from passing protocol tests alone.

Public plugin or MCP-directory listings are optional distribution channels requiring
separate submission. They are not created by ``pip install`` or by the skill installer.

Setup follows the official `Codex MCP documentation <https://learn.chatgpt.com/docs/extend/mcp>`_,
`Codex skill locations <https://learn.chatgpt.com/docs/build-skills>`_,
`Claude Code MCP guide <https://code.claude.com/docs/en/mcp>`_, and
`Cursor skill guide <https://cursor.com/docs/skills>`_.
