"""Study-level commands: variant, bundle, metadata, schema, screen."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from PyNCBI.cli.console import _determine_mode, print  # noqa: A001
from PyNCBI.core.formatter import (
    render_output,
    save_data,
)
from PyNCBI.domains.bundle import build_study_bundle
from PyNCBI.domains.screening import screen_studies
from PyNCBI.domains.variants import fetch_variants, format_variants_markdown
from PyNCBI.metadata import (
    build_study_record,
    json_schema,
    save_study_record,
    vocabulary,
)

if TYPE_CHECKING:
    import argparse


def _cmd_variant(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    query = " ".join(args.query).strip()
    source = "clinvar" if args.subcommand == "clinvar" else args.source
    result = fetch_variants(
        query,
        source=source,
        significance=args.significance,
        gene=args.gene,
        limit=args.limit,
    )
    saved_files: list[str] = []
    if args.output:
        rows = result["clinvar"] or result["dbsnp"]
        p = save_data(
            rows if str(args.output).endswith((".csv", ".tsv", ".parquet")) else result, args.output
        )
        saved_files.append(str(p))
    n_cv, n_snp = len(result["clinvar"]), len(result["dbsnp"])
    summary = (
        f"Found {n_cv} ClinVar variant(s)"
        + (f" and {n_snp} dbSNP record(s)" if n_snp else "")
        + f" for '{query}'"
    )
    if mode == "json":
        print(
            render_output(
                result,
                mode="json",
                command=args.subcommand,
                target=query,
                summary=summary,
                saved_files=saved_files,
            )
        )
        return 0
    body = format_variants_markdown(result)
    if mode == "text":
        body = body.replace("# ", "").replace("`", "")
    print(body + (f"\n\nSaved to: {saved_files[0]}" if saved_files else ""))
    return 0


def _cmd_bundle(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    bundle = build_study_bundle(
        args.gse_id,
        output_dir=args.output,
        include_matrix=args.matrix,
        include_sra=not args.no_sra,
        include_pubmed=not args.no_pubmed,
        include_methods=not args.no_methods,
        where=args.where,
        check_file_sizes=not args.no_sizes,
        expand_subseries=bool(getattr(args, "subseries", False)),
    )
    saved = [str(p) for p in bundle.files.values()]
    print(
        render_output(
            bundle,
            mode=mode,
            command="bundle",
            target=bundle.gse_id,
            summary=f"Study bundle for {bundle.gse_id} ({bundle.sample_count} samples) written to {bundle.output_dir}",
            saved_files=saved,
            warnings=bundle.warnings,
            next_steps=[f"cat {bundle.files['overview']}"],
        )
    )
    return 0


def _cmd_metadata(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    enrich = tuple(x.strip() for x in (args.enrich or "").split(",") if x.strip())
    record = build_study_record(
        args.gse_id,
        where=args.where,
        enrich=enrich,
        check_file_sizes=not args.no_sizes,
    )
    saved_files: list[str] = []
    if args.output:
        manifest = save_study_record(record, args.output)
        saved_files = [str(Path(args.output) / f["path"]) for f in manifest["files"]] + [
            str(Path(args.output) / "manifest.json")
        ]
    gse = record.gse_id
    quality = record.quality.score if record.quality else "n/a"
    summary = f"{gse}: {record.sample_count} samples, {len(record.harmonization.get('mapped_fields', []))} harmonized field(s), quality {quality}/100"
    if mode == "json":
        print(
            render_output(
                record.to_dict(include_samples=not args.no_samples),
                mode="json",
                command="metadata",
                target=gse,
                summary=summary,
                saved_files=saved_files,
                warnings=[
                    f"{i.severity}:{i.code}: {i.message}"
                    for i in (record.quality.issues if record.quality else [])
                ],
                next_steps=[f"pyncbi download {gse} --where \"<field> == '<value>'\" -o ./{gse}/"],
            )
        )
        return 0
    body = record.to_markdown(max_samples=args.max_rows) if mode == "markdown" else record.to_text()
    if saved_files:
        body += f"\n\nSaved study record layout to: {Path(args.output).resolve()}"
    print(body)
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    if args.name == "vocabulary":
        fields = {k: v.to_dict() for k, v in vocabulary.fields().items()}
        if mode == "json":
            print(
                render_output(
                    {"fields": fields},
                    mode="json",
                    command="schema",
                    target="vocabulary",
                    summary=f"{len(fields)} canonical sample fields",
                )
            )
            return 0
        lines = (
            ["| field | normalizer | description | synonyms |", "| :-- | :-- | :-- | :-- |"]
            if mode == "markdown"
            else []
        )
        for name, f in fields.items():
            lines.append(
                f"| {name} | {f['normalizer']} | {f['description']} | {', '.join(f['synonyms'][:8])} |"
                if mode == "markdown"
                else f"{name:20s} [{f['normalizer']}] {f['description']} (synonyms: {', '.join(f['synonyms'][:6])})"
            )
        print("\n".join(lines))
        return 0
    if args.name == "mcp":
        from PyNCBI.mcp.server import mcp_manifest

        print(json.dumps(mcp_manifest(), indent=2))
        return 0
    print(json.dumps(json_schema(args.name), indent=2))
    return 0


def _cmd_screen(args: argparse.Namespace) -> int:
    mode = _determine_mode(args)
    targets: str | list[str] = args.targets if len(args.targets) > 1 else args.targets[0]
    require = tuple(x.strip() for x in (args.require or "").replace(",", " ").split() if x.strip())
    res = screen_studies(
        targets,
        organism=args.organism,
        dataset_type=args.type,
        min_samples=args.min_samples,
        limit=args.limit,
        require=require,
        check_file_sizes=not args.no_sizes,
        sort_by=args.sort_by,
        max_workers=args.workers,
        save_to=args.output,
    )
    saved = [str(res.saved_path)] if res.saved_path else []
    summary = f"Screened {res.study_count + len(res.errors)} study(ies); {res.study_count} kept"
    if mode == "json":
        print(
            render_output(
                res.to_dict(),
                mode="json",
                command="screen",
                target=res.query or ",".join(args.targets),
                summary=summary,
                saved_files=saved,
                warnings=res.notes,
                next_steps=[f"pyncbi metadata {res.rows[0]['gse_id']} --markdown"]
                if res.rows
                else [],
            )
        )
        return 0
    body = res.to_markdown() if mode == "markdown" else res.to_text()
    if saved:
        body += f"\n\nSaved screening table to: {saved[0]}"
    print(body)
    return 0
