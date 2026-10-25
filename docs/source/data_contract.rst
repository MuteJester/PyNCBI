Data and output contracts
=========================

Measurements and identifiers
-----------------------------

Raw file downloads retain the bytes returned by the server. Parsed measurements use
pandas numeric types. Floating-point values are represented as binary64, not arbitrary
precision decimals; parsing, caching and JSON export retain that representation without
additional decimal rounding. Counts stored in integer columns retain integer precision.
A mixed numeric column containing missing or fractional values may require a floating-point
dtype; values outside that dtype's exact range are not arbitrary precision integers.

Feature identifiers are read as text, including leading zeros and numeric-looking IDs.
Literal identifiers such as ``NA`` and ``null`` remain text. Missing-value markers
(``NA``, ``N/A``, ``NaN``, ``null``, ``None`` and empty cells) apply to measurement
columns; GDS annotation columns retain their original text.
PyNCBI does not normalize or impute measurements. Optional annotation adds columns;
metadata harmonization affects descriptive sample fields, with the raw values preserved.

JSON files and previews
-----------------------

``-o matrix.json`` writes an array of records with the index included as a column
(e.g. ``probe`` or ``GeneID``). Unnamed indices use ``index``. If that name is already
a data column, underscores are prepended until the index column has a unique name.

Finite numbers retain Python's float precision. Missing values and non-finite floats
(``NaN``, positive or negative infinity) are JSON ``null``. Public JSON contains no
nonstandard numeric tokens. Preserve distinctions between missing-value types or infinity
in the in-memory result or an appropriate binary table format when those distinctions matter.

CLI ``--json`` returns an envelope with version, command, data, saved files and timing.
Terminal previews and agent payloads may be bounded; use ``-o`` for complete results.
Envelope schema versions and study-record schema versions are separate contracts.

Cache compatibility
-------------------

The local cache is gzip-compressed, typed JSON, never pickle. It retains numeric values,
column dtypes and row labels. Older table-cache entries created with decimal rounding are
cache misses and are replaced from the source when requested. Legacy ``.ch`` files are
ignored; ``pyncbi cache clear`` removes them.

Metadata completeness
---------------------

The 0–100 quality field measures metadata completeness and consistency using documented
issues such as absent samples, organism, platform or characteristics. It is a heuristic
for screening records, not a measure of experimental design, biological validity, statistical
power, publication quality, or suitability for a particular analysis. Inspect the individual
issues and raw metadata before selecting a study.
