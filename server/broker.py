"""IoT Command Broker (فاز اول: فقط دستگاه شبیه‌سازی‌شده).

Why this module exists
----------------------
The LLM may produce a structured command, but its output must never reach a
GPIO or relay directly (``AGENTS.md`` §2.3, ``docs/SPEC.md`` §8).  Every command
passes through this broker, which:

1. validates the command against a closed schema (unknown action or field → reject),
2. checks an explicit policy (which action, on which device, which channel),
3. executes only after approval, with a bounded wait for the device's own result,
4. records every decision and outcome in an append-only audit log.

Phase 1 is deliberately narrow: one ``FakeDevice`` and two actions.  No real pin
is driven, so every result from this module is SIMULATED until hardware tests
are recorded in ``docs/HARDWARE_VALIDATION.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("persian_voice_ai.broker")

ACTIONS: frozenset[str] = frozenset({"relay.set", "sensor.read"})
"""The closed set of actions.  Anything else is rejected before policy runs."""

RELAY_STATES: frozenset[str] = frozenset({"on", "off"})


class BrokerError(Exception):
    """Base class for broker refusals; ``message`` is Farsi and user-facing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Command:
    """A validated structured command."""

    action: str
    device_id: str
    channel: int
    value: str | None = None

    @classmethod
    def from_mapping(cls, raw: Any) -> Command:
        if not isinstance(raw, dict):
            raise BrokerError("invalid_schema", "فرمان باید یک شیء باشد.")
        allowed = {"action", "device_id", "channel", "value"}
        unknown = set(raw) - allowed
        if unknown:
            raise BrokerError("invalid_schema", f"فیلد ناشناخته در فرمان: {sorted(unknown)}")
        action = raw.get("action")
        if action not in ACTIONS:
            raise BrokerError("unknown_action", f"عمل ناشناخته: {action!r}")
        device_id = raw.get("device_id")
        if not isinstance(device_id, str) or not device_id.strip():
            raise BrokerError("invalid_schema", "شناسهٔ دستگاه لازم است.")
        channel = raw.get("channel")
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 0:
            raise BrokerError("invalid_schema", "کانال باید عدد صحیح نامنفی باشد.")
        value = raw.get("value")
        if action == "relay.set":
            if value not in RELAY_STATES:
                raise BrokerError("invalid_schema", "مقدار رله باید on یا off باشد.")
        elif value is not None:
            raise BrokerError("invalid_schema", "عمل sensor.read مقدار نمی‌گیرد.")
        return cls(action=action, device_id=device_id.strip(), channel=channel, value=value)


@dataclass(frozen=True, slots=True)
class PolicyRule:
    action: str
    device_id: str
    channels: frozenset[int]


@dataclass(frozen=True, slots=True)
class Policy:
    """Explicit allow-list.  A command without a matching rule is refused."""

    rules: tuple[PolicyRule, ...] = ()

    def check(self, command: Command) -> None:
        for rule in self.rules:
            if (
                rule.action == command.action
                and rule.device_id == command.device_id
                and command.channel in rule.channels
            ):
                return
        raise BrokerError(
            "not_permitted",
            f"اجازهٔ {command.action} روی {command.device_id} کانال {command.channel} داده نشده است.",
        )


class Device(Protocol):
    device_id: str

    async def execute(self, command: Command) -> dict[str, Any]:
        """Apply ``command`` and return the device's own report of the result."""


@dataclass
class FakeDevice:
    """In-memory device.  Holds relay states; never touches hardware."""

    device_id: str
    relays: dict[int, str] = field(default_factory=dict)
    delay_s: float = 0.0

    async def execute(self, command: Command) -> dict[str, Any]:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if command.action == "relay.set":
            assert command.value is not None
            self.relays[command.channel] = command.value
            return {"channel": command.channel, "state": command.value}
        return {"channel": command.channel, "state": self.relays.get(command.channel, "off")}


@dataclass(frozen=True, slots=True)
class CommandResult:
    status: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


class AuditLog:
    """Append-only JSONL.  One line per decision, including refusals."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def write(self, event: dict[str, Any]) -> None:
        if self.path is None:
            return
        event = {"ts": round(time.time(), 3), **event}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


class CommandBroker:
    def __init__(
        self,
        policy: Policy,
        devices: dict[str, Device],
        *,
        audit: AuditLog | None = None,
        timeout_s: float = 3.0,
    ) -> None:
        self.policy = policy
        self.devices = devices
        self.audit = audit or AuditLog(None)
        self.timeout_s = timeout_s

    async def submit(self, raw: Any) -> CommandResult:
        """Validate, authorise, execute and audit one command.  Never raises."""
        try:
            command = Command.from_mapping(raw)
            self.policy.check(command)
        except BrokerError as refusal:
            self.audit.write({"event": "refused", "code": refusal.code, "request": raw})
            return CommandResult("refused", refusal.message, {"code": refusal.code})

        device = self.devices.get(command.device_id)
        if device is None:
            self.audit.write({"event": "refused", "code": "unknown_device", "command": asdict(command)})
            return CommandResult("refused", f"دستگاه {command.device_id!r} شناخته نشد.", {"code": "unknown_device"})

        self.audit.write({"event": "approved", "command": asdict(command)})
        try:
            report = await asyncio.wait_for(device.execute(command), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            self.audit.write({"event": "timeout", "command": asdict(command)})
            return CommandResult(
                "unconfirmed",
                "دستگاه در مهلت مقرر پاسخ نداد؛ نتیجه نامشخص است و انجام‌شدن تأیید نشد.",
                {"code": "device_timeout"},
            )
        except Exception:
            logger.exception("device execution failed")
            self.audit.write({"event": "failed", "command": asdict(command)})
            return CommandResult("failed", "اجرای فرمان روی دستگاه با خطا مواجه شد.", {"code": "device_error"})

        self.audit.write({"event": "executed", "command": asdict(command), "report": report})
        return CommandResult("ok", "فرمان با موفقیت اجرا و تأیید شد.", report)
