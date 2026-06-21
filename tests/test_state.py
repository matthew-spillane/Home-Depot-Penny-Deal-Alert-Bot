"""Tests for the SQLite dedup/state layer."""
from bot.state import State


def test_seen_dedup(tmp_path):
    st = State(str(tmp_path / "t.sqlite3"))
    assert not st.is_seen("t3_abc")
    st.mark_seen("t3_abc")
    assert st.is_seen("t3_abc")
    # idempotent
    st.mark_seen("t3_abc")
    assert st.is_seen("t3_abc")
    st.close()


def test_alert_history(tmp_path):
    st = State(str(tmp_path / "t.sqlite3"))
    assert not st.already_alerted_today("1004567890", "2667")
    st.record_alert("1004567890", "2667")
    assert st.already_alerted_today("1004567890", "2667")
    # different store -> independent
    assert not st.already_alerted_today("1004567890", "2660")
    st.close()
