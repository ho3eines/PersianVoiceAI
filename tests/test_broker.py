"""Command Broker phase 1: schema, policy, execution and audit with a FakeDevice only."""

from __future__ import annotations

import asyncio
import json

import pytest

from server.broker import (
    AuditLog,
    BrokerError,
    Command,
    CommandBroker,
    FakeDevice,
    Policy,
    PolicyRule,
)


def _policy() -> Policy:
    return Policy(
        rules=(
            PolicyRule("relay.set", "lamp-board", frozenset({0, 1})),
            PolicyRule("sensor.read", "lamp-board", frozenset({2})),
        )
    )


def _broker(tmp_path, device=None, timeout_s=0.5):
    device = device or FakeDevice("lamp-board")
    audit = AuditLog(tmp_path / "audit.jsonl")
    broker = CommandBroker(_policy(), {device.device_id: device}, audit=audit, timeout_s=timeout_s)
    return broker, device, audit


def _events(audit: AuditLog) -> list[dict]:
    return [json.loads(line) for line in audit.path.read_text(encoding="utf-8").splitlines()]


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "raw, code",
    [
        ("relay.set", "invalid_schema"),
        ({"action": "shell.exec", "device_id": "lamp-board", "channel": 0}, "unknown_action"),
        ({"action": "relay.set", "device_id": "lamp-board", "channel": 0, "value": "on", "pin": 4}, "invalid_schema"),
        ({"action": "relay.set", "device_id": "", "channel": 0, "value": "on"}, "invalid_schema"),
        ({"action": "relay.set", "device_id": "lamp-board", "channel": True, "value": "on"}, "invalid_schema"),
        ({"action": "relay.set", "device_id": "lamp-board", "channel": -1, "value": "on"}, "invalid_schema"),
        ({"action": "relay.set", "device_id": "lamp-board", "channel": 0, "value": "maybe"}, "invalid_schema"),
        ({"action": "relay.set", "device_id": "lamp-board", "channel": 0}, "invalid_schema"),
        ({"action": "sensor.read", "device_id": "lamp-board", "channel": 2, "value": "on"}, "invalid_schema"),
    ],
)
def test_malformed_commands_are_rejected_before_policy(raw, code):
    with pytest.raises(BrokerError) as info:
        Command.from_mapping(raw)
    assert info.value.code == code


def test_a_well_formed_command_is_parsed_and_trimmed():
    command = Command.from_mapping({"action": "relay.set", "device_id": " lamp-board ", "channel": 1, "value": "on"})
    assert command == Command("relay.set", "lamp-board", 1, "on")


def test_policy_refuses_a_channel_or_action_that_was_not_granted(tmp_path):
    broker, device, audit = _broker(tmp_path)
    refused = _run(broker.submit({"action": "relay.set", "device_id": "lamp-board", "channel": 5, "value": "on"}))
    assert refused.status == "refused"
    assert refused.detail == {"code": "not_permitted"}
    assert device.relays == {}
    assert _events(audit)[-1]["event"] == "refused"


def test_unknown_device_is_refused_and_audited(tmp_path):
    broker, _, audit = _broker(tmp_path)
    policy_ok = Policy((PolicyRule("relay.set", "ghost", frozenset({0})),))
    broker.policy = policy_ok
    result = _run(broker.submit({"action": "relay.set", "device_id": "ghost", "channel": 0, "value": "on"}))
    assert result.status == "refused"
    assert result.detail == {"code": "unknown_device"}
    assert _events(audit)[-1]["code"] == "unknown_device"


def test_approved_relay_command_is_executed_and_confirmed(tmp_path):
    broker, device, audit = _broker(tmp_path)
    result = _run(broker.submit({"action": "relay.set", "device_id": "lamp-board", "channel": 0, "value": "on"}))
    assert result.status == "ok"
    assert result.detail == {"channel": 0, "state": "on"}
    assert device.relays == {0: "on"}
    assert [event["event"] for event in _events(audit)] == ["approved", "executed"]


def test_sensor_read_reports_the_device_state_without_changing_it(tmp_path):
    broker, device, _ = _broker(tmp_path)
    device.relays[2] = "on"
    result = _run(broker.submit({"action": "sensor.read", "device_id": "lamp-board", "channel": 2}))
    assert result.status == "ok"
    assert result.detail == {"channel": 2, "state": "on"}
    assert device.relays == {2: "on"}


def test_a_slow_device_is_reported_as_unconfirmed_not_as_success(tmp_path):
    broker, device, audit = _broker(tmp_path, device=FakeDevice("lamp-board", delay_s=5.0), timeout_s=0.05)
    result = _run(broker.submit({"action": "relay.set", "device_id": "lamp-board", "channel": 0, "value": "on"}))
    assert result.status == "unconfirmed"
    assert "تأیید نشد" in result.message
    assert _events(audit)[-1]["event"] == "timeout"


class _CrashingDevice:
    device_id = "lamp-board"

    async def execute(self, command):
        raise RuntimeError("pin driver exploded")


def test_a_device_crash_is_a_user_facing_failure_and_does_not_escape(tmp_path):
    broker, _, audit = _broker(tmp_path, device=_CrashingDevice())
    result = _run(broker.submit({"action": "relay.set", "device_id": "lamp-board", "channel": 0, "value": "on"}))
    assert result.status == "failed"
    assert result.detail == {"code": "device_error"}
    assert "pin driver" not in result.message
    assert _events(audit)[-1]["event"] == "failed"


def test_submit_never_raises_for_non_mapping_input(tmp_path):
    broker, _, audit = _broker(tmp_path)
    result = _run(broker.submit(["relay.set"]))
    assert result.status == "refused"
    assert _events(audit)[-1]["event"] == "refused"


def test_audit_is_disabled_when_no_path_is_given():
    broker = CommandBroker(_policy(), {"lamp-board": FakeDevice("lamp-board")})
    result = _run(broker.submit({"action": "relay.set", "device_id": "lamp-board", "channel": 0, "value": "on"}))
    assert result.status == "ok"
