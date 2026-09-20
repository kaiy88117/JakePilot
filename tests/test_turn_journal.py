from services.turn_journal import TurnJournal


def _journal(tmp_path):
    return TurnJournal(
        f"sqlite:///{(tmp_path / 'turn-journal.db').as_posix()}"
    )


def test_turn_journal_tracks_safe_lifecycle_and_successful_write(tmp_path):
    journal = _journal(tmp_path)
    journal.begin(
        turn_id="turn-a",
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
    )
    journal.observe(
        "turn-a",
        "tool_finished",
        {"tool": "return.create", "status": "succeeded", "arguments": {"reason": "private"}},
    )
    journal.observe(
        "turn-a",
        "turn_ended",
        {"status": "completed", "delta": "private answer"},
    )
    journal.mark_delivered("turn-a")

    checkpoint = journal.get("turn-a", "session-a")

    assert checkpoint == {
        "turn_id": "turn-a",
        "status": "completed",
        "last_event_type": "turn_ended",
        "delivery_status": "delivered",
        "business_write_succeeded": True,
        "event_count": 2,
    }
    assert "private" not in str(checkpoint)


def test_turn_journal_marks_disconnect_without_losing_write_outcome(tmp_path):
    journal = _journal(tmp_path)
    journal.begin(
        turn_id="turn-b",
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
    )
    journal.observe(
        "turn-b",
        "tool_finished",
        {"tool": "return.create", "status": "succeeded"},
    )

    journal.mark_disconnected("turn-b")

    checkpoint = journal.get("turn-b", "session-a")
    assert checkpoint["status"] == "cancelled"
    assert checkpoint["delivery_status"] == "disconnected"
    assert checkpoint["business_write_succeeded"] is True


def test_turn_journal_requires_matching_session_and_scope(tmp_path):
    journal = _journal(tmp_path)
    journal.begin(
        turn_id="turn-c",
        tenant_id="demo",
        user_id="user-a",
        session_id="session-a",
    )

    assert journal.get("turn-c", "session-b") is None
    assert journal.get("missing", "session-a") is None
