"""
Business-day calculations with region-aware holidays.

Regions supported (MVP): ``US``, ``UK``, ``EU`` (a pragmatic pan-EU/TARGET-style
set), plus ``NONE`` for weekends-only. Callers may also pass a ``custom_holidays``
list (ISO ``YYYY-MM-DD`` strings) which is unioned with the region set.

Holidays are computed per-year (including floating US holidays and Easter-based
UK/EU holidays via :func:`dateutil.easter.easter`) so the logic is correct for
any year, not just a hardcoded window.

Operations
----------
add_business_days, count_business_days, next_business_day, previous_business_day
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from dateutil.easter import easter

from schemas.responses import CalcError, success_response

try:  # optional: broad, per-country calendars
    import holidays as _holidays_lib  # type: ignore
    _HAS_HOLIDAYS_LIB = True
except ImportError:  # pragma: no cover
    _holidays_lib = None
    _HAS_HOLIDAYS_LIB = False

WEEKEND = {5, 6}  # Saturday, Sunday
# Built-in regions always available (offline, deterministic). When the optional
# `holidays` library is installed, ANY of its country codes (US, GB, DE, FR, CA,
# AU, JP, IN, ...) may also be passed as `region`.
SUPPORTED_REGIONS = ("US", "UK", "EU", "NONE")
# `UK` is an alias for the ISO country code used by the holidays library.
_REGION_ALIASES = {"UK": "GB"}
HOLIDAYS_LIB_AVAILABLE = _HAS_HOLIDAYS_LIB


# ---------------------------------------------------------------------------
# Holiday computation
# ---------------------------------------------------------------------------

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the ``n``-th ``weekday`` (Mon=0) of ``month``. n=-1 => last."""
    if n > 0:
        d = date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        return d + timedelta(days=offset + (n - 1) * 7)
    # last occurrence
    if month == 12:
        d = date(year, 12, 31)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _observed(d: date) -> date:
    """US-style observed rule: Sat->Fri, Sun->Mon."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _us_holidays(year: int) -> set[date]:
    e = {
        _observed(date(year, 1, 1)),                       # New Year's Day
        _nth_weekday(year, 1, 0, 3),                        # MLK Day (3rd Mon Jan)
        _nth_weekday(year, 2, 0, 3),                        # Presidents' Day
        _nth_weekday(year, 5, 0, -1),                       # Memorial Day (last Mon May)
        _observed(date(year, 6, 19)),                       # Juneteenth
        _observed(date(year, 7, 4)),                        # Independence Day
        _nth_weekday(year, 9, 0, 1),                         # Labor Day
        _nth_weekday(year, 10, 0, 2),                        # Columbus Day
        _observed(date(year, 11, 11)),                      # Veterans Day
        _nth_weekday(year, 11, 3, 4),                        # Thanksgiving (4th Thu Nov)
        _observed(date(year, 12, 25)),                      # Christmas Day
    }
    return e


def _uk_holidays(year: int) -> set[date]:
    easter_sun = easter(year)
    good_friday = easter_sun - timedelta(days=2)
    easter_monday = easter_sun + timedelta(days=1)

    def _uk_observed(d: date) -> date:
        # UK "bank holiday" substitute-day rule: weekend -> next weekday.
        while d.weekday() in WEEKEND:
            d += timedelta(days=1)
        return d

    return {
        _uk_observed(date(year, 1, 1)),                    # New Year's Day
        good_friday,
        easter_monday,
        _nth_weekday(year, 5, 0, 1),                        # Early May bank holiday
        _nth_weekday(year, 5, 0, -1),                       # Spring bank holiday
        _nth_weekday(year, 8, 0, -1),                       # Summer bank holiday
        _uk_observed(date(year, 12, 25)),                  # Christmas
        _uk_observed(date(year, 12, 26)),                  # Boxing Day
    }


def _eu_holidays(year: int) -> set[date]:
    # Pragmatic pan-EU / TARGET-style common set.
    easter_sun = easter(year)
    return {
        date(year, 1, 1),                                  # New Year's Day
        easter_sun - timedelta(days=2),                    # Good Friday
        easter_sun + timedelta(days=1),                    # Easter Monday
        date(year, 5, 1),                                  # Labour Day
        date(year, 12, 25),                                # Christmas
        date(year, 12, 26),                                # St. Stephen's / 2nd day
    }


_REGION_FUNCS = {"US": _us_holidays, "UK": _uk_holidays, "EU": _eu_holidays}


def _holidays_for(region: str, years: Iterable[int], custom: list[str] | None) -> set[date]:
    region = (region or "US").upper()
    years = list(years)
    result: set[date] = set()

    if region == "NONE":
        pass
    elif region in _REGION_FUNCS:
        # Built-in, offline, deterministic set (US/UK/EU).
        func = _REGION_FUNCS[region]
        for y in years:
            result |= func(y)
    elif _HAS_HOLIDAYS_LIB:
        # Any ISO country code supported by the `holidays` library.
        code = _REGION_ALIASES.get(region, region)
        try:
            country = _holidays_lib.country_holidays(code, years=years)
        except (NotImplementedError, KeyError) as exc:
            raise CalcError(
                f"Unsupported region/country '{region}'.",
                hint=f"Built-in: {', '.join(SUPPORTED_REGIONS)}. Or any ISO country code "
                     "supported by the 'holidays' library (e.g. DE, FR, CA, AU, JP, IN).",
                error_type="unsupported_region",
            ) from exc
        result |= set(country.keys())
    else:
        raise CalcError(
            f"Unsupported region '{region}'.",
            hint=f"Supported regions: {', '.join(SUPPORTED_REGIONS)}. Install the optional "
                 "'holidays' package for per-country calendars.",
            error_type="unsupported_region",
        )

    for iso in custom or []:
        result.add(_parse_date(iso, field="custom_holidays entry"))
    return result


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _parse_date(value: str, field: str = "date") -> date:
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise CalcError(
            f"Invalid {field} '{value}'. Expected format YYYY-MM-DD.",
            hint="Example: 2024-12-24.",
        ) from exc


def _is_business_day(d: date, holidays: set[date]) -> bool:
    return d.weekday() not in WEEKEND and d not in holidays


def _years_span(a: date, b: date) -> range:
    lo, hi = sorted((a, b))
    return range(lo.year - 1, hi.year + 2)


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------

def business_days(
    operation: str,
    start_date: str,
    days: int | None = None,
    end_date: str | None = None,
    region: str = "US",
    custom_holidays: list[str] | None = None,
) -> dict[str, Any]:
    """Perform a business-day operation.

    Parameters
    ----------
    operation:
        ``add_business_days`` | ``count_business_days`` | ``next_business_day`` |
        ``previous_business_day``.
    start_date:
        ISO ``YYYY-MM-DD``.
    days:
        Required for ``add_business_days`` (may be negative to go backwards).
    end_date:
        Required for ``count_business_days`` (ISO).
    region:
        ``US`` | ``UK`` | ``EU`` | ``NONE``.
    custom_holidays:
        Optional list of extra ISO holiday dates.
    """
    op = (operation or "").strip().lower()
    start = _parse_date(start_date, "start_date")
    region_u = (region or "US").upper()

    if op == "add_business_days":
        if days is None:
            raise CalcError("add_business_days requires 'days'.",
                            hint="Pass an integer 'days' (negative moves backwards).")
        n = int(days)
        holidays = _holidays_for(region_u, range(start.year - 1, start.year + 3), custom_holidays)
        result = _add_business_days(start, n, holidays)
        return success_response(
            value=result.isoformat(),
            formatted_value=result.strftime("%A, %d %B %Y"),
            formula="result = start_date shifted by N business days (skip weekends+holidays)",
            inputs_used={"start_date": start.isoformat(), "days": n, "region": region_u,
                         "custom_holidays": custom_holidays or []},
            unit="date",
            notes=[f"Weekends and {region_u} holidays skipped.",
                   "Negative 'days' moves backwards." if n < 0 else "Forward shift."],
        )

    if op == "count_business_days":
        if end_date is None:
            raise CalcError("count_business_days requires 'end_date'.",
                            hint="Pass 'end_date' in YYYY-MM-DD.")
        end = _parse_date(end_date, "end_date")
        holidays = _holidays_for(region_u, _years_span(start, end), custom_holidays)
        count, inclusive_lo, inclusive_hi = _count_business_days(start, end, holidays)
        return success_response(
            value=count,
            formatted_value=f"{count} business day(s)",
            formula="count of business days in [min(start,end), max(start,end)] inclusive",
            inputs_used={"start_date": start.isoformat(), "end_date": end.isoformat(),
                         "region": region_u, "custom_holidays": custom_holidays or []},
            unit="days",
            notes=[f"Inclusive range {inclusive_lo.isoformat()} .. {inclusive_hi.isoformat()}.",
                   f"Weekends and {region_u} holidays excluded."],
        )

    if op in ("next_business_day", "previous_business_day"):
        step = 1 if op == "next_business_day" else -1
        holidays = _holidays_for(region_u, range(start.year - 1, start.year + 2), custom_holidays)
        d = start + timedelta(days=step)
        while not _is_business_day(d, holidays):
            d += timedelta(days=step)
        return success_response(
            value=d.isoformat(),
            formatted_value=d.strftime("%A, %d %B %Y"),
            formula=f"first business day {'after' if step > 0 else 'before'} start_date",
            inputs_used={"start_date": start.isoformat(), "region": region_u,
                         "custom_holidays": custom_holidays or []},
            unit="date",
            notes=[f"Weekends and {region_u} holidays skipped."],
        )

    raise CalcError(
        f"Unknown operation '{operation}'.",
        hint="Use add_business_days | count_business_days | next_business_day | previous_business_day.",
        error_type="unknown_operation",
    )


def _add_business_days(start: date, n: int, holidays: set[date]) -> date:
    if n == 0:
        return start
    step = 1 if n > 0 else -1
    remaining = abs(n)
    d = start
    while remaining > 0:
        d += timedelta(days=step)
        if _is_business_day(d, holidays):
            remaining -= 1
    return d


def _count_business_days(start: date, end: date, holidays: set[date]) -> tuple[int, date, date]:
    lo, hi = sorted((start, end))
    count = 0
    d = lo
    while d <= hi:
        if _is_business_day(d, holidays):
            count += 1
        d += timedelta(days=1)
    return count, lo, hi
