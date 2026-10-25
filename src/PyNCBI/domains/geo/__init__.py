"""GEO info, inspection, and raw-data retrieval engine for PyNCBI.

Provides fast, programmatic, zero-post-processing access to NCBI GEO. The package is
split by responsibility and this module is the stable import surface for all of it:

- :mod:`~PyNCBI.domains.geo.urls` - URL builders, assembly resolution, SuperSeries relations
- :mod:`~PyNCBI.domains.geo.transfer` - gzip sanity checks, archive unpacking, parallel HTTP I/O
- :mod:`~PyNCBI.domains.geo.planning` - download plans and the pre-transfer safety rails
- :mod:`~PyNCBI.domains.geo.samples` - single-request sample metadata and cohort filtering
- :mod:`~PyNCBI.domains.geo.inspection` - study design, cohort summary, remote file sizes
- :mod:`~PyNCBI.domains.geo.tables` - Series Matrix, RNA-seq counts, GDS, supplementary tables
- :mod:`~PyNCBI.domains.geo.downloads` - raw file downloads and cohort file resolution
- :mod:`~PyNCBI.domains.geo.preview` - streaming previews without a full download

Key entry points:

- :func:`inspect_accession`: complete overview of any GSE, GSM, or GPL with file sizes
- :func:`extract_gse_samples`: O(1) single-request sample metadata & characteristics table
- :func:`load_series_matrix`: download and parse GEO Series Matrix (``.txt.gz``) files
- :func:`load_rnaseq_counts`: download and parse NCBI-generated RNA-seq count tables
- :func:`download_geo_files`: download raw supplementary, matrix, or table files from NCBI
"""

from __future__ import annotations

from PyNCBI.domains.geo.downloads import download_geo_files, download_many
from PyNCBI.domains.geo.inspection import (
    gse_of_gsm,
    inspect_accession,
    platform_of_gsm,
)
from PyNCBI.domains.geo.planning import (
    DownloadPlan,
    build_download_plan,
    plan_geo_download,
)
from PyNCBI.domains.geo.preview import preview_geo_file
from PyNCBI.domains.geo.resolve import CohortFile, resolve_cohort_files
from PyNCBI.domains.geo.samples import extract_gse_samples
from PyNCBI.domains.geo.tables import (
    load_gds,
    load_rnaseq_counts,
    load_series_matrix,
    read_supplementary_table,
)
from PyNCBI.domains.geo.transfer import decompress_table_bytes, download_urls_parallel
from PyNCBI.domains.geo.urls import (
    RNASEQ_ASSEMBLY_BY_ORGANISM,
    resolve_rnaseq_assembly,
    rnaseq_counts_url,
    subseries_of,
)

__all__ = [
    "RNASEQ_ASSEMBLY_BY_ORGANISM",
    "CohortFile",
    "DownloadPlan",
    "build_download_plan",
    "decompress_table_bytes",
    "download_geo_files",
    "download_many",
    "download_urls_parallel",
    "extract_gse_samples",
    "gse_of_gsm",
    "inspect_accession",
    "load_gds",
    "load_rnaseq_counts",
    "load_series_matrix",
    "plan_geo_download",
    "platform_of_gsm",
    "preview_geo_file",
    "read_supplementary_table",
    "resolve_cohort_files",
    "resolve_rnaseq_assembly",
    "rnaseq_counts_url",
    "subseries_of",
]
