"""Trust & ergonomics: not-found detection, error-code taxonomy, envelope metadata,
console-encoding safety, argparse suggestions, --no-color, --version details."""

from __future__ import annotations

import json

import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.core.exceptions import (
    AccessionNotFoundError,
    AccessionPrivateError,
    BotChallengeError,
    NoDataAvailableError,
    PyNCBIError,
    RateLimitExceededError,
    error_code_for,
    error_payload,
)
from PyNCBI.domains.geo import inspect_accession
from PyNCBI.mcp.server import handle_jsonrpc_message

GEO_NOT_FOUND_HTML = (
    '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\n<HTML><HEAD><style>'
    + "a { text-decoration: none; } " * 80
    + "</style></HEAD><BODY>Could not find a public or private accession GSE999999999</BODY></HTML>"
)


# --- 1. Not-found accessions must raise, never return an empty "ok" report -----------------


@pytest.fixture
def fake_geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(
        Config,
        "NCBI_QUERY_URL_TEMPLATE",
        base + "/geo/query/acc.cgi?acc={accession}&targ=self&form=text&view=quick",
    )
    monkeypatch.setattr(
        Config,
        "NCBI_BATCH_GSM_URL_TEMPLATE",
        base + "/geo/query/acc.cgi?acc={accession}&targ=gsm&form=text&view=brief",
    )
    for acc in ("GSE999999999", "GSM999999999", "GPL999999"):
        local_http.add(
            f"/geo/query/acc.cgi?acc={acc}&targ=self&form=text&view=quick",
            GEO_NOT_FOUND_HTML,
            "text/html",
        )
    return local_http


@pytest.mark.parametrize("acc", ["GSE999999999", "GSM999999999", "GPL999999"])
def test_html_response_to_soft_request_raises_accession_not_found(fake_geo, acc):
    with pytest.raises(AccessionNotFoundError) as excinfo:
        inspect_accession(acc)
    assert acc in str(excinfo.value)


def test_cli_inspect_missing_accession_returns_error_envelope_with_code(fake_geo, capsys):
    rc = cli_main(["inspect", "GSE999999999", "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_code"] == "not_found"
    assert payload["retryable"] is False
    assert payload["hint"]
    assert payload["schema_version"]


# --- 2. Error-code taxonomy shared by CLI JSON and MCP ------------------------------------


def test_error_code_taxonomy():
    assert error_code_for(AccessionNotFoundError("GSE1")) == "not_found"
    assert error_code_for(AccessionPrivateError("GSE1")) == "private"
    assert error_code_for(NoDataAvailableError("GSE1")) == "no_data"
    assert error_code_for(RateLimitExceededError("https://x")) == "rate_limited"
    assert error_code_for(BotChallengeError("https://x")) == "bot_challenge"
    assert error_code_for(ValueError("bad")) == "invalid_argument"
    assert error_code_for(KeyError("k")) == "internal_error"

    payload = error_payload(RateLimitExceededError("https://x", retry_after=2.0), command="search")
    assert payload["ok"] is False
    assert payload["error_code"] == "rate_limited"
    assert payload["retryable"] is True
    assert payload["retry_after_sec"] == 2.0
    assert payload["command"] == "search"
    assert "NCBI_API_KEY" in payload["hint"]


def test_mcp_error_result_carries_error_code_and_hint(fake_geo):
    resp = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "ncbi_inspect", "arguments": {"accession": "GSE999999999"}},
        }
    )
    assert resp is not None and resp["result"]["isError"] is True
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["error_code"] == "not_found"
    assert body["hint"]
    assert body["retryable"] is False


# --- 3. Envelope metadata --------------------------------------------------------------------


def test_json_envelope_includes_schema_version_timing_and_request_count(capsys):
    rc = cli_main(["cache", "status", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "1.1"
    assert payload["pyncbi_version"]
    assert isinstance(payload["elapsed_ms"], (int, float)) and payload["elapsed_ms"] >= 0
    assert isinstance(payload["request_count"], int)


# --- 4. Console encoding safety --------------------------------------------------------------


def test_console_safe_text_transliterates_when_encoding_cannot_represent_glyphs():
    from PyNCBI.core.formatter import console_safe_text

    text = "• GSE1 — 47 samples ★★ → done"
    assert console_safe_text(text, encoding="utf-8") == text
    safe = console_safe_text(text, encoding="cp1252")
    safe.encode("cp1252")  # must not raise
    assert "GSE1" in safe and "47 samples" in safe
    assert console_safe_text(text, encoding="ascii").isascii()


# --- 5. argparse suggestions, --no-color, --version ------------------------------------------


def test_cli_suggests_closest_subcommand_on_typo(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli_main(["inspekt", "GSE1"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "inspect" in err and "did you mean" in err.lower()


def test_cli_version_reports_runtime_details(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli_main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "pyncbi 1." in out
    assert "python" in out.lower() and "pandas" in out.lower() and "requests" in out.lower()


def test_cli_no_color_flag_disables_ansi(monkeypatch, capsys):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    rc = cli_main(["--no-color", "cache", "status"])
    assert rc == 0
    out = capsys.readouterr()
    assert "\x1b[" not in out.out + out.err
    import os

    assert os.environ.get("NO_COLOR") == "1"


def test_missing_required_tool_argument_is_reported_as_an_invalid_argument():
    """An agent that forgets a required field must learn which one, not see 'internal_error'."""
    from PyNCBI.mcp.server import handle_jsonrpc_message

    response = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ncbi_metadata", "arguments": {}},
        }
    )
    assert response is not None
    structured = response["result"]["structuredContent"]
    assert response["result"]["isError"] is True
    assert structured["error_code"] == "invalid_argument"
    assert "gse_id" in structured["message"]
    assert "required" in structured["message"].lower()


def test_parquet_output_explains_the_missing_engine_instead_of_leaking_pandas(tmp_path):
    """`.parquet` is documented everywhere, but the engine is an optional dependency."""
    import pandas as pd

    from PyNCBI.core.formatter import save_data

    dest = tmp_path / "table.parquet"
    frame = pd.DataFrame({"gene": ["A1BG"], "GSM1": [3]})
    try:
        import pyarrow  # noqa: F401

        pyarrow_available = True
    except ImportError:
        pyarrow_available = False

    if pyarrow_available:
        assert save_data(frame, dest) == dest
        assert dest.is_file()
        return

    with pytest.raises(PyNCBIError) as excinfo:
        save_data(frame, dest)
    message = f"{excinfo.value} {excinfo.value.hint or ''}"
    assert "parquet" in message.lower()
    assert "pyarrow" in message.lower()


def test_piping_output_into_head_does_not_print_a_broken_pipe_error(monkeypatch, capsys):
    """`pyncbi ... | head -5` is normal usage; the reader closing early is not an error.

    Without handling, the interpreter's final flush of stdout raises and Python prints
    `Exception ignored in: <_io.TextIOWrapper ...> BrokenPipeError` on stderr, which looks
    like a crash in a log.
    """
    import io
    import sys

    from PyNCBI.cli.main import main

    class ClosedPipe(io.TextIOBase):
        def write(self, _text: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self) -> None:
            raise BrokenPipeError(32, "Broken pipe")

    pipe = ClosedPipe()
    monkeypatch.setattr(sys, "stdout", pipe)
    exit_code = main(["schema", "vocabulary"])
    # Do not leave a deliberately broken flush for TextIOBase's finalizer.
    monkeypatch.setattr(pipe, "flush", lambda: None)
    pipe.close()

    assert exit_code == 141, "128 + SIGPIPE is the conventional status for a closed reader"
    captured = capsys.readouterr()
    assert "BrokenPipeError" not in captured.err
    assert "Traceback" not in captured.err
