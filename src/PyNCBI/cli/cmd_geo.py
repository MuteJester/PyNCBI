"""GEO commands: inspect, samples, files, download, preview, gds, expression, gpl."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyNCBI.cli.console import _determine_mode, print  # noqa: A001
from PyNCBI.core.formatter import (
    format_dataframe_preview,
    render_output,
    save_data,
)
from PyNCBI.domains.annotation import fetch_gpl_table
from PyNCBI.domains.expression import load_expression
from PyNCBI.domains.geo import (
    download_geo_files,
    download_many,
    extract_gse_samples,
    inspect_accession,
    load_gds,
    plan_geo_download,
    preview_geo_file,
)
from PyNCBI.io.http_client import format_size_human
from PyNCBI.metadata import (
    harmonize_characteristics,
)

if TYPE_CHECKING:
    import argparse


def _cmd_inspect(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    report = inspect_accession(args.accession, check_file_sizes=not args.no_sizes)
    saved_files: list[str] = []
    if args.output:
        p = save_data(report.to_dict(), args.output)
        saved_files.append(str(p))

    print(
        render_output(
            report,
            mode=mode,
            command="inspect",
            target=report.accession,
            summary=f"{report.record_type} {report.accession}: {report.title}",
            saved_files=saved_files,
            next_steps=[
                f"pyncbi samples {report.accession} -o {report.accession}_samples.csv",
                f"pyncbi download {report.accession} -o ./{report.accession}/",
            ]
            if report.record_type == "GSE"
            else [f"pyncbi download {report.accession} -o ./{report.accession}/"],
        )
    )
    return 0


def _cmd_samples(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    combined_df, char_df, summaries = extract_gse_samples(
        args.gse_id,
        where=getattr(args, "where", None),
    )
    target_df = char_df if args.characteristics_only else combined_df
    harmonization: dict[str, Any] | None = None
    if getattr(args, "harmonize", False):
        table = harmonize_characteristics(char_df)
        harmonization = table.summary()
        if args.characteristics_only:
            target_df = table.normalized_df
        else:
            front = table.normalized_df
            rest = combined_df[[c for c in combined_df.columns if c not in char_df.columns]]
            target_df = front.join(rest, how="left")
    saved_files: list[str] = []

    if args.output and not target_df.empty:
        p = save_data(target_df, args.output)
        saved_files.append(str(p))

    if mode == "json":
        payload = {
            "gse_id": args.gse_id.upper(),
            "sample_count": len(target_df),
            "characteristics_summary": [cs.to_dict() for cs in summaries],
            "harmonization": harmonization,
            "columns": [str(c) for c in target_df.columns],
            "preview": target_df.iloc[: args.max_rows].reset_index().to_dict(orient="records"),
        }
        print(
            render_output(
                payload,
                mode="json",
                command="samples",
                target=args.gse_id.upper(),
                summary=f"Extracted metadata for {len(target_df)} samples in {args.gse_id.upper()}",
                saved_files=saved_files,
            )
        )
        return 0

    if mode == "markdown":
        lines = [
            f"# Sample Metadata & Characteristics: {args.gse_id.upper()} ({len(target_df)} samples)\n"
        ]
        if summaries:
            lines.append("## Characteristics Breakdown")
            for cs in summaries:
                lines.append(f"- **`{cs.name}`** ({cs.dtype}): {cs.format_brief()}")
            lines.append("")
        if not args.summary:
            lines.append("## Sample Table Preview")
            lines.append(
                format_dataframe_preview(target_df, mode="markdown", max_rows=args.max_rows)
            )
        if saved_files:
            lines.append(f"\n**Saved full sample table to:** `{saved_files[0]}`")
        print("\n".join(lines))
        return 0

    # Default text output
    lines = [f"=== {args.gse_id.upper()} Sample Metadata ({len(target_df)} samples) ==="]
    if summaries:
        lines.append("\nCharacteristics Breakdown:")
        for cs in summaries:
            lines.append(f"  • {cs.name}: {cs.format_brief()}")
    if not args.summary:
        lines.append(
            "\n" + format_dataframe_preview(target_df, mode="text", max_rows=args.max_rows)
        )
    if saved_files:
        lines.append(
            f"\nSaved full table ({len(target_df)} rows × {len(target_df.columns)} cols) to: {saved_files[0]}"
        )
    print("\n".join(lines))
    return 0


def _cmd_files(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    report = inspect_accession(
        args.accession,
        check_file_sizes=not args.no_sizes,
        include_sample_breakdown=False,
    )
    files_list = [f.to_dict() for f in report.files]
    saved_files: list[str] = []
    if args.output:
        p = save_data(files_list, args.output)
        saved_files.append(str(p))

    if mode == "text":
        lines = [f"Available files on NCBI for {report.accession} ({report.title}):\n"]
        if not report.files:
            lines.append("  (No downloadable files found)")
        for idx, f in enumerate(report.files):
            lines.append(f"  [{idx}] {f.name:40s} | {f.size_human:10s} | {f.source:18s} | {f.url}")
        if saved_files:
            lines.append(f"\nSaved file manifest to: {saved_files[0]}")
        print("\n".join(lines))
    else:
        print(
            render_output(
                files_list,
                mode=mode,
                command="files",
                target=report.accession,
                summary=f"Found {len(files_list)} downloadable file(s) for {report.accession}",
                saved_files=saved_files,
            )
        )
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    accessions = [a.upper() for a in args.accessions]
    where = getattr(args, "where", None)
    workers = int(getattr(args, "workers", 4) or 4)
    annotate = bool(getattr(args, "annotate", False))

    if getattr(args, "dry_run", False):
        plans = [
            plan_geo_download(
                acc,
                output_dir=args.output,
                source=args.source,
                file_index=args.file_index,
                pattern=args.pattern,
                where=where,
            )
            for acc in accessions
        ]
        if mode == "json":
            payload = (
                plans[0].to_dict()
                if len(plans) == 1
                else {
                    "plans": [p.to_dict() for p in plans],
                    "file_count": sum(p.file_count for p in plans),
                    "total_bytes": sum(p.total_bytes for p in plans),
                }
            )
            print(
                render_output(
                    payload,
                    mode="json",
                    command="download.dry_run",
                    target=",".join(accessions),
                    summary=f"Would download {sum(p.file_count for p in plans)} file(s), "
                    f"{format_size_human(sum(p.total_bytes for p in plans))} total (nothing was downloaded)",
                )
            )
            return 0
        print("\n\n".join(p.to_markdown() if mode == "markdown" else p.to_text() for p in plans))
        return 0

    if len(accessions) > 1:
        if where:
            raise ValueError("--where applies to a single GSE accession")
        per_acc = download_many(
            accessions,
            output_dir=args.output,
            source=args.source,
            file_index=args.file_index,
            pattern=args.pattern,
            unpack=args.unpack,
            max_workers=workers,
            annotate=annotate,
            expand_subseries=args.subseries,
            max_download_mb=args.max_download_mb,
            check_disk_space=not args.no_disk_check,
        )
        data = {
            "downloaded_files": {acc: [str(p) for p in paths] for acc, paths in per_acc.items()}
        }
        all_paths = [str(p) for paths in per_acc.values() for p in paths]
        print(
            render_output(
                data,
                mode=mode,
                command="download",
                target=",".join(accessions),
                summary=f"Downloaded {len(all_paths)} file(s) for {len(per_acc)} accession(s)",
                saved_files=all_paths,
            )
        )
        return 0

    accession = accessions[0]
    paths = download_geo_files(
        accession,
        output_dir=args.output,
        source=args.source,
        file_index=args.file_index,
        pattern=args.pattern,
        where=where,
        unpack=args.unpack,
        annotate=annotate,
        max_workers=workers,
        expand_subseries=bool(getattr(args, "subseries", False)),
        max_download_mb=args.max_download_mb,
        check_disk_space=not args.no_disk_check,
    )
    str_paths = [str(p) for p in paths]
    summary = f"Downloaded {len(str_paths)} file(s) for {accession}"
    if where:
        summary += f" (cohort filter: {where})"
    print(
        render_output(
            {"downloaded_files": str_paths},
            mode=mode,
            command="download",
            target=accession,
            summary=summary,
            saved_files=str_paths,
        )
    )
    return 0


def _cmd_preview(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    preview = preview_geo_file(
        args.accession,
        source=args.source,
        file_index=args.file_index,
        pattern=args.pattern,
        max_rows=args.rows,
    )
    saved_files: list[str] = []
    if args.output:
        p = save_data(preview.to_dict(), args.output)
        saved_files.append(str(p))

    print(
        render_output(
            preview,
            mode=mode,
            command="preview",
            target=preview.accession,
            summary=f"Preview of {preview.file_name} ({preview.total_columns} columns, index='{preview.index_name}')",
            saved_files=saved_files,
            next_steps=[
                f"pyncbi download {preview.accession} --source {args.source} -o ./{preview.accession}/"
            ],
        )
    )
    return 0


def _cmd_gpl(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    columns = [c.strip() for c in args.columns.split(",") if c.strip()] if args.columns else None
    df = fetch_gpl_table(
        args.gpl_id,
        columns=columns,
        limit=args.limit,
        save_to=args.output,
        source=args.source,
    )
    saved_files = [str(Path(args.output).resolve())] if args.output else []
    gpl = args.gpl_id.upper()
    if mode == "json":
        payload = {
            "gpl_id": gpl,
            "shape": [len(df), len(df.columns)],
            "index_name": df.index.name,
            "columns": [str(c) for c in df.columns],
            "preview": df.head(args.max_rows).reset_index().to_dict(orient="records"),
        }
        print(
            render_output(
                payload,
                mode="json",
                command="gpl",
                target=gpl,
                summary=f"{gpl} platform table: {len(df):,} probes x {len(df.columns)} columns",
                saved_files=saved_files,
            )
        )
        return 0
    header = (
        f"# Platform Table: {gpl}\n" if mode == "markdown" else f"=== Platform Table: {gpl} ===\n"
    )
    out = header + format_dataframe_preview(df, mode=mode, max_rows=args.max_rows, max_cols=8)
    if saved_files:
        out += f"\n\nSaved platform table ({len(df):,} probes) to: {saved_files[0]}"
    print(out)
    return 0


def _cmd_gds(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    res = load_gds(args.gds_id, full=args.full, save_to=args.output)
    saved = [str(Path(args.output).resolve())] if args.output else []
    gds = args.gds_id.upper()
    if mode == "json":
        payload = {
            **res.to_dict(),
            "sample_groups": res.sample_groups_df.reset_index().to_dict(orient="records"),
            "preview": res.table_df.head(args.max_rows).reset_index().to_dict(orient="records"),
        }
        print(
            render_output(
                payload,
                mode="json",
                command="gds",
                target=gds,
                summary=f"{gds}: {res.table_df.shape[0]:,} features x {max(0, res.table_df.shape[1] - 1)} samples, {len(res.subsets)} subset(s)",
                saved_files=saved,
            )
        )
        return 0
    head = (
        f"# GEO DataSet {gds}: {res.info.get('title', '')}\n"
        if mode == "markdown"
        else f"=== GEO DataSet {gds}: {res.info.get('title', '')} ===\n"
    )
    lines = [head]
    for sub in res.subsets:
        lines.append(f"- {sub['type']}: {sub['description']} ({len(sub['sample_ids'])} samples)")
    lines.append("")
    lines.append(
        format_dataframe_preview(res.table_df, mode=mode, max_rows=args.max_rows, max_cols=8)
    )
    if saved:
        lines.append(f"\nSaved GDS table to: {saved[0]}")
    print("\n".join(lines))
    return 0


def _cmd_expression(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    res = load_expression(
        args.gse_id,
        prefer=args.prefer,
        count_type=args.count_type,
        annotate=args.annotate,
        where=args.where,
        save_to=args.output,
        max_download_mb=args.max_download_mb,
        max_workers=args.workers,
    )
    saved = [str(res.saved_path)] if res.saved_path else []
    if res.saved_path:
        saved.append(
            str(Path(res.saved_path).with_name(f"{Path(res.saved_path).stem}_samples.csv"))
        )
    rows, cols = res.shape
    summary = f"{res.gse_id}: {rows:,} features x {cols:,} samples from '{res.source}'"
    if mode == "json":
        payload = res.to_dict()
        payload["preview"] = (
            res.matrix_df.iloc[: args.max_rows, :12].reset_index().to_dict(orient="records")
        )
        payload["samples_preview"] = (
            res.samples_df.iloc[: args.max_rows, :8].reset_index().to_dict(orient="records")
        )
        print(
            render_output(
                payload,
                mode="json",
                command="expression",
                target=res.gse_id,
                summary=summary,
                saved_files=saved,
                warnings=res.notes,
            )
        )
        return 0
    body = res.to_markdown(max_rows=args.max_rows) if mode == "markdown" else res.to_text()
    if saved:
        body += f"\n\nSaved matrix to: {saved[0]}"
    print(body)
    return 0
