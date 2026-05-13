"""Tests for the cert_checker scheduler job.

Previously this module owned a daemon thread; now it exports a single
per-tick function (`check_certs_once`) that the scheduler calls. We test
the tick body directly -- no asyncio, no threads, no sleep."""

from unittest.mock import patch


def test_tick_calls_get_certs():
    """One tick: `get_certs()` must be invoked exactly once."""
    import services.cert_checker as cc
    with patch.object(cc, "get_certs") as mock_get:
        cc.check_certs_once()
    mock_get.assert_called_once()


def test_tick_swallows_errors(capsys):
    """If `get_certs()` raises, the tick must log and return -- never
    propagate the error up to the scheduler (which would count the job
    failed and rely on listeners for visibility)."""
    import services.cert_checker as cc
    with patch.object(cc, "get_certs", side_effect=RuntimeError("sim failure")):
        # Must NOT raise.
        cc.check_certs_once()
    out = capsys.readouterr().out
    assert "[cert-check] error" in out
    assert "sim failure" in out


def test_interval_constant_exported():
    """The scheduler registration in server.py reads this constant -- if it
    gets renamed, server.py would silently schedule at the wrong cadence."""
    import services.cert_checker as cc
    assert cc.CERT_CHECK_INTERVAL_SECONDS == 300
