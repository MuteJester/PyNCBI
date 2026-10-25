"""Sequence and read commands: sra, fasta, fastq, genome, blast."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PyNCBI.cli.console import _determine_mode, print  # noqa: A001
from PyNCBI.core.formatter import (
    format_table_preview,
    render_output,
    save_data,
)
from PyNCBI.core.tables import column, preview_records, project
from PyNCBI.domains.blast_search import BlastJob, blast, check_blast, fetch_blast
from PyNCBI.domains.entrez import (
    fetch_fasta,
    fetch_sra_runs,
)
from PyNCBI.domains.genomes import download_genome, fetch_assembly, search_assemblies
from PyNCBI.domains.sequencing import download_fastq, fetch_fastq_urls
from PyNCBI.io.http_client import format_size_human

if TYPE_CHECKING:
    import argparse


def _cmd_sra(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    query = " ".join(args.query).strip()
    df = fetch_sra_runs(query, limit=args.limit, save_to=args.output)
    saved_files = [str(Path(args.output).resolve())] if args.output and df["rows"] else []

    if mode == "json":
        payload = {
            "query": query,
            "run_count": len(df["rows"]),
            "columns": [str(c) for c in df["columns"]],
            "runs": preview_records(df, args.max_rows),
        }
        print(
            render_output(
                payload,
                mode="json",
                command="sra",
                target=query,
                summary=f"Found {len(df['rows'])} SRA sequencing run(s) for '{query}'",
                saved_files=saved_files,
            )
        )
        return 0

    display_cols = [
        c
        for c in (
            "Run",
            "Experiment",
            "SampleName",
            "LibraryStrategy",
            "LibraryLayout",
            "Model",
            "spots",
            "size_MB",
        )
        if c in df["columns"]
    ]
    view_df = project(df, display_cols) if display_cols else df
    header = (
        f"# SRA RunInfo: {query} ({len(df['rows'])} runs)\n"
        if mode == "markdown"
        else f"=== SRA RunInfo: {query} ({len(df['rows'])} runs) ===\n"
    )
    out = header + format_table_preview(view_df, mode=mode, max_rows=args.max_rows, max_cols=10)
    if saved_files:
        out += f"\n\nSaved full SRA RunInfo table ({len(df['rows'])} runs) to: {saved_files[0]}"
    print(out)
    return 0


def _cmd_fasta(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    records = fetch_fasta(
        args.accessions,
        db=args.db,
        seq_start=args.start,
        seq_stop=args.stop,
        save_to=args.output,
    )
    saved_files = [str(Path(args.output).resolve())] if args.output and records else []

    if mode == "json":
        print(
            render_output(
                [r.to_dict() for r in records],
                mode="json",
                command="fasta",
                target=",".join(args.accessions),
                summary=f"Fetched {len(records)} FASTA sequence(s) from '{args.db}'",
                saved_files=saved_files,
            )
        )
        return 0

    if mode == "markdown":
        lines = [f"# FASTA Sequences ({len(records)} record(s))"]
        for r in records:
            lines.append(f"\n## `{r.accession}` — {r.description} ({r.length:,} bp/aa)")
            preview_seq = r.sequence[:600] + ("..." if len(r.sequence) > 600 else "")
            lines.append(f"```fasta\n>{r.accession} {r.description}\n{preview_seq}\n```")
        if saved_files:
            lines.append(f"\n**Saved FASTA file to:** `{saved_files[0]}`")
        print("\n".join(lines))
        return 0

    # Text mode outputs standard FASTA directly to terminal
    sys.stdout.write("".join(r.fasta_text for r in records))
    if saved_files:
        sys.stderr.write(f"Saved FASTA to: {saved_files[0]}\n")
    return 0


def _cmd_fastq(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    acc = args.accession.strip()
    if args.download:
        out_dir = args.output or f"./{acc.upper()}_fastq"
        manifest = fetch_fastq_urls(acc)
        paths = download_fastq(
            manifest,
            out_dir,
            runs=args.runs,
            max_workers=args.workers,
            verify_md5=not args.no_verify,
            max_download_mb=args.max_download_mb,
            check_disk_space=not args.no_disk_check,
        )
        str_paths = [str(p) for p in paths]
        print(
            render_output(
                {"downloaded_files": str_paths, "file_count": len(str_paths)},
                mode=mode,
                command="fastq",
                target=acc,
                summary=f"Downloaded {len(str_paths)} FASTQ file(s) for {acc}",
                saved_files=str_paths,
            )
        )
        return 0
    df = fetch_fastq_urls(acc, save_to=args.output)
    saved = [str(Path(args.output).resolve())] if args.output and df["rows"] else []
    total = int(sum(value or 0 for value in column(df, "size_bytes"))) if df["rows"] else 0
    if mode == "json":
        payload = {
            "accession": acc,
            "file_count": len(df["rows"]),
            "run_count": int(len(set(column(df, "run_accession")))) if df["rows"] else 0,
            "total_size_bytes": total,
            "total_size_human": format_size_human(total),
            "columns": [str(c) for c in df["columns"]],
            "files": preview_records(df, args.max_rows),
        }
        print(
            render_output(
                payload,
                mode="json",
                command="fastq",
                target=acc,
                summary=f"{len(df['rows'])} FASTQ file(s) ({format_size_human(total)}) for {acc}",
                saved_files=saved,
                next_steps=[f"pyncbi fastq {acc} --download -o ./{acc}_fastq/"]
                if len(df["rows"])
                else [],
            )
        )
        return 0
    cols = [
        c
        for c in (
            "run_accession",
            "sample_alias",
            "file_name",
            "size_bytes",
            "library_layout",
            "library_strategy",
        )
        if c in df["columns"]
    ]
    header = (
        f"# FASTQ files: {acc} ({len(df['rows'])} files, {format_size_human(total)})\n"
        if mode == "markdown"
        else f"=== FASTQ files: {acc} ({len(df['rows'])} files, {format_size_human(total)}) ===\n"
    )
    out = header + format_table_preview(
        project(df, cols) if cols else df,
        mode=mode,
        max_rows=args.max_rows,
        max_cols=8,
    )
    if saved:
        out += f"\n\nSaved FASTQ manifest to: {saved[0]}"
    print(out)
    return 0


def _cmd_genome(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    if args.genome_action == "download":
        include = tuple(x.strip() for x in args.include.split(",") if x.strip())
        result = download_genome(
            args.target,
            args.output,
            include=include,
            unpack=not args.keep_zip,
            max_download_mb=args.max_download_mb,
            check_disk_space=not args.no_disk_check,
        )
        print(
            render_output(
                result,
                mode=mode,
                command="genome.download",
                target=args.target,
                summary=f"Downloaded {len(result.files)} file(s) for {args.target} into {result.output_dir}",
                saved_files=[str(p) for p in result.files],
            )
        )
        return 0
    target = args.target.strip()
    if target.upper().startswith(("GCF_", "GCA_")):
        records = [fetch_assembly(target)]
    else:
        records = search_assemblies(
            target, reference_only=not args.all_assemblies, limit=args.limit
        )
    saved_files: list[str] = []
    if args.output:
        saved_files.append(str(save_data([r.to_dict() for r in records], args.output)))
    if mode == "text":
        lines = [f"Found {len(records)} assembly record(s) for '{target}':\n"]
        for r in records:
            lines.append(
                f"* {r.accession} | {r.organism} {('(' + r.strain + ')') if r.strain else ''} | {r.assembly_name} | {r.assembly_level} | {r.refseq_category or r.source_db}"
            )
            lines.append(
                f"  length={r.total_length:,} bp | contig N50={r.contig_n50:,} | chromosomes={r.chromosome_count} | genes={r.gene_count:,} | released {r.release_date}"
            )
        if saved_files:
            lines.append(f"\nSaved to: {saved_files[0]}")
        print("\n".join(lines))
        return 0
    print(
        render_output(
            records,
            mode=mode,
            command="genome.search",
            target=target,
            summary=f"Found {len(records)} assembly record(s) for '{target}'",
            saved_files=saved_files,
            next_steps=[
                f"pyncbi genome download {records[0].accession} --include genome,gff3 -o ./{records[0].accession}/"
            ]
            if records
            else [],
        )
    )
    return 0


def _cmd_blast(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    if args.rid:
        job = check_blast(args.rid)
        if not job.is_ready:
            print(
                render_output(
                    job,
                    mode=mode,
                    command="blast",
                    target=job.rid,
                    summary=f"BLAST job {job.rid} status: {job.status}. {job.message}",
                    next_steps=[f"pyncbi blast --rid {job.rid}"],
                )
            )
            return 0
        result = fetch_blast(args.rid, save_to=args.output)
    else:
        if not args.query:
            raise ValueError(
                "Provide a sequence/accession/file to search, or --rid to fetch an existing job"
            )
        outcome = blast(
            args.query,
            program=args.program,
            database=args.database,
            megablast=not args.no_megablast,
            evalue=args.evalue,
            hitlist_size=args.hitlist_size,
            entrez_query=args.entrez_query,
            wait=not args.no_wait,
            poll_interval=args.poll_interval,
            timeout=args.timeout,
            save_to=args.output,
        )
        if isinstance(outcome, BlastJob):
            print(
                render_output(
                    outcome,
                    mode=mode,
                    command="blast",
                    target=outcome.rid,
                    summary=f"Submitted {outcome.program} job {outcome.rid}"
                    + (f" (~{outcome.estimated_seconds}s)" if outcome.estimated_seconds else ""),
                    next_steps=[f"pyncbi blast --rid {outcome.rid}"],
                )
            )
            return 0
        result = outcome

    saved = [str(result.saved_path)] if result.saved_path else []
    summary = f"{result.hit_count} hit(s) for {result.query_def or result.query_id or 'query'} in {result.database}"
    if mode == "json":
        print(
            render_output(
                result.to_dict(),
                mode="json",
                command="blast",
                target=result.rid,
                summary=summary,
                saved_files=saved,
            )
        )
        return 0
    body = result.to_markdown(max_hits=args.max_rows) if mode == "markdown" else result.to_text()
    if saved:
        body += f"\n\nSaved hit table to: {saved[0]}"
    print(body)
    return 0
