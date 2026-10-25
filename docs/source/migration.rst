Migrating to 1.0
================

The 1.0 engine uses only the Python standard library. It retrieves NCBI records and
files and exposes native Python data; analysis libraries and additional formats are
chosen by callers.

Native table API
----------------

Table-returning functions now return ``{"columns": [...], "rows": [[...], ...]}``.
IDs are explicit columns, with ``feature_id`` for measurements and ``gsm_id`` for sample
metadata. Values are Python strings, integers, floats, booleans and ``None``. There is
no DataFrame/Series compatibility layer.

* ``ExpressionResult.matrix_df`` becomes ``matrix``; ``samples_df`` becomes ``sample_table``.
* ``SeriesMatrixResult`` exposes ``matrix``, ``sample_table``, ``characteristics_table``
  and a dictionary ``series_info``.
* ``GSE.samples_df`` and ``characteristics_df`` become ``sample_table`` and
  ``characteristics_table``. ``to_dataframe()`` becomes ``to_table()``; ``to_records()``
  provides row dictionaries. ``GSM.info`` and ``characteristics`` are dictionaries;
  ``GSM.data`` is a native table or ``None``.
* Study views use ``sample_table()``, ``harmonized_table()`` and ``provenance_table()``.
  BLAST uses ``hits_table()``; GDS exposes ``table``, ``sample_groups`` and
  ``expression_table()``.
* ``format_table_preview`` and ``summarize_characteristics`` replace the DataFrame-named
  helpers. Constructor arguments for HTTP connection pools are removed; urllib owns
  response handles and cookies, while NCBIClient owns retries and rate limits.

Use ``json.dumps(result.matrix)`` for JSON-compatible finite data, or ``save_data`` for
strict JSON exports. A caller using an independently installed DataFrame library can
construct a frame from the payload's ``rows`` and ``columns``. For large files, use
``iter_supplementary_records`` and close the generator on early termination.

Output and filters
------------------

Built-in exports are JSON, CSV and TSV. The former Parquet extra is removed; YAML,
Parquet and spreadsheet conversion belongs to callers. Raw downloads remain byte-preserving.
JSON table files now use the native columns/rows structure. CLI/MCP envelope schema ``2.0``
identifies this change; the MCP initialization protocol is unchanged.

Cohort filters support comparisons, numeric bounds, membership, ``and``/``or``/``not``,
``key=value``/``key:value`` matching and plain substrings. Quote multiword field names
with backticks. Arbitrary Python calls, attributes and pandas query methods are rejected.
Filtering preserves original values; numeric comparisons do not rewrite source metadata.

Precision and caching
---------------------

Python integer counts retain exact digits even beside missing cells. Missing measurements
are ``None`` and literal ``NA`` feature IDs remain strings. Floats retain binary64 precision;
GDS no longer uses a separate rounding converter. Non-numeric GDS measurements raise a
structured ``DataError`` instead of silently becoming missing data.

Old pickle, DataFrame/Series and annotation TSV caches are ignored. Native object caches
have a new version and distinguish source/file selections from metadata-only snapshots.
Annotation completeness is explicit. Saved study JSON and canonical hashes use strict
numeric/null semantics; hashes containing non-native development-snapshot values may change.

Imports and removed analysis
----------------------------

Prefer the stable package-level imports (``from PyNCBI import GSE, GSM, Session``).
Old paths such as ``PyNCBI.GSE``, ``PyNCBI.GSM``, ``PyNCBI.config`` and ``PyNCBI.services``
were reorganized into ``core``, ``io``, ``parsing``, ``domains``, ``metadata`` and ``geo``.
IDAT retrieval remains supported; methylprep processing and methylation-specific analysis
are left to dedicated caller tools.

Transfers and errors
--------------------

An operation shares its transfer limit across nested work and worker threads, including
metadata bodies. The default is 2048 MB; zero disables the ceiling. Explicit client defaults
and per-operation overrides apply consistently to preflight and actual I/O. Read
:doc:`configuration` for boundaries and :doc:`data_contract` for native data semantics.

Operational errors derive from ``PyNCBIError``; invalid arguments can raise ``ValueError``.
Use ``error_code_for`` and ``is_retryable`` rather than matching error text. Authentication,
rate-limit, transport and budget errors propagate; record-local failures remain explicit.
GSE/GSM do not add retries over the shared client policy. Loaded accessions cannot be
reassigned. HTTP MCP clients must initialize a session and supply its ID on later requests.
