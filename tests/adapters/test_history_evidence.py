from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from github_issue_worker.adapters.history_evidence import HistoryEvidence
from github_issue_worker.application.history import RunRecord, merged_history

T0 = datetime(2026, 10, 31, 11, tzinfo=UTC)
QUOTA = {"event": "result", "result": {"status": "ERROR", "error": "Individual quota reached."}}


def record(worker: str = "antigravity", agent: str = "antigravity") -> RunRecord:
    return RunRecord(
        "o/r",
        78,
        worker,
        agent,
        "Model",
        "first",
        "paused",
        T0,
        T0 + timedelta(minutes=20),
        T0,
        "m",
    )


def write_log(root: Path, stamp: str, worker: str, *events: dict[str, object]) -> None:
    folder = root / "o/r/78"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stamp}-{worker}.ndjson").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


def test_real_work_before_a_legacy_quota_pause_is_restored_from_its_transcripts(
    tmp_path: Path,
) -> None:
    write_log(
        tmp_path,
        "20261031T110230",
        "antigravity",
        {"event": "step_update", "step_update": {"step_type": "tool", "state": "DONE"}},
        QUOTA,
    )
    (restored,) = HistoryEvidence(tmp_path, "m").restore([record()])
    assert restored.active is True and restored.is_work
    assert restored.started == T0 and restored.finished == record().finished


def test_only_a_proven_quota_only_reply_is_excluded(tmp_path: Path) -> None:
    write_log(tmp_path, "20261031T110230", "antigravity", QUOTA)
    (restored,) = HistoryEvidence(tmp_path, "m").restore([record()])
    assert restored.active is False and not restored.is_work


def test_missing_or_incomplete_transcripts_do_not_erase_legacy_activity(tmp_path: Path) -> None:
    evidence = HistoryEvidence(tmp_path, "m")
    (unknown,) = evidence.restore([record()])
    assert unknown.active is None and unknown.is_work
    write_log(
        tmp_path, "20261031T110230", "antigravity", {"event": "init", "conversation_id": "session"}
    )
    assert evidence.restore([record()])[0].active is None


def test_evidence_is_matched_to_the_worker_time_and_manager(tmp_path: Path) -> None:
    write_log(tmp_path, "20261031T110230", "antigravity.2", QUOTA)
    write_log(tmp_path, "20261031T120230", "antigravity", QUOTA)
    assert HistoryEvidence(tmp_path, "m").restore([record()])[0].active is None
    write_log(tmp_path, "20261031T110230", "antigravity", QUOTA)
    foreign = replace(record(), manager="another-manager")
    assert HistoryEvidence(tmp_path, "m").restore([foreign])[0].active is None


def test_codex_work_before_its_limit_is_restored(tmp_path: Path) -> None:
    write_log(
        tmp_path,
        "20261031T110230",
        "codex",
        {"type": "item.completed", "item": {"type": "command_execution", "command": "test"}},
        {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}},
    )
    assert HistoryEvidence(tmp_path, "m").restore([record("codex", "codex")])[0].is_work


def test_local_evidence_is_not_lost_when_an_older_shared_record_wins_the_merge() -> None:
    local = replace(record(), active=True)
    (merged,) = merged_history([local], [record()])
    assert merged.active is True
    unshared = replace(local, issue=79)
    assert len(merged_history(iter([local, unshared]), [record()])) == 2
