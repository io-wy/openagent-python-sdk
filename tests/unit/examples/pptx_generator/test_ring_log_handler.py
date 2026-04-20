"""Tests for :class:`examples.pptx_generator.wizard._layout.RingLogHandler`."""

from __future__ import annotations

import logging

from examples.pptx_generator.wizard._layout import LogRing, RingLogHandler


def _fresh_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.propagate = False
    return logger


def test_ring_log_handler_appends_formatted_message() -> None:
    ring = LogRing(max_lines=5)
    handler = RingLogHandler(ring)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = _fresh_logger("pptx.test.append")
    logger.addHandler(handler)

    logger.info("hello")

    assert ring.snapshot() == ["hello"]


def test_ring_log_handler_truncates_to_max_lines() -> None:
    ring = LogRing(max_lines=3)
    handler = RingLogHandler(ring)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = _fresh_logger("pptx.test.truncate")
    logger.addHandler(handler)

    for i in range(10):
        logger.info("line-%d", i)

    snap = ring.snapshot()
    assert len(snap) == 3
    assert snap == ["line-7", "line-8", "line-9"]


def test_ring_log_handler_detaches_without_leaking() -> None:
    ring = LogRing(max_lines=5)
    handler = RingLogHandler(ring)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = _fresh_logger("pptx.test.detach")
    logger.addHandler(handler)

    logger.info("before")
    logger.removeHandler(handler)
    logger.info("after")

    assert ring.snapshot() == ["before"]
    assert handler not in logger.handlers
