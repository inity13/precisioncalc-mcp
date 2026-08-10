"""
Currency conversion with Decimal precision and a pluggable rate provider.

Providers
---------
* ``StaticRateProvider``     -- hardcoded USD-based mid-market table (offline,
  deterministic; the MVP default). Fixed ``as_of`` timestamp.
* ``FrankfurterProvider``    -- live + historical ECB reference rates from the
  free, key-less https://frankfurter.app API. Supports current ("latest") and
  historical (``YYYY-MM-DD``) lookups. Results are cached in-memory with a TTL.

Selection
---------
The effective provider is chosen per call:

* live rates are used when a ``date`` is supplied, or ``live=True``, or the env
  ``PRECISIONCALC_FX_PROVIDER=frankfurter`` is set;
* otherwise the static table is used;
* if a live lookup fails (offline, timeout, HTTP error) we **fall back** to the
  static table and attach an explicit warning note -- the tool never hard-fails
  on a network problem.

Cross rate between any two currencies uses USD as the base::

    amount_in_to = amount * (usd_per[to] / usd_per[from])

    # MONETIZATION / PROD NOTE: Frankfurter is free/ECB-sourced and rate-limited.
    # For SLA-grade FX, drop in a licensed provider by implementing RateProvider.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from schemas.responses import CalcError, success_response

from ._util import format_money, quantize_money, require_non_negative, to_decimal

# --- static table -----------------------------------------------------------
_RATES_AS_OF = "2024-06-01T00:00:00Z"
_USD_RATES: dict[str, Decimal] = {
    "USD": Decimal("1"),
    "EUR": Decimal("0.9200"),
    "GBP": Decimal("0.7850"),
    "JPY": Decimal("157.00"),
    "CAD": Decimal("1.3700"),
    "AUD": Decimal("1.5050"),
    "CHF": Decimal("0.8950"),
    "CNY": Decimal("7.2400"),
    "INR": Decimal("83.50"),
}
_ZERO_DECIMAL = {"JPY"}
SUPPORTED_CURRENCIES = tuple(_USD_RATES.keys())


# ---------------------------------------------------------------------------
# Rate providers
# ---------------------------------------------------------------------------

class RateProvider(Protocol):
    """Returns ``(usd_rates, as_of, is_live)`` for an optional historical date."""

    name: str

    def get_usd_rates(self, on_date: date | None) -> tuple[dict[str, Decimal], str, bool]:
        ...


class StaticRateProvider:
    """Deterministic, offline mid-market table (USD base)."""

    name = "static"

    def get_usd_rates(self, on_date: date | None) -> tuple[dict[str, Decimal], str, bool]:
        return dict(_USD_RATES), _RATES_AS_OF, False


class FrankfurterProvider:
    """Live + historical ECB rates via https://frankfurter.app (no API key)."""

    name = "frankfurter"

    def __init__(self, timeout: float | None = None, ttl_seconds: int | None = None):
        self.base_url = os.getenv("PRECISIONCALC_FX_BASE_URL", "https://api.frankfurter.app")
        self.timeout = timeout if timeout is not None else float(os.getenv("PRECISIONCALC_FX_TIMEOUT", "4"))
        self.ttl = ttl_seconds if ttl_seconds is not None else int(os.getenv("PRECISIONCALC_FX_TTL", "3600"))
        self._cache: dict[str, tuple[float, dict[str, Decimal], str]] = {}
        self._lock = threading.Lock()

    def get_usd_rates(self, on_date: date | None) -> tuple[dict[str, Decimal], str, bool]:
        key = on_date.isoformat() if on_date else "latest"
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and (now - cached[0]) < self.ttl:
                return dict(cached[1]), cached[2], True

        symbols = ",".join(c for c in SUPPORTED_CURRENCIES if c != "USD")
        url = f"{self.base_url}/{key}?from=USD&to={symbols}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PrecisionCalc/2.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            raise CalcError(
                f"Live FX lookup failed: {exc}",
                hint="Network/provider unavailable; static rates can be used instead.",
                error_type="fx_provider_error",
            ) from exc

        rates_raw = payload.get("rates", {})
        rates: dict[str, Decimal] = {"USD": Decimal("1")}
        for code in SUPPORTED_CURRENCIES:
            if code == "USD":
                continue
            if code in rates_raw:
                rates[code] = Decimal(str(rates_raw[code]))
        as_of = f"{payload.get('date', key)} (ECB via frankfurter.app)"
        with self._lock:
            self._cache[key] = (now, dict(rates), as_of)
        return rates, as_of, True


_STATIC = StaticRateProvider()
_LIVE: FrankfurterProvider | None = None


def _live_provider() -> FrankfurterProvider:
    global _LIVE
    if _LIVE is None:
        _LIVE = FrankfurterProvider()
    return _LIVE


def _should_use_live(on_date: str | None, live: bool | None) -> bool:
    if live is True:
        return True
    if live is False:
        return False
    if on_date:
        return True
    return os.getenv("PRECISIONCALC_FX_PROVIDER", "static").lower() in ("frankfurter", "live")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_currency(
    amount: Any,
    from_currency: str,
    to_currency: str,
    on_date: str | None = None,
    live: bool | None = None,
) -> dict[str, Any]:
    """Convert ``amount`` from one currency to another with Decimal precision.

    Parameters
    ----------
    amount:
        Numeric amount in ``from_currency`` (>= 0).
    from_currency, to_currency:
        ISO 4217 codes (USD, EUR, GBP, JPY, CAD, AUD, CHF, CNY, INR).
    on_date:
        Optional ISO date (``YYYY-MM-DD``) for a historical rate (uses live
        provider). Weekends/holidays resolve to the latest prior ECB rate.
    live:
        Force live (``True``) or static (``False``) rates. ``None`` = auto.
    """
    amt = require_non_negative("amount", to_decimal("amount", amount))
    src = (from_currency or "").upper()
    dst = (to_currency or "").upper()
    _ensure_supported(src)
    _ensure_supported(dst)

    parsed_date = _validate_date(on_date) if on_date else None
    notes: list[str] = []

    use_live = _should_use_live(on_date, live)
    provider_name = "static"
    rates = _USD_RATES
    as_of = _RATES_AS_OF
    is_live = False

    if use_live:
        try:
            rates, as_of, is_live = _live_provider().get_usd_rates(parsed_date)
            provider_name = "frankfurter"
            if src not in rates or dst not in rates:
                raise CalcError(
                    "Live provider did not return one of the requested currencies.",
                    error_type="fx_provider_error",
                )
        except CalcError as exc:
            # Graceful fallback to static.
            rates, as_of, is_live = dict(_USD_RATES), _RATES_AS_OF, False
            provider_name = "static (fallback)"
            notes.append(f"Live FX unavailable ({exc}); used static mid-market table instead.")
            if parsed_date:
                notes.append(
                    f"Requested historical date '{on_date}' could not be honored offline; "
                    "static current-ish rate applied."
                )

    rate_from = rates[src]
    rate_to = rates[dst]
    rate = rate_to / rate_from
    converted = amt * rate

    places = 0 if dst in _ZERO_DECIMAL else 2
    converted_q = quantize_money(converted, places)

    notes.append("Cross rate computed via USD base: rate = usd_per[to] / usd_per[from].")
    if provider_name.startswith("static"):
        notes.append("Static rates are indicative for MVP; not for settlement/trading.")
    else:
        notes.append("Live ECB reference rates (frankfurter.app); indicative, not for settlement.")

    return success_response(
        value=converted,
        formatted_value=format_money(converted_q, dst, places),
        formula="converted = amount * (usd_per[to] / usd_per[from])",
        inputs_used={"amount": amt, "from_currency": src, "to_currency": dst,
                     "requested_date": on_date, "live": use_live},
        unit=dst,
        notes=notes,
        extra={
            "rate": rate,
            "rate_formatted": f"1 {src} = {rate.quantize(Decimal('0.00000001'))} {dst}",
            "rate_as_of": as_of,
            "provider": provider_name,
            "is_live": is_live,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "rounded_value": converted_q,
        },
    )


def _ensure_supported(code: str) -> None:
    if code not in _USD_RATES:
        raise CalcError(
            f"Unsupported currency '{code}'.",
            hint=f"Supported: {', '.join(SUPPORTED_CURRENCIES)}.",
            error_type="unsupported_currency",
        )


def _validate_date(value: str) -> date:
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CalcError(
            f"Invalid date '{value}'. Expected format YYYY-MM-DD.",
            hint="Example: 2024-01-15.",
        ) from exc
    if d > datetime.now(timezone.utc).date():
        raise CalcError(
            f"Date '{value}' is in the future; no rate exists yet.",
            hint="Use today's date or a past date.",
        )
    return d
