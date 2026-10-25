"""Tool dispatch: every `ncbi_*` tool call is executed here and rendered as an envelope."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyNCBI.core.formatter import format_table_preview, render_output, save_data
from PyNCBI.core.tables import column, iter_records, preview_records
from PyNCBI.domains.blast_search import BlastJob, blast, check_blast, fetch_blast
from PyNCBI.domains.bundle import build_study_bundle
from PyNCBI.domains.entrez import (
    fetch_fasta,
    fetch_pubmed,
    fetch_sra_runs,
    fetch_taxonomy,
    link_accession,
    search_gene,
    search_geo,
    search_ncbi,
    search_pubmed,
    search_taxonomy,
)
from PyNCBI.domains.expression import load_expression
from PyNCBI.domains.genomes import download_genome, fetch_assembly, search_assemblies
from PyNCBI.domains.geo import (
    download_geo_files,
    extract_gse_samples,
    inspect_accession,
    plan_geo_download,
    preview_geo_file,
)
from PyNCBI.domains.literature import METHODS_SECTIONS, attach_full_text
from PyNCBI.domains.screening import screen_studies
from PyNCBI.domains.sequencing import download_fastq, fetch_fastq_urls
from PyNCBI.domains.variants import fetch_variants
from PyNCBI.io.http_client import format_size_human, get_client
from PyNCBI.metadata import build_study_record
from PyNCBI.metadata.harmonize import sample_metadata_view
from PyNCBI.metadata.serving import bounded_payload, paginate


def _structured_from_text(text: str) -> dict[str, Any] | None:
    """Extract the JSON envelope from a tool's text output, if it has one."""
    candidate = text.strip()
    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError):
        if "\n---\n" in text:
            tail = text.split("\n---\n", 1)[1].strip()
            try:
                parsed = json.loads(tail)
            except (ValueError, TypeError):
                return None
        else:
            return None
    return parsed if isinstance(parsed, dict) else None


def _argument_text(arguments: dict[str, Any], key: str, default: str | None = None) -> str:
    """Read a string argument, accepting the JSON array an agent may send instead.

    Several tools take "one or more comma-separated accessions" as a single string.
    Models routinely send `["GSE1", "GSE2"]` for such a field, and `str()` would turn that
    into `"['GSE1', 'GSE2']"` - an identifier NCBI cannot resolve, which came back as a
    successful call with zero records. Lists are joined with commas, which is exactly what
    the schema asks for, and everything else is stringified as before.
    """
    if key not in arguments:
        if default is None:
            # Required. Name the field, so the agent can fix the call; a bare KeyError
            # surfaced as "internal_error", which reads like a bug in PyNCBI instead.
            raise ValueError(f"missing required argument {key!r}")
        return default
    value = arguments[key]
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return str(value)


def execute_mcp_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute an MCP tool call and return a context-window safe text/JSON response."""
    if name == "ncbi_search":
        db = _argument_text(arguments, "db", "geo").lower().strip()
        query = _argument_text(arguments, "query")
        limit = int(arguments.get("limit", 15))

        if db in ("geo", "gds", "gse"):
            hits = search_geo(
                query,
                organism=arguments.get("organism"),
                dataset_type=arguments.get("dataset_type"),
                min_samples=arguments.get("min_samples"),
                limit=limit,
            )
            return render_output(
                hits,
                mode="json",
                command="ncbi_search",
                target=query,
                summary=f"Found {len(hits)} GEO datasets matching '{query}'",
                next_steps=[
                    f"Call ncbi_inspect with accession='{hits[0].accession}' to see sample breakdown and file sizes"
                ]
                if hits
                else [],
            )

        if db == "pubmed":
            arts = search_pubmed(query, limit=limit)
            return render_output(
                arts,
                mode="json",
                command="ncbi_search",
                target=query,
                summary=f"Found {len(arts)} PubMed articles matching '{query}'",
            )

        if db == "gene":
            genes = search_gene(
                query,
                organism=arguments.get("organism", "Homo sapiens"),
                limit=limit,
            )
            return render_output(
                genes,
                mode="json",
                command="ncbi_search",
                target=query,
                summary=f"Found {len(genes)} NCBI Gene records matching '{query}'",
            )

        raw_docs = search_ncbi(db, query, limit=limit)
        return render_output(
            raw_docs,
            mode="json",
            command="ncbi_search",
            target=f"{db}:{query}",
            summary=f"Found {len(raw_docs)} records in NCBI '{db}' matching '{query}'",
        )

    if name == "ncbi_inspect":
        accession = _argument_text(arguments, "accession").strip()
        check_sizes = bool(arguments.get("check_file_sizes", True))
        report = inspect_accession(accession, check_file_sizes=check_sizes)
        return (
            report.to_markdown()
            + "\n\n---\n"
            + render_output(
                report,
                mode="json",
                command="ncbi_inspect",
                target=accession,
                summary=f"{report.record_type} {report.accession}: {report.title}",
            )
        )

    if name == "ncbi_samples":
        gse_id = _argument_text(arguments, "gse_id").strip()
        where = arguments.get("where")
        save_to = arguments.get("save_to")
        chars_only = bool(arguments.get("characteristics_only", False))
        max_rows = int(arguments.get("max_preview_rows", 15))

        combined_df, char_df, summaries = extract_gse_samples(gse_id, where=where)
        target_df, harmonization = sample_metadata_view(
            combined_df,
            char_df,
            chars_only=chars_only,
            harmonize=bool(arguments.get("harmonize", False)),
        )
        saved_files: list[str] = []
        if save_to and target_df["rows"]:
            out_path = save_data(target_df, save_to)
            saved_files.append(str(out_path))
        if bool(arguments.get("as_json", False)):
            rows_all = list(iter_records(target_df))
            page, next_token, total = paginate(
                rows_all,
                page_size=int(arguments.get("page_size", 50)),
                page_token=arguments.get("page_token"),
            )
            payload = {
                "gse_id": gse_id,
                "sample_count": total,
                "columns": [str(c) for c in target_df["columns"]],
                "characteristics_summary": [cs.to_dict() for cs in summaries],
                "harmonization": harmonization,
                "rows": page,
                "next_page_token": next_token,
            }
            return render_output(
                bounded_payload(payload),
                mode="json",
                command="ncbi_samples",
                target=gse_id,
                summary=f"{total} samples in {gse_id} (page of {len(page)})",
                saved_files=saved_files,
            )

        summary_lines = [
            f"### {gse_id} Cohort Characteristics Breakdown ({len(target_df['rows'])} samples)",
            *[f"- **{cs.name}** ({cs.dtype}): {cs.format_brief()}" for cs in summaries],
            "",
            format_table_preview(target_df, mode="markdown", max_rows=max_rows),
        ]
        if saved_files:
            summary_lines.append(f"\n**Saved full table to:** `{saved_files[0]}`")
        envelope = render_output(
            {
                "gse_id": gse_id,
                "sample_count": len(target_df["rows"]),
                "columns": [str(c) for c in target_df["columns"]],
                "characteristics_summary": [cs.to_dict() for cs in summaries],
                "harmonization": harmonization,
                "preview": preview_records(target_df, max_rows),
            },
            mode="json",
            command="ncbi_samples",
            target=gse_id,
            summary=f"{len(target_df['rows'])} samples in {gse_id}",
            saved_files=saved_files,
        )
        return "\n".join(summary_lines) + "\n\n---\n" + envelope

    if name == "ncbi_preview":
        accession = _argument_text(arguments, "accession").strip()
        source = _argument_text(arguments, "source", "auto")
        file_index = arguments.get("file_index")
        pattern = arguments.get("pattern")
        max_rows = int(arguments.get("max_rows", 8))

        preview = preview_geo_file(
            accession,
            source=source,
            file_index=int(file_index) if file_index is not None else None,
            pattern=pattern,
            max_rows=max_rows,
        )
        return (
            preview.to_markdown()
            + "\n\n---\n"
            + render_output(
                preview,
                mode="json",
                command="ncbi_preview",
                target=accession,
                summary=f"Preview of {preview.file_name} ({preview.total_columns} columns, index='{preview.index_name}')",
            )
        )

    if name == "ncbi_download":
        accession = _argument_text(arguments, "accession").strip()
        output_path = arguments.get("output_path")
        source = _argument_text(arguments, "source", "auto")
        file_index = arguments.get("file_index")
        pattern = arguments.get("pattern")
        unpack = bool(arguments.get("unpack", False))

        if bool(arguments.get("dry_run", False)):
            plan = plan_geo_download(
                accession,
                output_dir=output_path,
                source=source,
                file_index=int(file_index) if file_index is not None else None,
                pattern=pattern,
                where=arguments.get("where"),
            )
            return render_output(
                plan.to_dict(),
                mode="json",
                command="ncbi_download",
                target=accession,
                summary=f"Would download {plan.file_count} file(s), {plan.total_human} total "
                f"(nothing was downloaded)",
            )
        paths = download_geo_files(
            accession,
            output_dir=output_path,
            source=source,
            file_index=int(file_index) if file_index is not None else None,
            pattern=pattern,
            where=arguments.get("where"),
            unpack=unpack,
            max_download_mb=float(arguments["max_download_mb"])
            if arguments.get("max_download_mb") is not None
            else None,
        )
        str_paths = [str(p) for p in paths]
        return render_output(
            {"downloaded_paths": str_paths},
            mode="json",
            command="ncbi_download",
            target=accession,
            summary=f"Downloaded {len(str_paths)} file(s) for {accession}",
            saved_files=str_paths,
        )

    if name == "ncbi_pubmed":
        q = _argument_text(arguments, "pmids_or_query").strip()
        limit = int(arguments.get("limit", 5))
        cleaned = q.replace("PMID:", "").replace(",", "").replace(" ", "")
        articles = fetch_pubmed(q) if cleaned.isdigit() else search_pubmed(q, limit=limit)
        sections_arg = arguments.get("sections")
        if isinstance(sections_arg, str):
            sections_arg = [s.strip() for s in sections_arg.split(",") if s.strip()]
        if sections_arg:
            attach_full_text(articles, sections=list(sections_arg))
        elif bool(arguments.get("fulltext", False)):
            attach_full_text(articles, sections=None)
        elif bool(arguments.get("methods", False)):
            attach_full_text(articles, sections=METHODS_SECTIONS)
        n_ft = sum(1 for a in articles if a.full_text is not None and a.full_text.is_open_access)
        return render_output(
            articles,
            mode="json",
            command="ncbi_pubmed",
            target=q,
            summary=f"Retrieved {len(articles)} PubMed article(s)"
            + (f", {n_ft} with PMC full text" if n_ft else ""),
        )

    if name == "ncbi_gene":
        q = _argument_text(arguments, "query").strip()
        organism = arguments.get("organism", "Homo sapiens")
        limit = int(arguments.get("limit", 5))
        records = search_gene(q, organism=organism, limit=limit)
        return render_output(
            records,
            mode="json",
            command="ncbi_gene",
            target=q,
            summary=f"Retrieved {len(records)} NCBI Gene record(s) for '{q}'",
        )

    if name == "ncbi_link":
        acc = _argument_text(arguments, "accession").strip()
        links = link_accession(acc)
        return render_output(
            links,
            mode="json",
            command="ncbi_link",
            target=acc,
            summary=f"Cross-database links for {acc}",
        )

    if name == "ncbi_sra":
        q = _argument_text(arguments, "accession_or_query").strip()
        limit = int(arguments.get("limit", 100))
        save_to = arguments.get("save_to")
        df = fetch_sra_runs(q, limit=limit, save_to=save_to)
        saved_files = [str(save_to)] if save_to and df["rows"] else []
        payload = {
            "query": q,
            "run_count": len(df["rows"]),
            "columns": [str(c) for c in df["columns"]],
            "runs": preview_records(df, 25),
        }
        return render_output(
            payload,
            mode="json",
            command="ncbi_sra",
            target=q,
            summary=f"Found {len(df['rows'])} SRA sequencing run(s) for '{q}'",
            saved_files=saved_files,
        )

    if name == "ncbi_fasta":
        accs = _argument_text(arguments, "accessions").strip()
        db = _argument_text(arguments, "db", "nuccore")
        seq_start = arguments.get("seq_start")
        seq_stop = arguments.get("seq_stop")
        save_to = arguments.get("save_to")
        fasta_records = fetch_fasta(
            accs,
            db=db,
            seq_start=int(seq_start) if seq_start is not None else None,
            seq_stop=int(seq_stop) if seq_stop is not None else None,
            save_to=save_to,
        )
        saved_files = [str(save_to)] if save_to and fasta_records else []
        return render_output(
            [r.to_dict() for r in fasta_records],
            mode="json",
            command="ncbi_fasta",
            target=accs,
            summary=f"Fetched {len(fasta_records)} FASTA sequence(s) from '{db}'",
            saved_files=saved_files,
        )

    if name == "ncbi_bundle":
        gse_id = _argument_text(arguments, "gse_id").strip()
        bundle = build_study_bundle(
            gse_id,
            output_dir=arguments.get("output_dir"),
            include_matrix=bool(arguments.get("include_matrix", False)),
            include_sra=bool(arguments.get("include_sra", True)),
            include_methods=bool(arguments.get("include_methods", True)),
            where=arguments.get("where"),
            expand_subseries=bool(arguments.get("expand_subseries", False)),
        )
        overview = bundle.files["overview"].read_text(encoding="utf-8")
        if len(overview) > 12000:
            overview = (
                overview[:12000] + "\n\n… (truncated; read STUDY_OVERVIEW.md for the full overview)"
            )
        return (
            overview
            + "\n\n---\n"
            + render_output(
                bundle,
                mode="json",
                command="ncbi_bundle",
                target=gse_id,
                summary=f"Study bundle for {gse_id} ({bundle.sample_count} samples) written to {bundle.output_dir}",
                saved_files=[str(p) for p in bundle.files.values()],
                warnings=bundle.warnings,
            )
        )

    if name == "ncbi_variant":
        q = _argument_text(arguments, "query").strip()
        result = fetch_variants(
            q,
            source=_argument_text(arguments, "source", "auto"),
            significance=arguments.get("significance"),
            gene=arguments.get("gene"),
            limit=int(arguments.get("limit", 20)),
        )
        return render_output(
            result,
            mode="json",
            command="ncbi_variant",
            target=q,
            summary=f"{len(result['clinvar'])} ClinVar variant(s), {len(result['dbsnp'])} dbSNP record(s) for '{q}'",
        )

    if name == "ncbi_metadata":
        gse_id = _argument_text(arguments, "gse_id").strip()
        enrich_arg = arguments.get("enrich") or []
        if isinstance(enrich_arg, str):
            enrich_arg = [e.strip() for e in enrich_arg.split(",") if e.strip()]
        record = build_study_record(gse_id, where=arguments.get("where"), enrich=tuple(enrich_arg))
        saved: list[str] = []
        if arguments.get("save_to"):
            from PyNCBI.metadata import save_study_record

            manifest = save_study_record(record, _argument_text(arguments, "save_to", ""))
            saved = [
                str(Path(_argument_text(arguments, "save_to", "")) / f["path"])
                for f in manifest["files"]
            ]
        data = record.to_dict(include_samples=False)
        samples = [s.to_dict() for s in record.samples]
        page, next_token, total = paginate(
            samples,
            page_size=int(arguments.get("page_size", 50)),
            page_token=arguments.get("page_token"),
        )
        data["samples"] = page
        data["next_page_token"] = next_token
        quality = record.quality.score if record.quality else "n/a"
        return render_output(
            bounded_payload(data),
            mode="json",
            command="ncbi_metadata",
            target=gse_id,
            summary=f"{gse_id}: {total} samples ({len(page)} on this page), harmonized fields: {', '.join(record.harmonization.get('mapped_fields', [])) or 'none'}, quality {quality}/100",
            saved_files=saved,
            warnings=[
                f"{i.severity}:{i.code}: {i.message}"
                for i in (record.quality.issues if record.quality else [])
            ],
        )

    if name == "ncbi_screen":
        accessions = arguments.get("accessions") or []
        query = _argument_text(arguments, "query", "").strip()
        if not accessions and not query:
            raise ValueError("ncbi_screen needs either `query` or `accessions`")
        targets: Any = [str(a) for a in accessions] if accessions else query
        res = screen_studies(
            targets,
            organism=arguments.get("organism"),
            dataset_type=arguments.get("dataset_type"),
            min_samples=arguments.get("min_samples"),
            limit=int(arguments.get("limit", 20)),
            require=tuple(str(f) for f in (arguments.get("require") or [])),
            sort_by=arguments.get("sort_by"),
            save_to=arguments.get("save_to"),
        )
        return render_output(
            bounded_payload(res.to_dict()),
            mode="json",
            command="ncbi_screen",
            target=res.query or ",".join(str(a) for a in accessions),
            summary=f"Screened {res.study_count + len(res.errors)} study(ies); {res.study_count} kept",
            saved_files=[str(res.saved_path)] if res.saved_path else [],
            warnings=res.notes,
        )

    if name == "ncbi_expression":
        gse_id = _argument_text(arguments, "gse_id").strip()
        expr_res = load_expression(
            gse_id,
            prefer=_argument_text(arguments, "prefer", "auto"),
            count_type=_argument_text(arguments, "count_type", "raw_counts"),
            annotate=bool(arguments.get("annotate", False)),
            where=arguments.get("where"),
            save_to=arguments.get("output_path"),
            max_download_mb=arguments.get("max_download_mb"),
        )
        max_rows = int(arguments.get("max_rows", 8))
        payload = expr_res.to_dict()
        payload["preview"] = preview_records(expr_res.matrix, max_rows, 12)
        payload["samples_preview"] = preview_records(expr_res.sample_table, max_rows, 8)
        saved = []
        if expr_res.saved_path:
            saved = [
                str(expr_res.saved_path),
                str(
                    Path(expr_res.saved_path).with_name(
                        f"{Path(expr_res.saved_path).stem}_samples.csv"
                    )
                ),
            ]
        rows, cols = expr_res.shape
        return render_output(
            bounded_payload(payload),
            mode="json",
            command="ncbi_expression",
            target=gse_id,
            summary=f"{gse_id}: {rows:,} features x {cols:,} samples from '{expr_res.source}'",
            saved_files=saved,
            warnings=expr_res.notes,
        )

    if name == "ncbi_fastq":
        acc = _argument_text(arguments, "accession").strip()
        if bool(arguments.get("download", False)):
            fastq_manifest = fetch_fastq_urls(acc)
            paths = download_fastq(
                fastq_manifest,
                arguments.get("output_dir"),
                runs=arguments.get("runs"),
                max_download_mb=arguments.get("max_download_mb"),
                check_disk_space=not bool(arguments.get("no_disk_check", False)),
                max_workers=int(arguments.get("max_workers", 4)),
            )
            str_paths = [str(p) for p in paths]
            return render_output(
                {"downloaded_files": str_paths},
                mode="json",
                command="ncbi_fastq",
                target=acc,
                summary=f"Downloaded {len(str_paths)} FASTQ file(s) for {acc}",
                saved_files=str_paths,
            )
        df = fetch_fastq_urls(acc, save_to=arguments.get("save_to"))
        total = int(sum(value or 0 for value in column(df, "size_bytes"))) if df["rows"] else 0
        payload = {
            "accession": acc,
            "file_count": len(df["rows"]),
            "run_count": int(len(set(column(df, "run_accession")))) if df["rows"] else 0,
            "total_size_bytes": total,
            "total_size_human": format_size_human(total),
            "files": preview_records(df, 50),
        }
        return render_output(
            payload,
            mode="json",
            command="ncbi_fastq",
            target=acc,
            summary=f"{len(df['rows'])} FASTQ file(s) ({format_size_human(total)}) for {acc}",
            saved_files=[_argument_text(arguments, "save_to", "")]
            if arguments.get("save_to") and df["rows"]
            else [],
        )

    if name == "ncbi_genome":
        target = _argument_text(arguments, "target").strip()
        if bool(arguments.get("download", False)):
            include = tuple(
                x.strip()
                for x in _argument_text(arguments, "include", "genome").split(",")
                if x.strip()
            )
            genome_result = download_genome(
                target,
                arguments.get("output_dir"),
                include=include,
                max_download_mb=arguments.get("max_download_mb"),
                check_disk_space=not bool(arguments.get("no_disk_check", False)),
            )
            return render_output(
                genome_result,
                mode="json",
                command="ncbi_genome",
                target=target,
                summary=f"Downloaded {len(genome_result.files)} genome file(s) for {target}",
                saved_files=[str(p) for p in genome_result.files],
            )
        if target.upper().startswith(("GCF_", "GCA_")):
            assemblies = [fetch_assembly(target)]
        else:
            assemblies = search_assemblies(
                target,
                reference_only=bool(arguments.get("reference_only", True)),
                limit=int(arguments.get("limit", 10)),
            )
        return render_output(
            assemblies,
            mode="json",
            command="ncbi_genome",
            target=target,
            summary=f"Found {len(assemblies)} assembly record(s) for '{target}'",
        )

    if name == "ncbi_taxonomy":
        q = _argument_text(arguments, "query").strip()
        taxa = (
            fetch_taxonomy([q])
            if q.isdigit()
            else search_taxonomy(q, limit=int(arguments.get("limit", 5)))
        )
        return render_output(
            taxa,
            mode="json",
            command="ncbi_taxonomy",
            target=q,
            summary=f"Retrieved {len(taxa)} Taxonomy record(s) for '{q}'",
        )

    if name == "ncbi_blast":
        rid = _argument_text(arguments, "rid", "").strip()
        if rid:
            job = check_blast(rid)
            if not job.is_ready:
                return render_output(
                    job.to_dict(),
                    mode="json",
                    command="ncbi_blast",
                    target=rid,
                    summary=f"BLAST job {rid} status: {job.status}. {job.message}",
                    next_steps=[f"Call ncbi_blast again with rid='{rid}'"],
                )
            blast_result = fetch_blast(rid)
        else:
            query = _argument_text(arguments, "query", "").strip()
            if not query:
                raise ValueError("ncbi_blast needs either `query` (to submit) or `rid` (to fetch)")
            outcome = blast(
                query,
                program=_argument_text(arguments, "program", "blastn"),
                database=arguments.get("database"),
                evalue=arguments.get("evalue"),
                hitlist_size=int(arguments.get("hitlist_size", 50)),
                entrez_query=arguments.get("entrez_query"),
                wait=bool(arguments.get("wait", True)),
                timeout=float(arguments.get("timeout_seconds", 120)),
            )
            if isinstance(outcome, BlastJob):
                return render_output(
                    outcome.to_dict(),
                    mode="json",
                    command="ncbi_blast",
                    target=outcome.rid,
                    summary=f"Submitted {outcome.program} job {outcome.rid}"
                    + (
                        f"; ready in ~{outcome.estimated_seconds}s"
                        if outcome.estimated_seconds
                        else ""
                    ),
                    next_steps=[f"Call ncbi_blast again with rid='{outcome.rid}'"],
                )
            blast_result = outcome
        return render_output(
            bounded_payload(blast_result.to_dict()),
            mode="json",
            command="ncbi_blast",
            target=blast_result.rid,
            summary=f"{blast_result.hit_count} BLAST hit(s) in {blast_result.database}",
        )

    if name == "ncbi_status":
        probe = bool(arguments.get("probe_network", True))
        health = get_client().check_health(probe_network=probe)
        return render_output(
            health,
            mode="json",
            command="ncbi_status",
            target="ncbi",
            summary=f"PyNCBI {health['version']} backend status & telemetry",
        )

    raise ValueError(f"Unknown MCP tool: {name}")
