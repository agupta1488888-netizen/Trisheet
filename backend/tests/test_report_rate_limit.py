"""Tests for the report creation rate limiter.

`POST /reports` has no authentication and a report costs dozens of
rate-limited EDGAR reads and several model calls. The EDGAR token bucket
already stops the SEC being hammered, but it does so by making every
concurrent caller wait — so an unbounded caller degrades the service for
everyone else while spending model budget doing it.

The behaviour worth pinning down is the refusal path: a refused attempt must
not itself count, or a caller who keeps retrying is locked out for longer the
harder they try.

Time is injected rather than slept through, so the window's boundary is
asserted on exactly instead of approached with a real clock.
"""

from __future__ import annotations

import pytest

from app.main import _ReportRateLimiter


class _Clock:
    """A hand-wound clock, in seconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _limiter(
    *, max_runs: int = 3, window_seconds: float = 600.0, max_callers: int = 8
) -> tuple[_ReportRateLimiter, _Clock]:
    clock = _Clock()
    limiter = _ReportRateLimiter(
        max_runs=max_runs,
        window_seconds=window_seconds,
        max_callers=max_callers,
        clock=clock,
    )
    return limiter, clock


def test_a_caller_may_start_up_to_the_limit() -> None:
    limiter, _ = _limiter(max_runs=3)

    assert [limiter.allow("1.2.3.4") for _ in range(3)] == [True, True, True]


def test_the_next_report_past_the_limit_is_refused() -> None:
    limiter, _ = _limiter(max_runs=3)
    for _ in range(3):
        limiter.allow("1.2.3.4")

    assert limiter.allow("1.2.3.4") is False


def test_a_refused_attempt_does_not_extend_the_window() -> None:
    # The trap this guards: recording refusals would mean a caller who keeps
    # retrying pushes their own window forward and stays locked out longer
    # the harder they try. After the window passes they must be free again,
    # however many times they were refused inside it.
    limiter, clock = _limiter(max_runs=3, window_seconds=600.0)
    for _ in range(3):
        limiter.allow("1.2.3.4")

    clock.advance(300.0)
    for _ in range(10):
        assert limiter.allow("1.2.3.4") is False

    # 601s after the three accepted runs, and not one second later for the
    # ten refusals that followed them.
    clock.advance(301.0)
    assert limiter.allow("1.2.3.4") is True


def test_callers_are_counted_separately() -> None:
    limiter, _ = _limiter(max_runs=2)
    for _ in range(2):
        limiter.allow("1.2.3.4")

    assert limiter.allow("1.2.3.4") is False
    assert limiter.allow("5.6.7.8") is True


def test_a_run_still_counts_at_the_window_boundary() -> None:
    # Exactly at the window is still inside it; strictly past it is not.
    limiter, clock = _limiter(max_runs=1, window_seconds=600.0)
    assert limiter.allow("1.2.3.4") is True

    clock.advance(600.0)
    assert limiter.allow("1.2.3.4") is False

    clock.advance(0.1)
    assert limiter.allow("1.2.3.4") is True


def test_tracked_callers_are_bounded() -> None:
    # Otherwise the limiter is itself a memory leak with an unbounded key
    # space, which is a worse problem than the one it solves.
    limiter, _ = _limiter(max_runs=1, max_callers=4)

    for index in range(50):
        limiter.allow(f"10.0.0.{index}")

    assert len(limiter._seen) <= 4


def test_eviction_drops_the_least_recently_seen_caller() -> None:
    limiter, _ = _limiter(max_runs=1, max_callers=2)
    limiter.allow("first")
    limiter.allow("second")
    # "first" is now the least recently seen, so it is the one evicted.
    limiter.allow("third")

    assert "first" not in limiter._seen
    assert "third" in limiter._seen


# --- The endpoint ------------------------------------------------------------


def test_the_endpoint_refuses_past_the_limit_in_the_interface_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An end-to-end check that the limiter is actually wired to the route,
    # and that what a reader sees is a stated reason rather than a bare 429.
    #
    # The pipeline is stubbed out. The endpoint queues a real background run,
    # and ten of those would mean ten sets of live EDGAR reads inside a unit
    # test — slow, network-dependent, and rude to the SEC. What is under test
    # is the refusal, not the run behind it.
    from fastapi.testclient import TestClient

    from app import main as main_module
    from app.config import REPORT_RATE_LIMIT_MAX_RUNS, Settings
    from app.main import create_app

    async def _no_run(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(main_module, "run_pipeline", _no_run)

    settings = Settings(
        environment="development",
        edgar_contact_email="tests@example.com",
        cors_allowed_origins="http://localhost:3000",
    )
    payload = {"ticker": "NKE", "cik": "0000320187", "depth": "brief"}

    with TestClient(create_app(settings)) as client:
        accepted = [
            client.post("/reports", json=payload).status_code
            for _ in range(REPORT_RATE_LIMIT_MAX_RUNS)
        ]
        refused = client.post("/reports", json=payload)

    assert all(status == 202 for status in accepted)
    assert refused.status_code == 429
    body = refused.json()
    assert body["code"] == "report_rate_limited"
    assert "Try again shortly." in body["message"]
