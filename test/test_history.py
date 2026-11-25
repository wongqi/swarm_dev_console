"""Allocation revisions survive replay, out-of-order arrival, eviction and restart."""

import json
import stat

from swarm_dev_console.history import PlanHistory


def plan(epoch=1795513654008, revision=1, **fields):
    return {"mission_id": str(epoch), "round_epoch": epoch, "plan_revision": revision,
            "map_package": "/board/maps/company2", "commit_index": revision * 10,
            "routes": {"A": {"robot_id": "A", "task_ids": ["original-id"],
                             "checkpoints": [{"type": "task", "task_ids": ["original-id"],
                                              "location": {"frame_id": "company2", "pos": [1, 2, 0]}}]}},
            **fields}


def test_revision_history_is_immutable_sorted_and_private(tmp_path):
    path = tmp_path / "plans.sqlite3"
    history = PlanHistory(path)
    p2, p1 = plan(revision=2), plan(storage={"secretKey": "never-save"})
    assert history.record(p2, 20)
    assert history.record(p1, 10)
    assert not history.record(plan(revision=1, commit_index=90, completed_tasks=7), 90)
    assert [p["plan_revision"] for p in history.index(p1["round_epoch"])] == [1, 2]
    assert history.version == 2
    assert history.get(p1["round_epoch"], 1)["commit_index"] == 10
    assert history.index(p1["round_epoch"])[0]["received_at"] == 10
    assert "never-save" not in json.dumps(history.get(p1["round_epoch"], 1))
    assert history.rounds()[0]["revisions"] == 2
    assert history.rounds()[0]["assigned_count"] == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    history.close()
    restored = PlanHistory(path)
    try:
        assert restored.version == 2
        assert restored.latest() == p2
        assert restored.get(p1["round_epoch"], 3) is None
        assert restored.get(p1["round_epoch"], 1)["routes"]["A"]["task_ids"] == ["original-id"]
    finally:
        restored.close()


def test_rounds_and_revisions_do_not_replace_each_other(tmp_path):
    history = PlanHistory(tmp_path / "plans.sqlite3")
    try:
        for epoch in [1795513654009, 1795513654008]:
            for revision in [3, 1, 2]:
                assert history.record(plan(epoch, revision), epoch)
        assert [p["round_epoch"] for p in history.rounds()] == [1795513654009, 1795513654008]
        assert history.latest()["round_epoch"] == 1795513654009
        assert history.latest()["plan_revision"] == 3
        assert not history.record(plan(revision=0), 1)
        assert not history.record(plan(routes=None), 1)
        assert not history.record(plan(epoch=False), 1)
        assert history.version == 6
    finally:
        history.close()
