"""Search and linking commands: search, pubmed, gene, link, taxonomy."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PyNCBI.cli.console import _determine_mode, print  # noqa: A001
from PyNCBI.core.formatter import (
    render_output,
    save_data,
)
from PyNCBI.domains.citations import export_citations
from PyNCBI.domains.entrez import (
    fetch_pubmed,
    fetch_taxonomy,
    link_accession,
    search_gene,
    search_geo,
    search_ncbi,
    search_pubmed,
    search_taxonomy,
)
from PyNCBI.domains.literature import METHODS_SECTIONS, attach_full_text

if TYPE_CHECKING:
    import argparse


def _cmd_search(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    db = args.db.lower().strip()
    query = " ".join(args.query).strip()
    saved_files: list[str] = []
    if getattr(args, "all", False):
        args.limit = None

    if db in ("geo", "gds", "gse"):
        hits = search_geo(
            query,
            organism=args.organism,
            entry_type=args.entry_type,
            dataset_type=args.type,
            min_samples=args.min_samples,
            limit=args.limit,
        )
        if args.output:
            p = save_data([h.to_dict() for h in hits], args.output)
            saved_files.append(str(p))

        if mode == "text":
            lines = [f"Found {len(hits)} GEO record(s) for query: '{query}'\n"]
            for h in hits:
                lines.append(
                    f"• {h.accession} ({h.sample_count} samples | {h.organism} | {', '.join(h.platforms) or 'N/A'})"
                )
                lines.append(f"  Title: {h.title}")
                if h.dataset_type:
                    lines.append(f"  Type:  {h.dataset_type}")
                if h.supp_file_types:
                    lines.append(f"  Files: {h.supp_file_types}")
                lines.append("")
            if saved_files:
                lines.append(f"Saved results to: {saved_files[0]}")
            print("\n".join(lines))
        else:
            print(
                render_output(
                    hits,
                    mode=mode,
                    command="search.geo",
                    target=query,
                    summary=f"Found {len(hits)} GEO record(s) matching '{query}'",
                    saved_files=saved_files,
                    next_steps=[f"pyncbi inspect {hits[0].accession}"] if hits else [],
                )
            )
        return 0

    if db == "pubmed":
        articles = search_pubmed(query, limit=args.limit)
        if args.output and str(args.output).lower().endswith((".bib", ".ris")):
            saved_files.append(str(export_citations(articles, args.output)))
        elif args.output:
            p = save_data([a.to_dict() for a in articles], args.output)
            saved_files.append(str(p))

        if mode == "text":
            lines = [f"Found {len(articles)} PubMed article(s) for '{query}':\n"]
            for a in articles:
                authors_str = ", ".join(a.authors[:4]) + (" et al." if len(a.authors) > 4 else "")
                lines.append(f"• PMID:{a.pmid} | {a.pub_date} | {a.journal}")
                lines.append(f"  Title:   {a.title}")
                if authors_str:
                    lines.append(f"  Authors: {authors_str}")
                if a.doi:
                    lines.append(f"  DOI:     {a.doi}")
                lines.append("")
            if saved_files:
                lines.append(f"Saved results to: {saved_files[0]}")
            print("\n".join(lines))
        else:
            print(
                render_output(
                    articles,
                    mode=mode,
                    command="search.pubmed",
                    target=query,
                    summary=f"Found {len(articles)} PubMed article(s) matching '{query}'",
                    saved_files=saved_files,
                )
            )
        return 0

    if db == "gene":
        genes = search_gene(query, organism=args.organism, limit=args.limit)
        if args.output:
            p = save_data([g.to_dict() for g in genes], args.output)
            saved_files.append(str(p))

        if mode == "text":
            lines = [f"Found {len(genes)} NCBI Gene record(s) for '{query}':\n"]
            for g in genes:
                lines.append(
                    f"• {g.symbol} (GeneID: {g.gene_id}) — {g.description} [{g.organism}, chr{g.chromosome} {g.map_location}]"
                )
                if g.aliases:
                    lines.append(f"  Aliases: {', '.join(g.aliases)}")
                if g.summary:
                    lines.append(
                        f"  Summary: {g.summary[:240]}{'...' if len(g.summary) > 240 else ''}"
                    )
                lines.append("")
            if saved_files:
                lines.append(f"Saved results to: {saved_files[0]}")
            print("\n".join(lines))
        else:
            print(
                render_output(
                    genes,
                    mode=mode,
                    command="search.gene",
                    target=query,
                    summary=f"Found {len(genes)} NCBI Gene record(s) matching '{query}'",
                    saved_files=saved_files,
                )
            )
        return 0

    # Generic NCBI database search
    docs = search_ncbi(db, query, limit=args.limit)
    if args.output:
        p = save_data(docs, args.output)
        saved_files.append(str(p))

    print(
        render_output(
            docs,
            mode=mode,
            command=f"search.{db}",
            target=query,
            summary=f"Found {len(docs)} record(s) in NCBI '{db}' matching '{query}'",
            saved_files=saved_files,
        )
    )
    return 0


def _pubmed_sections(args: argparse.Namespace) -> list[str] | None:
    """Translate --methods / --fulltext / --sections into a PMC section filter (None = skip)."""
    if getattr(args, "sections", None):
        return [s.strip() for s in str(args.sections).split(",") if s.strip()]
    if getattr(args, "fulltext", False):
        return ["all"]
    if getattr(args, "methods", False):
        return list(METHODS_SECTIONS)
    return None


def _cmd_pubmed(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    articles = fetch_pubmed(args.pmids)
    sections = _pubmed_sections(args)
    if sections is not None:
        attach_full_text(articles, sections=None if sections == ["all"] else sections)
    saved_files: list[str] = []
    cite_fmt = getattr(args, "format", None)
    if args.output and (cite_fmt or str(args.output).lower().endswith((".bib", ".ris"))):
        saved_files.append(str(export_citations(articles, args.output, fmt=cite_fmt)))
    elif args.output:
        p = save_data([a.to_dict() for a in articles], args.output)
        saved_files.append(str(p))
    if cite_fmt and mode == "text":
        print(str(export_citations(articles, None, fmt=cite_fmt)))
        if saved_files:
            sys.stderr.write(f"Saved to: {saved_files[0]}\n")
        return 0

    if mode == "text":
        lines = []
        for a in articles:
            lines.append(f"=== PMID: {a.pmid} ({a.pub_date}) ===")
            lines.append(f"Title:   {a.title}")
            lines.append(f"Journal: {a.journal}")
            if a.authors:
                lines.append(f"Authors: {', '.join(a.authors)}")
            if a.doi:
                lines.append(f"DOI:     {a.doi}")
            if a.abstract:
                lines.append(f"\nAbstract:\n{a.abstract}")
            if a.full_text is not None:
                lines.append("")
                if a.full_text.is_open_access:
                    for sec in a.full_text.selected_sections():
                        lines.append(f"[{sec.kind}] {sec.path or sec.title}")
                        if sec.text:
                            lines.append(sec.text)
                        lines.append("")
                else:
                    lines.append(f"Full text: {a.full_text.notes}")
            lines.append("\n" + "-" * 60)
        if saved_files:
            lines.append(f"Saved to: {saved_files[0]}")
        print("\n".join(lines))
    elif mode == "markdown":
        lines = []
        for a in articles:
            lines.append(f"# {a.title} (PMID: {a.pmid})")
            lines.append(f"- **Journal:** {a.journal} ({a.pub_date})")
            if a.authors:
                lines.append(f"- **Authors:** {', '.join(a.authors)}")
            if a.doi:
                lines.append(f"- **DOI:** `{a.doi}`")
            if a.abstract:
                lines.append(f"\n## Abstract\n{a.abstract}\n")
            if a.full_text is not None:
                if a.full_text.is_open_access:
                    for sec in a.full_text.selected_sections():
                        lines.append(
                            f"{'#' * min(6, sec.level + 1)} {sec.title or sec.kind.title()}"
                        )
                        if sec.text:
                            lines.append(sec.text + "\n")
                else:
                    lines.append(f"> Full text: {a.full_text.notes}\n")
        print("\n".join(lines))
    else:
        print(
            render_output(
                articles,
                mode="json",
                command="pubmed",
                target=",".join(args.pmids),
                summary=f"Fetched {len(articles)} PubMed article(s)",
                saved_files=saved_files,
            )
        )
    return 0


def _cmd_gene(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    all_records = []
    for q in args.queries:
        all_records.extend(search_gene(q, organism=args.organism, limit=args.limit))

    saved_files: list[str] = []
    if args.output:
        p = save_data([g.to_dict() for g in all_records], args.output)
        saved_files.append(str(p))

    if mode == "text":
        lines = []
        for g in all_records:
            lines.append(f"=== {g.symbol} (GeneID: {g.gene_id}) ===")
            lines.append(f"Description: {g.description}")
            lines.append(f"Organism:    {g.organism}")
            lines.append(f"Location:    chr{g.chromosome} ({g.map_location})")
            if g.aliases:
                lines.append(f"Aliases:     {', '.join(g.aliases)}")
            if g.summary:
                lines.append(f"\nSummary:\n  {g.summary}")
            lines.append("")
        if saved_files:
            lines.append(f"Saved to: {saved_files[0]}")
        print("\n".join(lines))
    else:
        print(
            render_output(
                all_records,
                mode=mode,
                command="gene",
                target=",".join(args.queries),
                summary=f"Retrieved {len(all_records)} NCBI Gene record(s)",
                saved_files=saved_files,
            )
        )
    return 0


def _cmd_link(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    links = link_accession(args.accession)
    saved_files: list[str] = []
    if args.output:
        p = save_data(links, args.output)
        saved_files.append(str(p))

    print(
        render_output(
            links,
            mode=mode,
            command="link",
            target=args.accession.upper(),
            summary=f"Cross-database links for {args.accession.upper()}",
            saved_files=saved_files,
        )
    )
    return 0


def _cmd_taxonomy(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    records = []
    for q in args.queries:
        if q.strip().isdigit():
            records.extend(fetch_taxonomy([q]))
        else:
            records.extend(search_taxonomy(q, limit=args.limit))
    saved_files: list[str] = []
    if args.output:
        saved_files.append(str(save_data([r.to_dict() for r in records], args.output)))
    if mode == "text":
        lines = []
        for r in records:
            lines.append(f"=== {r.scientific_name} (TaxID {r.taxid}) ===")
            if r.common_name:
                lines.append(f"Common name: {r.common_name}")
            lines.append(
                f"Rank:        {r.rank} | Division: {r.division} | Genetic code: {r.genetic_code or '?'}"
            )
            if r.lineage:
                lines.append(f"Lineage:     {' > '.join(r.lineage)}")
            lines.append("")
        if saved_files:
            lines.append(f"Saved to: {saved_files[0]}")
        print("\n".join(lines))
        return 0
    print(
        render_output(
            records,
            mode=mode,
            command="taxonomy",
            target=",".join(args.queries),
            summary=f"Retrieved {len(records)} Taxonomy record(s)",
            saved_files=saved_files,
        )
    )
    return 0
