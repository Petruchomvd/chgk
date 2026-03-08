from types import SimpleNamespace

import classifier.runner as runner


class DummyConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _provider(max_concurrent: int):
    cfg = SimpleNamespace(
        model="test-model",
        name="test-provider",
        max_concurrent=max_concurrent,
    )
    return SimpleNamespace(config=cfg)


def test_run_classification_uses_cross_thread_connection_for_parallel_workers(monkeypatch):
    dummy_conn = DummyConn()
    captured = {}

    def fake_get_connection(_db_path, check_same_thread=True):
        captured["check_same_thread"] = check_same_thread
        return dummy_conn

    monkeypatch.setattr(runner, "get_connection", fake_get_connection)
    monkeypatch.setattr(runner, "get_unclassified_questions", lambda *args, **kwargs: [])
    monkeypatch.setattr(runner, "get_question_count", lambda *args, **kwargs: 0)

    runner.run_classification(provider=_provider(max_concurrent=8), workers=4)

    assert captured["check_same_thread"] is False
    assert dummy_conn.closed is True


def test_run_classification_keeps_default_connection_mode_for_single_worker(monkeypatch):
    dummy_conn = DummyConn()
    captured = {}

    def fake_get_connection(_db_path, check_same_thread=True):
        captured["check_same_thread"] = check_same_thread
        return dummy_conn

    monkeypatch.setattr(runner, "get_connection", fake_get_connection)
    monkeypatch.setattr(runner, "get_unclassified_questions", lambda *args, **kwargs: [])
    monkeypatch.setattr(runner, "get_question_count", lambda *args, **kwargs: 0)

    runner.run_classification(provider=_provider(max_concurrent=1), workers=4)

    assert captured["check_same_thread"] is True
    assert dummy_conn.closed is True
