"""GEO URL builders and series-relationship helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PyNCBI.core.config import get_config

if TYPE_CHECKING:
    from PyNCBI.core.types import GSEInfo

# NCBI-generated RNA-seq count tables exist only for these organisms/assemblies.
RNASEQ_ASSEMBLY_BY_ORGANISM: dict[str, str] = {
    "homo sapiens": "GRCh38.p13",
    "mus musculus": "GRCm39",
}


def subseries_of(gse_info: GSEInfo) -> list[str]:
    """SubSeries accessions listed on a SuperSeries record (`SuperSeries of: GSExxx`)."""
    out: list[str] = []
    for rel in gse_info.relations:
        if "superseries of:" in rel.lower():
            m = re.search(r"GSE\d+", rel)
            if m and m.group(0) not in out:
                out.append(m.group(0))
    return out


def resolve_rnaseq_assembly(gse_info: GSEInfo, default: str | None = None) -> str | None:
    """Resolve the NCBI RNA-seq count assembly (`GRCh38.p13` / `GRCm39`) from series organisms.

    Human wins over mouse for mixed series (NCBI generates the human table first).
    Returns `default` when the organism has no NCBI-generated count tables.
    """
    orgs = [o.lower() for o in gse_info.organisms]
    for organism, assembly in RNASEQ_ASSEMBLY_BY_ORGANISM.items():
        if any(organism in o for o in orgs):
            return assembly
    return default


def rnaseq_counts_url(
    gse_id: str, *, assembly: str = "GRCh38.p13", count_type: str = "raw_counts"
) -> str:
    """Build the GEO download CGI URL for an NCBI-generated RNA-seq count table."""
    cfg = get_config()
    fname = f"{gse_id}_{count_type}_{assembly}_NCBI.tsv.gz"
    return f"{cfg.NCBI_GEO_DOWNLOAD_URL}?type=rnaseq_counts&acc={gse_id}&format=file&file={fname}"
