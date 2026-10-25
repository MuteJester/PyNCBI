Migrating to 1.0
================

PyNCBI 1.0 broadens the package from methylation-specific GEO access to NCBI discovery,
metadata and raw/processed data retrieval. Review these changes before upgrading pipelines.

Imports
-------

Prefer the stable top-level API:

.. code-block:: python

   from PyNCBI import GSE, GSM, GEOReader, Config, PyNCBIError, NetworkError

Old module paths such as ``PyNCBI.GSE``, ``PyNCBI.GSM``, ``PyNCBI.GEOReader``,
``PyNCBI.config``, ``PyNCBI.exceptions``, ``PyNCBI.Constants``, ``PyNCBI.Utilities``,
``PyNCBI.FileUtilities`` and ``PyNCBI.services`` have been removed or reorganized.
Use top-level functions and classes; implementation modules now live under ``core``,
``io``, ``parsing``, ``domains``, ``metadata`` and ``geo``.

Methylation processing
----------------------

PyNCBI retrieves IDAT and other raw files. It no longer runs methylprep or promises a
methylation-specific beta matrix from an IDAT archive. Use a dedicated analysis tool for
IDAT processing. Array-specific constants and unsupported-array exceptions from older
versions are not part of the new public contract. ``GSE``, ``GSM`` and ``GEOReader`` remain
facades over the retrieval engine.

Caches and output
-----------------

The cache moved to typed, gzip-compressed JSON. Pickle ``.ch`` files are ignored and can be
removed with ``pyncbi cache clear``. Older JSON tables written with rounded values are
invalidated so that measurements can be fetched again.

JSON table exports include the feature/sample index as an explicit column. Missing and
non-finite numeric values are ``null``. Adjust consumers that previously inferred identifiers
from row order or accepted Python's nonstandard ``NaN`` tokens.

Downloads and errors
--------------------

The default transfer budget is 2048 MB per operation. Set ``PYNCBI_MAX_DOWNLOAD_MB`` or
pass ``max_download_mb=`` to change it; zero disables the ceiling. Parallel and nested
transfers share the operation limit. Read :doc:`configuration` for precise boundaries.

Operational errors derive from ``PyNCBIError``; invalid arguments can also raise ``ValueError``.
Use ``error_code_for`` and ``is_retryable`` for recovery decisions. CLI ``--json`` and MCP
results expose corresponding structured error fields. Parse the output schema rather than
matching human-readable text.

MCP HTTP clients must initialize a session and supply its ID on subsequent requests.
Terminated/unknown sessions return 404. HTTP POST accepts a single JSON-RPC message;
JSON-RPC batches are rejected.

GEO object loading and caches
-----------------------------

``GSE`` and ``GSM`` remain eager convenience objects, with the same constructor arguments
and public methods. They use shared typed metadata/table loaders; cache hydration performs
no network requests. ``GSM.data_status`` describes its loaded metadata snapshot without
making a fresh request. Construct with ``overwrite_cache=True`` to refresh it.

``GSE(mode="auto")`` uses the same source resolver as ``load_expression``. Explicit
``matrix``, ``rnaseq`` and ``supp`` modes stay on the requested source. Supplying a file
selection with AUTO selects that supplementary table. A selected raw archive is downloaded
with ``download_supplementary()``, not parsed as a measurement table. Raw-only GSMs retain
file-manifest DataFrames, but these manifests are never assembled as measurements.

Metadata and full data have separate object-cache entries. Full entries are keyed by mode,
file index and pattern, so one source cannot supply another source's measurements. Old
accession-only object snapshots are invalidated. Arbitrary custom file selectors bypass
full-data caching because their behavior cannot be inferred safely. ``clear_cache()``
removes metadata and registered data variants.

Record-local failures in PER_GSM mode appear in ``failed_samples`` and structured ``failures``
inside ``to_dict()``; incomplete results are not cached as complete data. Transport,
authentication, rate-limit and budget failures propagate. Objects do not add retries on
top of the HTTP client's retry policy. Accessions cannot be reassigned to loaded objects;
construct another object so data cannot be relabelled or written under the wrong accession.
Object reprs are concise and contain no terminal color escapes; detailed summaries remain
available through ``str()``, ``to_dict()`` and ``to_markdown()``.

On-page GSM measurements retain full binary64 precision instead of being downcast to
float32. Integer counts, including ``GSM[feature_id]`` lookups, retain integer precision.

GDS measurements now use the same correctly rounded parser as Series Matrix, GSM and
supplementary tables. Literal feature IDs such as ``NA`` remain identifiers. Filename
selection and format detection also recognize percent-encoded URL paths and GEO ``file=``
query parameters consistently across preview, planning and download.
Malformed non-numeric GDS measurements raise ``DataError`` instead of silently becoming
missing values.

Annotation caches now use the shared typed JSON cache under ``annotations/``. Older TSV
cache files are ignored and annotation is fetched again. Complete and partial gene
annotations are distinguished explicitly, regardless of the number of rows.

Saved study JSON and its canonical content hash use the same strict JSON semantics as
CLI/MCP output. Non-finite values become ``null`` and NumPy integers remain JSON numbers.
Records containing those values may have a different content hash from earlier development
snapshots; ordinary JSON-native records retain their hashes.
