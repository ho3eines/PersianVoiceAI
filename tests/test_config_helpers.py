"""Direct tests for small public helpers that the rest of the suite only reaches indirectly."""

from __future__ import annotations

import pytest

from server import config
from server.audio_io import StreamPlan


@pytest.fixture(autouse=True)
def clean_env_issues():
    del config.ENV_ISSUES[:]
    yield
    del config.ENV_ISSUES[:]


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.8.9.10", "localhost", "::1", "[::1]", "0:0:0:0:0:0:0:1", "", None, "  LOCALHOST "],
)
def test_loopback_hosts_are_recognised(host):
    assert config.is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "10.0.0.5", "example.com"])
def test_lan_and_wildcard_binds_are_not_loopback(host):
    assert config.is_loopback_host(host) is False


def test_device_tokens_parse_and_keep_colons_inside_the_token():
    tokens = config.parse_device_tokens("esp32-kitchen:tokenA, esp32-study:to:kenB")
    assert tokens == {"tokenA": "esp32-kitchen", "to:kenB": "esp32-study"}
    assert config.ENV_ISSUES == []


def test_empty_device_token_spec_is_silent():
    assert config.parse_device_tokens("") == {}
    assert config.parse_device_tokens(" , ,") == {}
    assert config.ENV_ISSUES == []


def test_malformed_device_token_entries_are_reported_not_dropped_silently():
    tokens = config.parse_device_tokens("no-colon-here,:tok,dev:,good:ok")
    assert tokens == {"ok": "good"}
    reasons = [reason for name, reason in config.ENV_ISSUES if name == "PVAI_WS_TOKENS"]
    assert len(reasons) == 3


def test_duplicate_device_token_is_reported_and_first_wins():
    tokens = config.parse_device_tokens("first:same,second:same")
    assert tokens == {"same": "first"}
    assert any("duplicate" in reason for _, reason in config.ENV_ISSUES)


def test_stream_plan_passes_device_pair_only_when_a_device_is_chosen():
    callback = lambda *args, **kwargs: None  # noqa: E731
    plan = StreamPlan(
        samplerate=16000,
        blocksize=320,
        input_device=None,
        output_device=None,
        input_name="default",
        output_name="default",
    )
    params = plan.parameters(callback)
    assert params == {
        "samplerate": 16000,
        "blocksize": 320,
        "channels": 1,
        "dtype": "int16",
        "callback": callback,
    }
    assert "device" not in params

    pinned = StreamPlan(16000, 320, 3, None, "mic", "default").parameters(callback)
    assert pinned["device"] == (3, None)
