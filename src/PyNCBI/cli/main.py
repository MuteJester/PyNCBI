"""Entry point: parses arguments, configures reporting, and dispatches to a command."""

from __future__ import annotations

import contextlib
import json
import os
import sys

from PyNCBI.cli.cmd_admin import _cmd_cache, _cmd_skill, _cmd_status
from PyNCBI.cli.cmd_geo import (
    _cmd_download,
    _cmd_expression,
    _cmd_files,
    _cmd_gds,
    _cmd_gpl,
    _cmd_inspect,
    _cmd_preview,
    _cmd_samples,
)
from PyNCBI.cli.cmd_search import (
    _cmd_gene,
    _cmd_link,
    _cmd_pubmed,
    _cmd_search,
    _cmd_taxonomy,
)
from PyNCBI.cli.cmd_sequence import _cmd_blast, _cmd_fasta, _cmd_fastq, _cmd_genome, _cmd_sra
from PyNCBI.cli.cmd_study import (
    _cmd_bundle,
    _cmd_metadata,
    _cmd_schema,
    _cmd_screen,
    _cmd_variant,
)
from PyNCBI.cli.console import print  # noqa: A001
from PyNCBI.cli.parser import build_parser
from PyNCBI.core.exceptions import error_payload
from PyNCBI.core.formatter import (
    begin_run,
    console_safe_text,
)
from PyNCBI.core.reporter import Verbosity, get_reporter


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "no_color", False):
        os.environ["NO_COLOR"] = "1"
        os.environ.pop("FORCE_COLOR", None)

    if not args.subcommand:
        parser.print_help()
        return 0
    begin_run()

    if args.subcommand == "mcp":
        if getattr(args, "http", False):
            from PyNCBI.mcp.server import run_http_server

            run_http_server(host=args.host, port=args.port)
            return 0
        from PyNCBI.mcp.server import run_mcp_server

        run_mcp_server()
        return 0

    # Configure centralized verbosity / progress reporter (strictly on stderr)
    verbose_count = int(getattr(args, "verbose", 0) or 0)
    is_quiet = bool(getattr(args, "quiet", False))
    reporter = get_reporter()
    if is_quiet:
        reporter.set_verbosity(Verbosity.SILENT)
    elif verbose_count >= 2:
        reporter.set_verbosity(Verbosity.DEBUG)
    elif verbose_count == 1:
        reporter.set_verbosity(Verbosity.VERBOSE)
    elif getattr(args, "json", False) or getattr(args, "markdown", False):
        reporter.set_verbosity(Verbosity.SILENT)

    dispatch = {
        "search": _cmd_search,
        "inspect": _cmd_inspect,
        "samples": _cmd_samples,
        "preview": _cmd_preview,
        "files": _cmd_files,
        "download": _cmd_download,
        "pubmed": _cmd_pubmed,
        "gene": _cmd_gene,
        "sra": _cmd_sra,
        "fasta": _cmd_fasta,
        "link": _cmd_link,
        "gpl": _cmd_gpl,
        "variant": _cmd_variant,
        "bundle": _cmd_bundle,
        "taxonomy": _cmd_taxonomy,
        "fastq": _cmd_fastq,
        "genome": _cmd_genome,
        "gds": _cmd_gds,
        "metadata": _cmd_metadata,
        "expression": _cmd_expression,
        "screen": _cmd_screen,
        "blast": _cmd_blast,
        "schema": _cmd_schema,
        "clinvar": _cmd_variant,
        "cache": _cmd_cache,
        "skill": _cmd_skill,
        "status": _cmd_status,
    }

    handler = dispatch.get(args.subcommand)
    if handler is None:
        parser.print_help()
        return 1

    try:
        return handler(args)
    except BrokenPipeError:
        # The reader closed early (`| head`, a quitting pager). Point stdout at the void
        # so the interpreter's final flush cannot raise again, and report the conventional
        # 128 + SIGPIPE status instead of an error.
        with contextlib.suppress(OSError):
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        return 141
    except KeyboardInterrupt:
        sys.stderr.write("Interrupted.\n")
        return 130
    except Exception as e:
        target = ""
        for attr in ("accession", "gse_id", "gpl_id", "accessions", "pmids", "query", "queries"):
            val = getattr(args, attr, None)
            if val:
                target = " ".join(val) if isinstance(val, list) else str(val)
                break
        payload = error_payload(e, command=args.subcommand, target=target)
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2, default=str))
        else:
            msg = f"Error [{payload['error_code']}] ({type(e).__name__}): {payload['message']}"
            if payload.get("hint"):
                msg += f"\n  Hint: {payload['hint']}"
            sys.stderr.write(console_safe_text(msg, getattr(sys.stderr, "encoding", None)) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
