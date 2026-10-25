Data and output contracts
=========================

Native data
-----------

PyNCBI has no third-party runtime dependencies. Metadata uses dictionaries, lists and
serializable domain records. Tables are ordinary Python dictionaries with two keys:

.. code-block:: python

   {"columns": ["feature_id", "GSM1"],
    "rows": [["0001234", 9007199254740993], ["NA", None]]}

Measurement tables expose their first column as ``feature_id``. Metadata tables use
explicit keys such as ``gsm_id``, ``gse_id``, ``gene_id``, or the published SRA ``Run``.
There is no hidden index or dtype state. ``iter_records(table)`` yields row dictionaries.
Callers can wrap these native values in whichever analysis library they choose.

Measurements and identifiers
-----------------------------

Raw downloads retain the hosted bytes. Parsed counts are Python integers, retaining
precision even when the same column also contains missing values. Decimal/exponent
measurements are Python floats (binary64), not arbitrary-precision decimal numbers.
Parsing, caching and export do not add further decimal rounding. Text values remain
strings; malformed non-numeric GDS measurements raise ``DataError``.

Identifiers remain text, including leading zeros and literal ``NA`` or ``null`` IDs.
Missing-value markers (``NA``, ``N/A``, ``NaN``, ``null``, ``None`` and empty cells)
apply to measurement cells, not identifiers or GDS annotation text. Missing cells are
``None``; no imputation or normalization is applied to measurements. Metadata harmonization
is an explicit descriptive operation that preserves raw values and provenance.

Streaming and materialization
-----------------------------

``load_expression`` and other full-table loaders materialize native rows in memory.
For large files, ``iter_supplementary_records(url_or_path)`` yields dictionaries lazily.
Exhaust the generator or close it when stopping early (for example with
``contextlib.closing``) to release its socket or file handle. The generator retains the
operation transfer budget throughout iteration. Native Python numbers can use more
memory than specialized array libraries; raw downloads and iterators let callers control
that cost without an implicit analysis dependency.

JSON, CSV and TSV
-----------------

JSON table exports retain the same ``columns``/``rows`` structure. CSV/TSV exports write
those explicit columns, including identifiers. Finite floats retain Python's precision;
missing/non-finite values are JSON ``null``. Python integer digits are preserved in JSON;
consumers must choose a number representation that can retain large integers.

YAML, Parquet, spreadsheets and DataFrame conversion belong to callers. PyNCBI does not
install, import or implement those libraries/formats. It can still download original
binary files without decoding them.

CLI/MCP envelopes use schema version ``2.0`` for native data, saved files and request
timing. Previews are bounded; save a result for complete data. Study-record schema versions
and the MCP protocol version are separate contracts.

Caches and metadata completeness
--------------------------------

Caches use gzip-compressed typed JSON, never pickle. They retain native identifiers,
integers, floats and missing values without dtype inference. Old DataFrame/Series caches
are invalidated and fetched again. Source/file request identities remain separate, and
partial results are never represented as a complete cache entry.

The 0–100 quality field measures metadata completeness and consistency. It is a heuristic
for screening records, not a measure of experimental design, biological validity,
statistical power or suitability for an analysis. Inspect issues and raw metadata.
