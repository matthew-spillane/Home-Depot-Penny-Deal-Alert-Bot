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


def test_alert_log_and_stats(tmp_path):
    st = State(str(tmp_path / "t.sqlite3"))
    assert st.recent_alerts() == []
    assert st.alert_stats()["total"] == 0

    st.log_alert(kind="mention", sku="312345678", name="A drill",
                 subreddit="r/homedepot", source_url="https://reddit.com/x")
    st.log_alert(kind="hit", sku="1004567890", store_id="2667", name="Glitched",
                 price=0.01, quantity=4, source_url="https://reddit.com/y")

    rows = st.recent_alerts()
    assert len(rows) == 2
    assert rows[0]["kind"] == "hit"          # newest first
    assert rows[0]["price"] == 0.01
    assert rows[1]["sku"] == "312345678"

    stats = st.alert_stats()
    assert stats == {"total": 2, "hits": 1, "mentions": 1,
                     "last_ts": rows[0]["ts"], "stores_watched": 0}
    st.close()


def test_recent_alerts_limit_clamped(tmp_path):
    st = State(str(tmp_path / "t.sqlite3"))
    for i in range(5):
        st.log_alert(kind="mention", sku=f"10045678{i:02d}")
    assert len(st.recent_alerts(limit=2)) == 2
    assert len(st.recent_alerts(limit=0)) == 1   # clamped to >= 1
    st.close()


def test_watched_stores(tmp_path):
    st = State(str(tmp_path / "t.sqlite3"))
    assert st.list_stores() == []
    assert st.add_store("2660") is True
    assert st.add_store("2660") is False        # already present
    assert st.add_store("2671") is True
    assert st.list_stores() == ["2660", "2671"]  # insertion order
    assert st.remove_store("2660") is True
    assert st.remove_store("9999") is False      # not present
    assert st.list_stores() == ["2671"]
    st.close()
