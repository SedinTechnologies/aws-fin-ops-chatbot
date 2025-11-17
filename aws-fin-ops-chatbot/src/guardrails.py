from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Policy data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AccountPolicy:
    allowed_accounts: List[str] = field(default_factory=list)

    def is_allowed(self, account_id: Optional[str]) -> bool:
        if not self.allowed_accounts or not account_id:
            return True
        return account_id in self.allowed_accounts


@dataclass(slots=True)
class ServicePolicy:
    allowed_services: List[str] = field(default_factory=list)

    def is_allowed(self, service: Optional[str]) -> bool:
        if not self.allowed_services or not service:
            return True
        normalized = service.lower()
        return any(normalized == allowed.lower() for allowed in self.allowed_services)


@dataclass(slots=True)
class WindowPolicy:
    max_lookback_days: int = 365
    max_forecast_days: int = 90

    def validate(self, lookback_days: Optional[int], forecast_days: Optional[int]) -> bool:
        if lookback_days is not None and lookback_days > self.max_lookback_days:
            return False
        if forecast_days is not None and forecast_days > self.max_forecast_days:
            return False
        return True


@dataclass(slots=True)
class BudgetPolicy:
    monthly_limit_usd: Optional[float] = None


@dataclass(slots=True)
class ToolRateLimit:
    tool_name: str
    max_calls: int
    per_seconds: int


@dataclass(slots=True)
class GuardrailConfig:
    account_policy: AccountPolicy = field(default_factory=AccountPolicy)
    service_policy: ServicePolicy = field(default_factory=ServicePolicy)
    window_policy: WindowPolicy = field(default_factory=WindowPolicy)
    budget_policy: BudgetPolicy = field(default_factory=BudgetPolicy)
    tool_rate_limits: Dict[str, ToolRateLimit] = field(default_factory=dict)
    audit_log_path: Optional[Path] = None
    enabled: bool = True


# ---------------------------------------------------------------------------
# Violation hierarchy
# ---------------------------------------------------------------------------


class GuardrailViolation(Exception):
    """Base exception for guardrail failures."""

    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.context = context or {}


class InputViolation(GuardrailViolation):
    pass


class ToolViolation(GuardrailViolation):
    pass


class ToolRateLimitViolation(ToolViolation):
    pass


class ModelViolation(GuardrailViolation):
    pass


class BudgetViolation(GuardrailViolation):
    pass


# ---------------------------------------------------------------------------
# Guardrail engine
# ---------------------------------------------------------------------------


class GuardrailEngine:
    """Evaluates inputs, tool usage, and model responses against policies."""

    RATE_LIMIT_GRACE_SECONDS = 0.5

    def __init__(self, config: GuardrailConfig) -> None:
        self.config = config
        self._tool_counters: Dict[str, List[float]] = {}
        self._counters_lock = threading.Lock()
        self._session_budget_spend = 0.0
        logger.debug("GuardrailEngine initialized with config: %s", config)

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "GuardrailEngine":
        enabled = os.getenv("GUARDRAILS_ENABLED", "true").lower() != "false"
        allowed_accounts = _split_env_list("ALLOWED_AWS_ACCOUNTS")
        allowed_services = _split_env_list("ALLOWED_AWS_SERVICES")

        max_lookback = _int_env("MAX_LOOKBACK_DAYS", 365)
        max_forecast = _int_env("MAX_FORECAST_DAYS", 90)

        budget_policy = BudgetPolicy(
            monthly_limit_usd=_float_from_json_env("BUDGET_POLICY_JSON", "monthly_limit_usd")
        )

        tool_limits = _parse_tool_limits(os.getenv("TOOL_RATE_LIMITS_JSON"))

        audit_path = os.getenv("GUARDRAIL_AUDIT_LOG")
        audit_path_obj = Path(audit_path) if audit_path else None

        config = GuardrailConfig(
            account_policy=AccountPolicy(allowed_accounts=allowed_accounts),
            service_policy=ServicePolicy(allowed_services=allowed_services),
            window_policy=WindowPolicy(
                max_lookback_days=max_lookback,
                max_forecast_days=max_forecast,
            ),
            budget_policy=budget_policy,
            tool_rate_limits=tool_limits,
            audit_log_path=audit_path_obj,
            enabled=enabled,
        )
        return cls(config)

    # ------------------------------------------------------------------
    # Public guard methods
    # ------------------------------------------------------------------
    def guard_input(
        self,
        *,
        session_id: str,
        user_id: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.config.enabled:
            return
        metadata = metadata or {}
        account = metadata.get("account_id")
        service = metadata.get("service")
        lookback_days = metadata.get("lookback_days")
        forecast_days = metadata.get("forecast_days")

        if not self.config.account_policy.is_allowed(account):
            raise InputViolation("Requested account is not authorized", {"account_id": account})

        if not self.config.service_policy.is_allowed(service):
            raise InputViolation("Requested service is not authorized", {"service": service})

        if not self.config.window_policy.validate(lookback_days, forecast_days):
            raise InputViolation(
                "Requested time window exceeds guardrail limits",
                {"lookback_days": lookback_days, "forecast_days": forecast_days},
            )

        if self._detect_sensitive_terms(text):
            raise InputViolation("Input contains disallowed content", {"text": text[:80]})

        self.audit_event(
            "guard_input_pass",
            session_id,
            user_id,
            {"text_sample": text[:120], "metadata": metadata},
        )

    def guard_tool_call(
        self,
        *,
        session_id: str,
        user_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> None:
        if not self.config.enabled:
            return
        self._enforce_tool_rate_limit(tool_name)
        account = arguments.get("account_id")
        service = arguments.get("service")
        if not self.config.account_policy.is_allowed(account):
            raise ToolViolation("Tool account argument is not authorized", {"account_id": account})
        if not self.config.service_policy.is_allowed(service):
            raise ToolViolation("Tool service argument is not authorized", {"service": service})
        self.audit_event(
            "guard_tool_call_pass",
            session_id,
            user_id,
            {"tool": tool_name, "arguments": _scrub_args(arguments)},
        )

    def guard_tool_response(
        self,
        *,
        session_id: str,
        user_id: str,
        tool_name: str,
        response: Any,
    ) -> None:
        if not self.config.enabled:
            return
        if self._detect_sensitive_terms(str(response)):
            raise ToolViolation(
                "Tool response contains disallowed content",
                {"tool": tool_name, "response_sample": str(response)[:200]},
            )
        self.audit_event(
            "guard_tool_response_pass",
            session_id,
            user_id,
            {"tool": tool_name},
        )

    def guard_model_response(
        self,
        *,
        session_id: str,
        user_id: str,
        content: str,
    ) -> None:
        if not self.config.enabled:
            return
        if self._detect_sensitive_terms(content):
            raise ModelViolation(
                "Model response contains disallowed content",
                {"response_sample": content[:200]},
            )
        self.audit_event(
            "guard_model_response_pass",
            session_id,
            user_id,
            {"content_sample": content[:200]},
        )

    def audit_event(
        self,
        event_type: str,
        session_id: str,
        user_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = payload or {}
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "session_id": session_id,
            "user_id": user_id,
            "payload": payload,
        }
        logger.info("guardrail_event", extra={"guardrail_event": record})
        if self.config.audit_log_path:
            try:
                self.config.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.config.audit_log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to write guardrail audit log: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _detect_sensitive_terms(self, text: str) -> bool:
        lowered = text.lower()
        sensitive_terms = [
            "secret access key",
            "password",
            "ssh key",
            "injection",
            "drop table",
        ]
        return any(term in lowered for term in sensitive_terms)

    def _enforce_tool_rate_limit(self, tool_name: str) -> None:
        rate_limit = self.config.tool_rate_limits.get(tool_name)
        if not rate_limit:
            return
        now = time.monotonic()
        with self._counters_lock:
            timestamps = self._tool_counters.setdefault(tool_name, [])
            window_start = now - rate_limit.per_seconds
            # prune old timestamps
            self._tool_counters[tool_name] = [ts for ts in timestamps if ts >= window_start]
            timestamps = self._tool_counters[tool_name]
            if len(timestamps) >= rate_limit.max_calls:
                raise ToolRateLimitViolation(
                    f"Tool '{tool_name}' exceeded rate limit",
                    {"tool": tool_name, "max_calls": rate_limit.max_calls, "per_seconds": rate_limit.per_seconds},
                )
            timestamps.append(now)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _split_env_list(env_name: str) -> List[str]:
    raw = os.getenv(env_name, "")
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _int_env(env_name: str, default: int) -> int:
    try:
        return int(os.getenv(env_name, default))
    except (TypeError, ValueError):
        return default


def _float_from_json_env(env_name: str, field: str) -> Optional[float]:
    raw = os.getenv(env_name)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        value = data.get(field)
        return float(value) if value is not None else None
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Invalid JSON in %s", env_name)
        return None


def _parse_tool_limits(raw: Optional[str]) -> Dict[str, ToolRateLimit]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("TOOL_RATE_LIMITS_JSON must be a list")
        limits = {}
        for item in data:
            name = item.get("tool_name")
            max_calls = int(item.get("max_calls", 0))
            per_seconds = int(item.get("per_seconds", 0))
            if not name or max_calls <= 0 or per_seconds <= 0:
                continue
            limits[name] = ToolRateLimit(name, max_calls, per_seconds)
        return limits
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse TOOL_RATE_LIMITS_JSON: %s", exc)
        return {}


def _scrub_args(args: Dict[str, Any]) -> Dict[str, Any]:
    scrubbed = {}
    for key, value in args.items():
        if key.lower() in {"password", "secret", "token"}:
            scrubbed[key] = "***"
        else:
            scrubbed[key] = value
    return scrubbed
