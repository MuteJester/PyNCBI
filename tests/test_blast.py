"""NCBI BLAST URL API: submission, polling, XML parsing, and the blocking convenience wrapper."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.core.exceptions import DataError, NoDataAvailableError
from PyNCBI.domains.blast_search import (
    BLAST_PROGRAMS,
    BlastJob,
    BlastResult,
    blast,
    check_blast,
    delete_blast,
    fetch_blast,
    parse_blast_xml,
    submit_blast,
)
from PyNCBI.mcp.server import execute_mcp_tool

PUT_HTML = """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html><head><title>NCBI Blast</title></head><body>
<!--QBlastInfoBegin
\tRID = ZK9A1B2C014
\tRTOE = 27
QBlastInfoEnd
-->
<p>Your request has been submitted.</p></body></html>
"""
WAITING = """<!--QBlastInfoBegin
\tStatus=WAITING
QBlastInfoEnd
-->
"""
READY = """<!--QBlastInfoBegin
\tStatus=READY
\tThereAreHits=yes
QBlastInfoEnd
-->
"""
READY_NO_HITS = """<!--QBlastInfoBegin
\tStatus=READY
\tThereAreHits=no
QBlastInfoEnd
-->
"""
UNKNOWN = """<!--QBlastInfoBegin
\tStatus=UNKNOWN
QBlastInfoEnd
-->
"""
BLAST_XML = """<?xml version="1.0"?>
<!DOCTYPE BlastOutput PUBLIC "-//NCBI//NCBI BlastOutput/EN" "http://www.ncbi.nlm.nih.gov/dtd/NCBI_BlastOutput.dtd">
<BlastOutput>
  <BlastOutput_program>blastn</BlastOutput_program>
  <BlastOutput_version>BLASTN 2.16.0+</BlastOutput_version>
  <BlastOutput_db>nt</BlastOutput_db>
  <BlastOutput_query-ID>Query_1</BlastOutput_query-ID>
  <BlastOutput_query-def>my query</BlastOutput_query-def>
  <BlastOutput_query-len>300</BlastOutput_query-len>
  <BlastOutput_iterations>
    <Iteration>
      <Iteration_iter-num>1</Iteration_iter-num>
      <Iteration_hits>
        <Hit>
          <Hit_num>1</Hit_num>
          <Hit_id>gi|1732746264|ref|NM_007294.4|</Hit_id>
          <Hit_def>Homo sapiens BRCA1 DNA repair associated (BRCA1), transcript variant 1, mRNA</Hit_def>
          <Hit_accession>NM_007294</Hit_accession>
          <Hit_len>7088</Hit_len>
          <Hit_hsps>
            <Hsp>
              <Hsp_num>1</Hsp_num>
              <Hsp_bit-score>554.517</Hsp_bit-score>
              <Hsp_score>300</Hsp_score>
              <Hsp_evalue>1.5e-155</Hsp_evalue>
              <Hsp_query-from>1</Hsp_query-from>
              <Hsp_query-to>300</Hsp_query-to>
              <Hsp_hit-from>101</Hsp_hit-from>
              <Hsp_hit-to>400</Hsp_hit-to>
              <Hsp_query-frame>1</Hsp_query-frame>
              <Hsp_hit-frame>1</Hsp_hit-frame>
              <Hsp_identity>300</Hsp_identity>
              <Hsp_positive>300</Hsp_positive>
              <Hsp_gaps>0</Hsp_gaps>
              <Hsp_align-len>300</Hsp_align-len>
              <Hsp_qseq>ATGC</Hsp_qseq>
              <Hsp_hseq>ATGC</Hsp_hseq>
              <Hsp_midline>||||</Hsp_midline>
            </Hsp>
            <Hsp>
              <Hsp_num>2</Hsp_num>
              <Hsp_bit-score>44.1</Hsp_bit-score>
              <Hsp_score>22</Hsp_score>
              <Hsp_evalue>0.004</Hsp_evalue>
              <Hsp_query-from>10</Hsp_query-from>
              <Hsp_query-to>40</Hsp_query-to>
              <Hsp_hit-from>900</Hsp_hit-from>
              <Hsp_hit-to>930</Hsp_hit-to>
              <Hsp_identity>28</Hsp_identity>
              <Hsp_gaps>1</Hsp_gaps>
              <Hsp_align-len>31</Hsp_align-len>
            </Hsp>
          </Hit_hsps>
        </Hit>
        <Hit>
          <Hit_num>2</Hit_num>
          <Hit_id>gi|000|ref|XM_001.1|</Hit_id>
          <Hit_def>Pan troglodytes BRCA1, mRNA</Hit_def>
          <Hit_accession>XM_001</Hit_accession>
          <Hit_len>5000</Hit_len>
          <Hit_hsps>
            <Hsp>
              <Hsp_num>1</Hsp_num>
              <Hsp_bit-score>500.1</Hsp_bit-score>
              <Hsp_score>280</Hsp_score>
              <Hsp_evalue>1e-140</Hsp_evalue>
              <Hsp_query-from>1</Hsp_query-from>
              <Hsp_query-to>299</Hsp_query-to>
              <Hsp_hit-from>1</Hsp_hit-from>
              <Hsp_hit-to>299</Hsp_hit-to>
              <Hsp_identity>290</Hsp_identity>
              <Hsp_gaps>0</Hsp_gaps>
              <Hsp_align-len>299</Hsp_align-len>
            </Hsp>
          </Hit_hsps>
        </Hit>
      </Iteration_hits>
      <Iteration_stat>
        <Statistics>
          <Statistics_db-num>100</Statistics_db-num>
          <Statistics_db-len>500000</Statistics_db-len>
          <Statistics_eff-space>1200000</Statistics_eff-space>
        </Statistics>
      </Iteration_stat>
    </Iteration>
  </BlastOutput_iterations>
</BlastOutput>
"""


# --- pure XML parsing -----------------------------------------------------------------------


def test_parse_blast_xml_flattens_hits_and_best_hsp():
    res = parse_blast_xml(BLAST_XML, rid="ZK9A1B2C014")
    assert isinstance(res, BlastResult)
    assert res.rid == "ZK9A1B2C014" and res.program == "blastn" and res.database == "nt"
    assert res.query_id == "Query_1" and res.query_def == "my query" and res.query_len == 300
    assert res.version.startswith("BLASTN") and res.hit_count == 2

    top = res.hits[0]
    assert top.hit_num == 1 and top.accession == "NM_007294" and top.length == 7088
    assert top.title.startswith("Homo sapiens BRCA1")
    assert top.bit_score == pytest.approx(554.517) and top.evalue == pytest.approx(1.5e-155)
    assert top.identity == 300 and top.align_len == 300 and top.gaps == 0
    assert top.percent_identity == pytest.approx(100.0)
    assert top.query_from == 1 and top.query_to == 300 and top.hit_from == 101 and top.hit_to == 400
    assert top.hsp_count == 2  # the best HSP is summarized, all are counted
    assert res.hits[1].percent_identity == pytest.approx(290 / 299 * 100, rel=1e-6)
    assert res.stats["db_num"] == 100

    df = res.hits_df()
    assert isinstance(df, pd.DataFrame) and list(df.index) == ["NM_007294", "XM_001"]
    assert df.index.name == "accession" and df.loc["NM_007294", "bit_score"] == pytest.approx(
        554.517
    )
    d = res.to_dict()
    assert d["hit_count"] == 2 and d["hits"][0]["accession"] == "NM_007294"
    assert "BRCA1" in res.to_markdown() and "e-155" in res.to_markdown().lower()


def test_parse_blast_xml_without_hits_and_with_garbage():
    empty = parse_blast_xml(
        BLAST_XML.replace(BLAST_XML[BLAST_XML.index("<Hit>") : BLAST_XML.rindex("</Hit>") + 6], "")
    )
    assert empty.hit_count == 0 and empty.hits == [] and empty.hits_df().empty
    with pytest.raises(DataError, match="(?i)blast"):
        parse_blast_xml("<html>Server error</html>")


# --- job lifecycle against a local stand-in --------------------------------------------------


@pytest.fixture
def blast_api(local_http, monkeypatch):
    monkeypatch.setattr(Config, "NCBI_BLAST_URL", local_http.base_url + "/Blast.cgi")
    state = {"searchinfo_calls": 0, "deleted": [], "put_params": None}

    def route(query: dict[str, str]):
        cmd = query.get("CMD", "")
        if cmd == "Put":
            state["put_params"] = query
            return PUT_HTML.encode(), "text/html"
        if cmd == "Delete":
            state["deleted"].append(query.get("RID", ""))
            return b"deleted", "text/html"
        if query.get("FORMAT_OBJECT") == "SearchInfo":
            state["searchinfo_calls"] += 1
            if query.get("RID") == "EXPIRED":
                return UNKNOWN.encode(), "text/html"
            if query.get("RID") == "NOHITS":
                return READY_NO_HITS.encode(), "text/html"
            if query.get("RID") == "READYNOW":
                return READY.encode(), "text/html"
            body = WAITING if state["searchinfo_calls"] < 2 else READY
            return body.encode(), "text/html"
        return BLAST_XML.encode(), "application/xml"

    local_http.add_dynamic("/Blast.cgi", route)
    local_http.state = state  # type: ignore[attr-defined]
    return local_http


def test_submit_blast_returns_a_job_with_rid_and_estimate(blast_api):
    job = submit_blast(
        "ATGCATGCATGC", program="blastn", database="nt", hitlist_size=10, evalue=1e-5
    )
    assert isinstance(job, BlastJob)
    assert job.rid == "ZK9A1B2C014" and job.estimated_seconds == 27
    assert job.program == "blastn" and job.database == "nt" and job.status == "waiting"
    assert job.submitted_at.endswith("Z") and "ZK9A1B2C014" in job.url

    sent = blast_api.state["put_params"]
    assert sent["PROGRAM"] == "blastn" and sent["DATABASE"] == "nt" and sent["CMD"] == "Put"
    assert (
        sent["QUERY"] == "ATGCATGCATGC"
        and sent["HITLIST_SIZE"] == "10"
        and sent["EXPECT"] == "1e-05"
    )
    assert sent["MEGABLAST"] == "on"  # blastn defaults to megablast
    assert job.to_dict()["rid"] == "ZK9A1B2C014"


def test_submit_blast_validates_program_and_reads_a_query_file(blast_api, tmp_path):
    with pytest.raises(ValueError, match="(?i)program"):
        submit_blast("ATGC", program="nosuchblast")
    fasta = tmp_path / "q.fasta"
    fasta.write_text(">q1\nATGCATGC\n")
    submit_blast(fasta, program="blastp")
    sent = blast_api.state["put_params"]
    assert sent["QUERY"].startswith(">q1") and sent["PROGRAM"] == "blastp"
    assert "MEGABLAST" not in sent  # only meaningful for blastn
    assert BLAST_PROGRAMS["blastp"] == "nr"  # documented default database per program


# NCBI's real READY response splits Status and ThereAreHits across TWO QBlastInfo blocks,
# wrapped in the HTML status page (observed live 2026-10-22).
REAL_READY_PAGE = """<html><body><table>
                   Format Request Status
                QBlastInfoBegin
\t                Status=READY
                QBlastInfoEnd
QBlastInfoBegin
\tThereAreHits=yes
QBlastInfoEnd
                <tr class="odd"><td>Status</td><td>Searching</td></tr>
</table></body></html>
"""


def test_qblast_info_merges_every_block_in_the_response():
    from PyNCBI.domains.blast_search import _parse_qblast_info

    fields = _parse_qblast_info(REAL_READY_PAGE)
    assert fields["Status"] == "READY"
    assert fields["ThereAreHits"] == "yes"


def test_check_blast_handles_the_real_two_block_ready_page(local_http, monkeypatch):
    monkeypatch.setattr(Config, "NCBI_BLAST_URL", local_http.base_url + "/Blast.cgi")
    local_http.add_dynamic("/Blast.cgi", lambda: (REAL_READY_PAGE.encode(), "text/html"))
    job = check_blast("ABC123")
    assert job.status == "ready" and job.there_are_hits is True


def test_check_blast_reports_status_transitions(blast_api):
    first = check_blast("ZK9A1B2C014")
    assert first.status == "waiting" and first.there_are_hits is None
    second = check_blast("ZK9A1B2C014")
    assert second.status == "ready" and second.there_are_hits is True
    assert check_blast("EXPIRED").status == "unknown"
    assert check_blast("NOHITS").there_are_hits is False


def test_fetch_blast_parses_results_and_delete_removes_the_job(blast_api):
    res = fetch_blast("ZK9A1B2C014")
    assert res.rid == "ZK9A1B2C014" and res.hit_count == 2
    assert delete_blast("ZK9A1B2C014") is True
    assert blast_api.state["deleted"] == ["ZK9A1B2C014"]


def test_blast_blocks_until_ready_then_returns_results(blast_api, tmp_path):
    res = blast("ATGCATGCATGC", poll_interval=0, timeout=30, save_to=tmp_path / "hits.csv")
    assert res.hit_count == 2 and res.rid == "ZK9A1B2C014"
    assert blast_api.state["searchinfo_calls"] >= 2  # polled through WAITING
    assert (tmp_path / "hits.csv").exists() and "NM_007294" in (tmp_path / "hits.csv").read_text()
    assert res.saved_path is not None


def test_blast_can_return_the_job_without_waiting(blast_api):
    job = blast("ATGCATGCATGC", wait=False)
    assert isinstance(job, BlastJob) and job.rid == "ZK9A1B2C014"
    assert blast_api.state["searchinfo_calls"] == 0


def test_blast_timeout_raises_with_the_rid_so_work_is_not_lost(blast_api, monkeypatch):
    monkeypatch.setattr("PyNCBI.domains.blast_search._status_of", lambda *a, **k: ("waiting", None))
    with pytest.raises(TimeoutError, match="ZK9A1B2C014"):
        blast("ATGC", poll_interval=0, timeout=0.05)


def test_blast_reports_expired_and_hitless_jobs_clearly(blast_api, monkeypatch):
    monkeypatch.setattr("PyNCBI.domains.blast_search._status_of", lambda *a, **k: ("unknown", None))
    with pytest.raises(DataError, match="(?i)expired|unknown"):
        blast("ATGC", poll_interval=0, timeout=5)
    monkeypatch.setattr("PyNCBI.domains.blast_search._status_of", lambda *a, **k: ("ready", False))
    with pytest.raises(NoDataAvailableError, match="(?i)no hits"):
        blast("ATGC", poll_interval=0, timeout=5)


def test_polling_is_throttled_only_against_real_ncbi_hosts():
    from PyNCBI.domains.blast_search import _poll_floor

    assert _poll_floor("https://blast.ncbi.nlm.nih.gov/Blast.cgi") >= 10.0
    assert _poll_floor("http://127.0.0.1:8000/Blast.cgi") == 0.0


def test_blast_realm_is_rate_limited_conservatively():
    from PyNCBI.io.rate_limiter import NCBIRealmLimiter, classify_ncbi_realm

    assert classify_ncbi_realm("https://blast.ncbi.nlm.nih.gov/Blast.cgi") == "blast"
    stats = NCBIRealmLimiter().stats()
    assert "blast" in stats and stats["blast"]["rate_per_sec"] <= 0.5


# --- CLI & MCP --------------------------------------------------------------------------------


def test_cli_blast_submit_and_fetch(blast_api, tmp_path, capsys):
    rc = cli_main(["blast", "ATGCATGCATGC", "--no-wait", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["rid"] == "ZK9A1B2C014" and payload["data"]["status"] == "waiting"
    assert any("ZK9A1B2C014" in step for step in payload["next_steps"])

    # --rid checks status first and reports a job that is still running instead of fetching
    rc = cli_main(["blast", "--rid", "ZK9A1B2C014", "--json"])
    assert rc == 0
    waiting = json.loads(capsys.readouterr().out)
    assert waiting["data"]["status"] == "waiting" and "poll" in waiting["data"]["message"].lower()

    rc = cli_main(["blast", "--rid", "READYNOW", "--json", "-o", str(tmp_path / "h.csv")])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["hit_count"] == 2
    assert payload["data"]["hits"][0]["accession"] == "NM_007294"
    assert (tmp_path / "h.csv").exists()

    rc = cli_main(["blast", "ATGCATGCATGC", "--poll-interval", "0", "--markdown"])
    out = capsys.readouterr().out
    assert rc == 0 and "BRCA1" in out


def test_mcp_blast_tool(blast_api):
    out = json.loads(execute_mcp_tool("ncbi_blast", {"query": "ATGCATGCATGC", "wait": False}))
    assert out["data"]["rid"] == "ZK9A1B2C014"
    waiting = json.loads(execute_mcp_tool("ncbi_blast", {"rid": "ZK9A1B2C014"}))
    assert waiting["data"]["status"] == "waiting"
    assert any("ZK9A1B2C014" in step for step in waiting["next_steps"])
    done = json.loads(execute_mcp_tool("ncbi_blast", {"rid": "READYNOW"}))
    assert done["data"]["hit_count"] == 2
    tools = {t["name"] for t in __import__("PyNCBI.mcp.server", fromlist=["MCP_TOOLS"]).MCP_TOOLS}
    assert "ncbi_blast" in tools
