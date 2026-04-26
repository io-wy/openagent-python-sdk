"""Datetime and time-related tools."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openagents.interfaces.tool import ToolPlugin


class CurrentTimeTool(ToolPlugin):
    """Get current datetime.

    What: return the current wall-clock time in ISO/timestamp/formatted forms; honors timezone if pytz available.
    Usage: ``{"id": "now", "type": "current_time"}``; invoke with ``{"timezone": "UTC"}``.
    Depends on: stdlib ``datetime``; optional ``pytz`` for non-UTC.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        tz = params.get("timezone", "UTC")
        try:
            if tz == "UTC":
                dt = datetime.now(timezone.utc)
            else:
                import pytz

                dt = datetime.now(pytz.timezone(tz))
            return {
                "iso": dt.isoformat(),
                "timestamp": dt.timestamp(),
                "formatted": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": str(dt.tzinfo),
            }
        except Exception:
            # Fallback to UTC
            dt = datetime.now(timezone.utc)
            return {
                "iso": dt.isoformat(),
                "timestamp": dt.timestamp(),
                "formatted": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": "UTC",
            }


class DateParseTool(ToolPlugin):
    """Parse various date string formats.

    What: try a fixed list of common date formats and return the first parseable representation.
    Usage: ``{"id": "date_parse", "type": "date_parse"}``; invoke with ``{"date": "2024-01-15"}``.
    Depends on: stdlib ``datetime.strptime``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        date_str = params.get("date", "")
        if not date_str:
            raise ValueError("'date' parameter is required")

        formats = [
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%B %d, %Y",
            "%b %d, %Y",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                return {
                    "parsed": dt.isoformat(),
                    "timestamp": dt.timestamp(),
                    "year": dt.year,
                    "month": dt.month,
                    "day": dt.day,
                    "weekday": dt.strftime("%A"),
                }
            except ValueError:
                continue

        raise ValueError(f"Unable to parse date: {date_str}")


class DateDiffTool(ToolPlugin):
    """Calculate difference between two dates.

    What: parse two dates and report the absolute difference in days/hours/minutes/seconds.
    Usage: ``{"id": "date_diff", "type": "date_diff"}``; invoke with
    ``{"date1": "...", "date2": "...", "unit": "days"}``.
    Depends on: stdlib ``datetime``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})

    async def invoke(self, params: dict[str, Any], context: Any) -> Any:
        date1 = params.get("date1", "")
        date2 = params.get("date2", "")
        unit = params.get("unit", "days")  # days, hours, minutes, seconds

        if not date1 or not date2:
            raise ValueError("'date1' and 'date2' parameters are required")

        # Try to parse dates
        formats = ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"]

        dt1 = dt2 = None
        for fmt in formats:
            try:
                dt1 = datetime.strptime(date1, fmt)
                dt2 = datetime.strptime(date2, fmt)
                break
            except ValueError:
                continue

        if dt1 is None or dt2 is None:
            raise ValueError(f"Unable to parse dates: {date1}, {date2}")

        diff = abs(dt2 - dt1)
        seconds = diff.total_seconds()

        result = {"seconds": seconds}
        if unit == "days":
            result["result"] = diff.days
        elif unit == "hours":
            result["result"] = seconds / 3600
        elif unit == "minutes":
            result["result"] = seconds / 60
        elif unit == "seconds":
            result["result"] = seconds
        else:
            result["result"] = diff.days

        return result
