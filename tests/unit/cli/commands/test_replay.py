"""Tests for ``openagents replay``."""

from __future__ import annotations

import json
from pathlib import Path

from openagents.cli.main import main as cli_main


def _write_jsonl(path: Path) -> None:
    path.write_text(
        "\n".join(
            json.dumps(obj)
            for obj in [
                {"schema": 1, "name": "run.started", "payload": {"turn": 1}},
                {"schema": 1, "name": "tool.called", "payload": {"tool_id": "t1", "params": {"query": "q"}}},
                {"schema": 1, "name": "tool.succeeded", "payload": {"tool_id": "t1", "result": {"ok": True}}},
                {"schema": 1, "name": "run.started", "payload": {"turn": 2}},
                {"schema": 1, "name": "llm.called", "payload": {"model": "m1"}},
                {"schema": 1, "name": "llm.succeeded", "payload": {"model": "m1"}},
            ]
        ),
        encoding="utf-8",
    )


def _write_json_array(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {"name": "tool.called", "payload": {"tool_id": "distinctive_tool"}},
                {"name": "tool.succeeded", "payload": {"tool_id": "distinctive_tool", "result": None}},
            ]
        ),
        encoding="utf-8",
    )


def _write_session_artifact(path: Path) -> None:
    """Shape produced by the jsonl_file session backend's transcript array."""
    path.write_text(
        json.dumps(
            {
                "transcript": [
                    {"type": "transcript", "data": {"role": "user", "content": "hi"}},
                    {"type": "artifact", "data": {"name": "summary", "payload": "ok"}},
                ]
            }
        ),
        encoding="utf-8",
    )


def test_replay_missing_file_returns_1(tmp_path, capsys):
    code = cli_main(["replay", str(tmp_path / "nope.jsonl")])
    assert code == 1
    assert "file not found" in capsys.readouterr().err


def test_replay_malformed_file_returns_2(tmp_path, capsys):
    p = tmp_path / "bad.jsonl"
    p.write_text("{this is not json", encoding="utf-8")
    code = cli_main(["replay", str(p)])
    assert code == 2


def test_replay_jsonl_renders_text(tmp_path, capsys):
    p = tmp_path / "transcript.jsonl"
    _write_jsonl(p)
    code = cli_main(["replay", str(p)])
    assert code == 0
    out = capsys.readouterr().out
    assert "t1" in out
    assert "m1" in out


def test_replay_filters_to_single_turn(tmp_path, capsys):
    p = tmp_path / "transcript.jsonl"
    _write_jsonl(p)
    code = cli_main(["replay", str(p), "--turn", "2"])
    assert code == 0
    out = capsys.readouterr().out
    # Turn 2 contains the LLM events, not the tool events.
    assert "m1" in out
    assert "t1" not in out


def test_replay_turn_out_of_range_yields_no_output(tmp_path, capsys):
    p = tmp_path / "transcript.jsonl"
    _write_jsonl(p)
    code = cli_main(["replay", str(p), "--turn", "99"])
    assert code == 0
    out = capsys.readouterr().out
    # Nothing to render; command still succeeds.
    assert "m1" not in out


def test_replay_turn_zero_is_empty(tmp_path, capsys):
    p = tmp_path / "transcript.jsonl"
    _write_jsonl(p)
    code = cli_main(["replay", str(p), "--turn", "0"])
    assert code == 0
    assert "m1" not in capsys.readouterr().out


def test_replay_json_format_is_valid_json(tmp_path, capsys):
    p = tmp_path / "transcript.jsonl"
    _write_jsonl(p)
    code = cli_main(["replay", str(p), "--format", "json"])
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["schema"] == 1
    assert len(data["events"]) == 6


def test_replay_accepts_json_array(tmp_path, capsys):
    p = tmp_path / "transcript.json"
    _write_json_array(p)
    code = cli_main(["replay", str(p)])
    assert code == 0
    out = capsys.readouterr().out
    assert "distinctive_tool" in out


def test_replay_accepts_session_artifact_shape(tmp_path, capsys):
    p = tmp_path / "session.json"
    _write_session_artifact(p)
    code = cli_main(["replay", str(p)])
    assert code == 0
    out = capsys.readouterr().out
    # Transcript entries surface as ``artifact.transcript`` / ``artifact.artifact``.
    assert "artifact.transcript" in out
    assert "artifact.artifact" in out


def test_replay_accepts_events_envelope(tmp_path, capsys):
    p = tmp_path / "wrapped.json"
    p.write_text(
        json.dumps({"events": [{"name": "tool.called", "payload": {"tool_id": "x"}}]}),
        encoding="utf-8",
    )
    code = cli_main(["replay", str(p)])
    assert code == 0
    assert "x" in capsys.readouterr().out


def test_replay_empty_file_produces_no_output(tmp_path, capsys):
    p = tmp_path / "empty.json"
    p.write_text("", encoding="utf-8")
    code = cli_main(["replay", str(p)])
    assert code == 0
    assert capsys.readouterr().out == ""
