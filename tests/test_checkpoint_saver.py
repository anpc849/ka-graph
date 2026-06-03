from __future__ import annotations

from kagraph.checkpoint import InMemorySaver


def test_in_memory_saver_returns_none_for_missing_key_or_checkpoint_id():
    saver = InMemorySaver()

    assert saver.get("missing-thread") is None
    assert saver.get("missing-thread", "missing-checkpoint") is None
    assert saver.list("missing-thread") == []


def test_in_memory_saver_put_get_and_list_latest_checkpoint():
    saver = InMemorySaver()

    first = saver.put("thread-1", {"state": {"step": 1}})
    second = saver.put("thread-1", {"state": {"step": 2}})

    assert first["checkpoint_id"]
    assert second["checkpoint_id"]
    assert first["checkpoint_id"] != second["checkpoint_id"]
    assert second["parent_checkpoint_id"] == first["checkpoint_id"]

    assert saver.get("thread-1") == second
    assert saver.get("thread-1", first["checkpoint_id"]) == first
    assert saver.get("thread-1", second["checkpoint_id"]) == second
    assert saver.list("thread-1") == [first, second]


def test_in_memory_saver_keeps_threads_isolated():
    saver = InMemorySaver()

    thread_a = saver.put("thread-a", {"state": {"value": "a"}})
    thread_b = saver.put("thread-b", {"state": {"value": "b"}})

    assert saver.get("thread-a") == thread_a
    assert saver.get("thread-b") == thread_b
    assert saver.get("thread-a") != saver.get("thread-b")
    assert saver.list("thread-a") == [thread_a]
    assert saver.list("thread-b") == [thread_b]


def test_in_memory_saver_preserves_explicit_checkpoint_metadata():
    saver = InMemorySaver()

    first = saver.put(
        "thread-1",
        {
            "checkpoint_id": "custom-1",
            "parent_checkpoint_id": "root",
            "state": {"step": 1},
        },
    )
    second = saver.put(
        "thread-1",
        {
            "checkpoint_id": "custom-2",
            "parent_checkpoint_id": "manual-parent",
            "state": {"step": 2},
        },
    )

    assert first["checkpoint_id"] == "custom-1"
    assert first["parent_checkpoint_id"] == "root"
    assert second["checkpoint_id"] == "custom-2"
    assert second["parent_checkpoint_id"] == "manual-parent"


def test_in_memory_saver_defensively_copies_inputs_and_outputs():
    saver = InMemorySaver()
    original = {"state": {"items": ["a"], "nested": {"count": 1}}}

    saved = saver.put("thread-1", original)
    original["state"]["items"].append("mutated-input")
    original["state"]["nested"]["count"] = 99

    loaded = saver.get("thread-1")
    assert loaded == saved
    assert loaded["state"]["items"] == ["a"]
    assert loaded["state"]["nested"]["count"] == 1

    loaded["state"]["items"].append("mutated-output")
    listed = saver.list("thread-1")
    listed[0]["state"]["nested"]["count"] = 42

    reloaded = saver.get("thread-1")
    assert reloaded["state"]["items"] == ["a"]
    assert reloaded["state"]["nested"]["count"] == 1
