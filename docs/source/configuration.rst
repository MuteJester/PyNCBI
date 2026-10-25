Configuration
=============

Credentials
-----------

NCBI searches work without an API key. Set ``NCBI_API_KEY`` for the higher E-Utilities
rate limit and ``NCBI_EMAIL`` for your contact address. ``PYNCBI_API_KEY`` and
``PYNCBI_EMAIL`` are supported aliases. Keys are redacted from PyNCBI logs and error URLs.
Never put real keys in issue reports, notebooks committed to Git, or MCP configuration examples.

Sessions
--------

.. code-block:: python

   import PyNCBI

   with PyNCBI.Session(cache_dir="./task-cache", max_download_mb=500) as session:
       result = session.load_expression("GSE152418")
       print(session.metrics())

Sessions own their configuration, client, caches and telemetry. Their generated typing
stubs expose the same signatures as the module-level functions. An explicit function budget
wins over the session default. The budget resets for each top-level operation.

Transfer limits
---------------

``PYNCBI_MAX_DOWNLOAD_MB`` sets the default budget (2048 MB). ``0`` disables it;
negative or non-finite values are rejected when starting a transfer. An explicit
``max_download_mb=`` or CLI ``--max-download-mb`` overrides the default where exposed.

A download operation shares its limit across nested calls and parallel workers, including
metadata bodies requested within that operation. Cached responses do not consume network
bytes. Both declared sizes and actual delivered chunks are checked. A missing HEAD size
cannot bypass the byte limit. A rejected transfer may already have read an in-flight
chunk, and concurrent requests may already be receiving data. The ceiling bounds accepted
response/file data; it is not an exact cap on network traffic or billing. Direct client
calls use a per-response default budget.

The count is bytes delivered by requests after HTTP content decoding, before file/archive
expansion. It is not a ceiling on RAM usage, uncompressed archive contents, exported CSV
size, or the total transferred by a session over its lifetime. Budget exhaustion can leave
completed files or a partial staging file; inspect the reported paths before retrying.

Raw file downloads check free disk space before and during writes. ``check_disk_space=False``
(or ``--no-disk-check`` where exposed) skips those checks. Archive expansion and table export
can require substantially more space than the download. Review ``pyncbi download --dry-run``
before large transfers; totals are incomplete when servers omit sizes.

Other environment variables
---------------------------

.. list-table::
   :header-rows: 1

   * - Variable
     - Meaning
   * - ``PYNCBI_CACHE_DIR`` / ``PYNCBI_CACHE_FOLDER``
     - Cache root (default ``~/.cache/PyNCBI``; respects ``XDG_CACHE_HOME``)
   * - ``PYNCBI_REQUEST_TIMEOUT``
     - Request timeout in seconds (default 30)
   * - ``PYNCBI_HTTP_CACHE_TTL``
     - In-memory metadata response lifetime in seconds (default 300; 0 disables)
   * - ``PYNCBI_AUDIT_LOG``
     - Append-only JSONL request audit path
   * - ``PYNCBI_AGENT_MODE``
     - Suppress colors and progress (``1`` enables, ``0`` disables)
   * - ``NO_COLOR``
     - Disable terminal colors

Cache directories are created when used, not on package import or ``--help`` / ``--version``.
