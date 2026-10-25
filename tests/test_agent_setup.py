"""Install complete, portable instructions and reject unsafe MCP calls before I/O."""

from pathlib import Path

import pytest

from PyNCBI.cli.main import main
from PyNCBI.mcp.protocol import handle_jsonrpc_message
from PyNCBI.mcp.validation import validate_tool_arguments


@pytest.mark.parametrize(
    "target,folder",
    [
        ("codex", ".agents/skills/pyncbi"),
        ("claude", ".claude/skills/pyncbi"),
        ("antigravity", ".gemini/antigravity-cli/skills/pyncbi"),
        ("cursor", ".cursor/skills/pyncbi"),
    ],
)
def test_installer_preserves_relative_skill_resources(tmp_path, monkeypatch, target, folder):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["skill", "install", "--target", target]) == 0
    root = tmp_path / folder
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    assert (root / "references/workflows.md").is_file()
    assert "references/workflows.md" in skill
    canonical = Path(__file__).resolve().parents[1] / "src/PyNCBI/resources"
    for relative in ("SKILL.md", "references/workflows.md"):
        assert (root / relative).read_bytes() == (canonical / relative).read_bytes()


def test_all_installs_user_skills_without_project_side_effects(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["skill", "install"]) == 0
    for folder in (".agents/skills", ".claude/skills", ".gemini/antigravity-cli/skills"):
        assert (tmp_path / folder / "pyncbi/references/workflows.md").is_file()
    assert not (tmp_path / ".cursor").exists()


def test_custom_skill_directory_is_self_contained(tmp_path):
    assert main(["skill", "install", "--dir", str(tmp_path / "skill")]) == 0
    assert (tmp_path / "skill/SKILL.md").is_file()
    assert (tmp_path / "skill/references/workflows.md").is_file()


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("ncbi_download", {"accession": "GSE1", "dryrun": True}),
        ("ncbi_download", {"accession": "GSE1", "dry_run": "false"}),
        ("ncbi_download", {"accession": "GSE1", "file_index": True}),
        ("ncbi_expression", {"gse_id": "GSE1", "max_download_mb": -1}),
        ("ncbi_samples", {"gse_id": "GSE1", "page_size": "ten"}),
        ("ncbi_samples", {}),
        ("ncbi_samples", {"gse_id": " "}),
        ("ncbi_gene", {"query": ["BRCA1", 123]}),
        ("ncbi_gene", {"query": []}),
    ],
)
def test_invalid_tool_arguments_fail_before_any_io(local_http, monkeypatch, tool, arguments):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid tool input reached a networked domain operation")

    from PyNCBI.mcp import handlers

    for operation in (
        "download_geo_files",
        "load_expression",
        "extract_gse_samples",
        "search_gene",
    ):
        monkeypatch.setattr(handlers, operation, unexpected)
    reply = handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    )
    payload = reply["result"]["structuredContent"]
    assert reply["result"]["isError"] is True
    assert payload["error_code"] == "invalid_argument"
    assert payload["retryable"] is False
    assert payload["request_count"] == 0
    assert local_http.hits == []


def test_numeric_constraints_do_not_reject_small_blast_expect_values():
    validate_tool_arguments("ncbi_blast", {"query": "ACGT", "evalue": 0.001})
    validate_tool_arguments(
        "ncbi_expression", {"gse_id": "GSE1", "max_rows": 0, "max_download_mb": 0}
    )
