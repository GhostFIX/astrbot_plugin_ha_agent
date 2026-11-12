"""日志缓冲：来源过滤、去重、容量、级别过滤与挂载。"""

from __future__ import annotations

import logging

from core.logs import LogBuffer, matches_plugin

PLUGIN_PATH = "/app/data/plugins/astrbot_plugin_ha_agent/main.py"


def _record(
    message: object = "hello",
    *,
    name: str = "astrbot",
    pathname: str = PLUGIN_PATH,
    level: int = logging.INFO,
) -> logging.LogRecord:
    return logging.LogRecord(name, level, pathname, 10, message, None, None)


def test_matches_by_source_path():
    assert matches_plugin(_record(pathname=PLUGIN_PATH, name="astrbot")) is True


def test_matches_by_logger_name():
    record = _record(name="astrbot_plugin_ha_agent.main", pathname="/x/y.py")
    assert matches_plugin(record) is True


def test_matches_by_message_text():
    record = _record(
        message="astrbot_plugin_ha_agent: 出站已就绪", name="astrbot", pathname="/x/y.py"
    )
    assert matches_plugin(record) is True


def test_ignores_unrelated_records():
    record = _record(message="other plugin", name="astrbot", pathname="/app/other/main.py")
    assert matches_plugin(record) is False


def test_emit_captures_plugin_record():
    buffer = LogBuffer()
    buffer.handle(_record())
    entries = buffer.snapshot()

    assert len(entries) == 1
    assert entries[0]["message"] == "hello"
    assert entries[0]["level"] == "INFO"


def test_same_record_is_captured_once():
    buffer = LogBuffer()
    record = _record()
    buffer.handle(record)
    buffer.handle(record)  # 同一条记录经过多个日志器时只收一次

    assert len(buffer.snapshot()) == 1


def test_unrelated_record_is_dropped():
    buffer = LogBuffer()
    buffer.handle(_record(message="nope", pathname="/app/other/main.py"))
    assert buffer.snapshot() == []


def test_capacity_is_enforced():
    buffer = LogBuffer(capacity=50)
    for index in range(60):
        buffer.record("INFO", f"line {index}")

    entries = buffer.snapshot(limit=100)
    assert len(entries) == 50
    assert entries[-1]["message"] == "line 59"


def test_level_filter_and_limit():
    buffer = LogBuffer()
    buffer.record("INFO", "info-line")
    buffer.record("ERROR", "error-line")

    only_errors = buffer.snapshot(min_level="ERROR")
    assert [item["message"] for item in only_errors] == ["error-line"]

    assert len(buffer.snapshot(limit=1)) == 1


def test_clear_empties_buffer():
    buffer = LogBuffer()
    buffer.record("INFO", "x")
    buffer.clear()
    assert buffer.snapshot() == []
    assert buffer.stats()["count"] == 0


def test_attach_captures_and_detach_stops():
    buffer = LogBuffer()
    logger = logging.getLogger("astrbot_plugin_ha_agent.test")
    logger.setLevel(logging.DEBUG)
    original_propagate = logger.propagate
    logger.propagate = False
    try:
        names = buffer.attach([logger])
        assert names == ["astrbot_plugin_ha_agent.test"]

        logger.info("from logger")
        assert buffer.snapshot()[-1]["message"] == "from logger"

        buffer.detach()
        logger.info("after detach")
        assert all("after detach" not in item["message"] for item in buffer.snapshot())
    finally:
        logger.propagate = original_propagate
        logger.handlers.clear()


def test_attach_ignores_objects_without_add_handler():
    buffer = LogBuffer()

    class NotALogger:
        pass

    assert buffer.attach([NotALogger(), None]) == []


def test_stats_reports_capacity_and_count():
    buffer = LogBuffer()
    buffer.record("INFO", "x")
    stats = buffer.stats()

    assert stats["capacity"] == 400
    assert stats["count"] == 1
    assert isinstance(stats["attached"], list)
