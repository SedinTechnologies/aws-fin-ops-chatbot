"""Shared helpers for tool argument handling and formatting."""

from __future__ import annotations

import json
import logging
import re
import calendar
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

SERVICE_KEYWORDS = {
    "rds": "Amazon Relational Database Service",
    "relational database": "Amazon Relational Database Service",
    "aurora": "Amazon Relational Database Service",
    "s3": "Amazon Simple Storage Service",
    "simple storage": "Amazon Simple Storage Service",
    "ec2": "Amazon Elastic Compute Cloud - Compute",
    "lambda": "AWS Lambda",
    "dynamodb": "Amazon DynamoDB",
    "cloudfront": "Amazon CloudFront",
    "eks": "Amazon Elastic Kubernetes Service",
    "elasticache": "Amazon ElastiCache",
    "redshift": "Amazon Redshift",
    "sqs": "Amazon Simple Queue Service",
    "sns": "Amazon Simple Notification Service"
}

MONTH_NAME_TO_NUM = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12
}

DATE_RANGE_START_KEYS = ("start_date", "start", "StartDate", "startDate", "Start")
DATE_RANGE_END_KEYS = ("end_date", "end", "EndDate", "endDate", "End")

# Required fields for AWS MCP tools
REQUIRED_TOOL_FIELDS = {
    "get_dimension_values": {"date_range", "dimension"},
    "get_cost_and_usage": {"date_range", "granularity", "metrics"}
}


def _serialize_tool_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "text":
                    parts.append(item.get("text", ""))
                elif item_type == "image_url":
                    url = (item.get("image_url") or {}).get("url")
                    if url:
                        parts.append(f"[image]: {url}")
                else:
                    parts.append(json.dumps(item, ensure_ascii=True))
            else:
                parts.append(str(item))
        flattened = "\n\n".join(part for part in parts if part)
        if flattened:
            return flattened
    try:
        return json.dumps(content, ensure_ascii=True)
    except TypeError:
        return str(content)


def _load_tool_arguments(raw_arguments: str) -> dict:
    if not raw_arguments:
        return {}
    try:
        return json.loads(raw_arguments)
    except json.JSONDecodeError:
        stripped = raw_arguments.lstrip()
        decoder = json.JSONDecoder()
        try:
            obj, _ = decoder.raw_decode(stripped)
            logger.debug("Recovered tool arguments after trailing payload noise")
            return obj
        except json.JSONDecodeError:
            last_open = stripped.rfind("{")
            if last_open != -1:
                candidate = stripped[last_open:]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass
            raise


def _normalize_tool_arguments(tool_name: str, arguments: dict) -> dict:
    if tool_name == "get_cost_and_usage":
        metrics = arguments.get("metrics")
        metric = arguments.pop("metric", None)

        if metrics is None and metric is not None:
            metrics = metric

        if isinstance(metrics, str) and metrics:
            arguments["metrics"] = [metrics]
        elif isinstance(metrics, list):
            arguments["metrics"] = metrics
        elif metrics is None:
            arguments.pop("metrics", None)

    return arguments


def _last_user_message_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
    return ""


def _build_date_range(start_dt: date, end_dt: date | None = None) -> dict:
    end_dt = end_dt or start_dt
    return {"start_date": start_dt.isoformat(), "end_date": end_dt.isoformat()}


def _month_date_range(year: int, month: int) -> dict:
    start_dt = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end_dt = date(year, month, last_day)
    return _build_date_range(start_dt, end_dt)


def _last_completed_month() -> dict:
    today = date.today().replace(day=1)
    previous_month_last_day = today - timedelta(days=1)
    return _month_date_range(previous_month_last_day.year, previous_month_last_day.month)


def _infer_relative_range(text: str) -> dict | None:
    lowered = text.lower()
    today = date.today()

    if any(phrase in lowered for phrase in ["last month", "previous month"]):
        first_of_current = today.replace(day=1)
        last_of_previous = first_of_current - timedelta(days=1)
        return _month_date_range(last_of_previous.year, last_of_previous.month)

    if any(phrase in lowered for phrase in ["this month", "current month"]):
        start = today.replace(day=1)
        return _build_date_range(start, today)

    if "last 30 days" in lowered or "past 30 days" in lowered:
        start = today - timedelta(days=30)
        return _build_date_range(start, today)

    if "last week" in lowered or "past week" in lowered:
        start = today - timedelta(days=7)
        return _build_date_range(start, today)

    return None


def _infer_month_year_range(text: str) -> dict | None:
    lowered = text.lower()
    match = re.search(r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+(\d{4})", lowered)
    if match:
        month = MONTH_NAME_TO_NUM.get(match.group(1))
        year = int(match.group(2))
        if month:
            return _month_date_range(year, month)
    return None


def _infer_date_range(messages: list[dict]) -> dict:
    text = _last_user_message_text(messages)
    if not text:
        return _last_completed_month()
    relative = _infer_relative_range(text)
    if relative:
        return relative
    month_year = _infer_month_year_range(text)
    if month_year:
        return month_year
    return _last_completed_month()


def _normalize_date_range(date_range: dict | None) -> dict:
    if not isinstance(date_range, dict):
        return {}
    start = None
    end = None
    for key in DATE_RANGE_START_KEYS:
        if date_range.get(key):
            start = date_range[key]
            break
    for key in DATE_RANGE_END_KEYS:
        if date_range.get(key):
            end = date_range[key]
            break
    normalized = {}
    if start:
        normalized["start_date"] = start
    if end:
        normalized["end_date"] = end
    return normalized


def _infer_service_filter(messages: list[dict]) -> str | None:
    text = _last_user_message_text(messages).lower()
    for keyword, service in SERVICE_KEYWORDS.items():
        if keyword in text:
            return service
    return None


def _populate_default_tool_arguments(tool_name: str, tool_args: dict, messages: list[dict]) -> dict:
    working_args = dict(tool_args or {})

    if tool_name in {"get_cost_and_usage", "get_dimension_values"}:
        normalized_range = _normalize_date_range(working_args.get("date_range"))
        if not normalized_range.get("start_date") or not normalized_range.get("end_date"):
            normalized_range = _infer_date_range(messages)
        working_args["date_range"] = normalized_range

    if tool_name == "get_cost_and_usage":
        metrics = working_args.get("metrics")
        if not metrics:
            working_args["metrics"] = ["UnblendedCost"]
        granularity = working_args.get("granularity")
        if not granularity:
            working_args["granularity"] = "MONTHLY"

        existing_filter = working_args.get("filter") or {}
        has_service_filter = False
        if isinstance(existing_filter, dict):
            dims = existing_filter.get("dimensions") or existing_filter.get("Dimensions") or {}
            key = (dims.get("Key") or dims.get("key") or "").upper()
            values = dims.get("Values") or dims.get("values") or []
            has_service_filter = key == "SERVICE" and bool(values)

        if not has_service_filter:
            inferred_service = _infer_service_filter(messages)
            if inferred_service:
                working_args["filter"] = {
                    "dimensions": {
                        "key": "SERVICE",
                        "values": [inferred_service]
                    }
                }

    if tool_name == "get_dimension_values" and not working_args.get("dimension"):
        working_args["dimension"] = "SERVICE"

    return working_args


def _missing_required_fields(tool_name: str, arguments: dict) -> set[str]:
    required = REQUIRED_TOOL_FIELDS.get(tool_name, set())
    if not required:
        return set()
    missing = set()
    for field in required:
        value = arguments.get(field)
        if value in (None, "", {}):
            missing.add(field)
    return missing
