import os
import tempfile

import pytest

from src.guardrails import (
    AccountPolicy,
    GuardrailConfig,
    GuardrailEngine,
    GuardrailViolation,
    ServicePolicy,
    ToolRateLimit,
)


def test_guard_input_rejects_disallowed_service():
    config = GuardrailConfig(
        account_policy=AccountPolicy(allowed_accounts=["123"]),
        service_policy=ServicePolicy(allowed_services=["costexplorer"]),
    )
    engine = GuardrailEngine(config)

    with pytest.raises(GuardrailViolation):
        engine.guard_input(
            session_id="s",
            user_id="u",
            text="show spend",
            metadata={"account_id": "123", "service": "foo"},
        )


def test_tool_rate_limit():
    config = GuardrailConfig(
        tool_rate_limits={
            "fast_tool": ToolRateLimit("fast_tool", max_calls=1, per_seconds=60),
        }
    )
    engine = GuardrailEngine(config)

    engine.guard_tool_call(session_id="s", user_id="u", tool_name="fast_tool", arguments={})
    with pytest.raises(GuardrailViolation):
        engine.guard_tool_call(session_id="s", user_id="u", tool_name="fast_tool", arguments={})


def test_audit_event_writes_file():
    with tempfile.TemporaryDirectory() as tmp:
        log_file = os.path.join(tmp, "audit.log")
        config = GuardrailConfig(audit_log_path=log_file)
        engine = GuardrailEngine(config)

        engine.audit_event("test", "session", "user", {"foo": "bar"})

        with open(log_file, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        assert len(lines) == 1
        assert "\"event_type\": \"test\"" in lines[0]
