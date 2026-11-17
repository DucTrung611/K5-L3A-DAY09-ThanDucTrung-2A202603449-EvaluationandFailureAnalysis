"""The investigator receives only a typed, read-only handoff."""

from __future__ import annotations

import json
from pathlib import Path

import lab8.shapes.orchestrator as orchestrator
from lab8.db import connect
from lab8.handoff import ALLOWED_FIELDS, READONLY_TOOLS, WRITE_TOOLS
from lab8.runtime import run_readonly_investigators


def test_orchestrator_handoff_excludes_parent_data_and_write_tools(
    public_db, monkeypatch, tmp_path
):
    conn = connect(public_db)
    try:
        conn.execute(
            "UPDATE claims SET ticket = ?, parent_secret = ?, raw_headers_json = ? WHERE claim_id = ?",
            ("CANARY_SECRET_TICKET", "CANARY_SECRET_PARENT", '{"Authorization":"Bearer secret"}', "C-001"),
        )
        conn.commit()
    finally:
        conn.close()

    received = []

    def inspect_handoff(conn, budget, handoff, principal_id):
        received.append(handoff)
        return run_readonly_investigators(conn, budget, handoff, principal_id)

    monkeypatch.setattr(orchestrator, "run_readonly_investigators", inspect_handoff)
    monkeypatch.chdir(tmp_path)
    result = orchestrator.run_claim("C-001", seed=11, store_path=str(public_db))

    assert result.status == "resolved"
    assert result.proposal_amount == 35000
    assert len(received) == 1
    handoff = received[0]
    assert set(handoff) <= ALLOWED_FIELDS
    assert handoff["facts"] == {}
    assert handoff["principal_scope"] == "owner_order_only"
    assert set(handoff["allowed_tools"]) <= READONLY_TOOLS
    assert not set(handoff["allowed_tools"]) & WRITE_TOOLS

    trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    assert trace["run_id"] == result.run_id
    assert [event["payload"] for event in trace["events"] if event["kind"] == "handoff"] == [handoff]
    trace_text = json.dumps(trace)
    assert "CANARY_SECRET_" not in trace_text
    assert "Bearer secret" not in trace_text
    assert "parent_secret" not in trace_text
    assert "raw_headers" not in trace_text


def test_orchestrator_writes_trace_on_early_return(public_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = orchestrator.run_claim("C-004", seed=11, store_path=str(public_db))
    trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))

    assert result.status == "needs_info"
    assert result.proposal_amount == 0
    assert trace["run_id"] == result.run_id
    assert trace["events"]
