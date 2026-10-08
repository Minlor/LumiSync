"""Read-only, schema-scaled electrical readings and vendor energy statistics."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
import json
import math

from .errors import AccountError

# Values are converted to these display units after applying the device's scale.
METER_CODES = {
    "cur_power": ("power_w", "W"),
    "cur_voltage": ("voltage_v", "V"),
    "cur_current": ("current_a", "mA"),
    "total_forward_energy": ("energy_kwh", "kWh"),
    "forward_energy_total": ("energy_kwh", "kWh"),
    "total_energy": ("energy_kwh", "kWh"),
}


def status_functions(device: dict) -> dict:
    return {**device.get("tuya_functions", {}), **device.get("tuya_status_functions", {})}


def properties(spec: dict) -> dict:
    values = spec.get("values", {})
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            return {}
    return values if isinstance(values, dict) else {}


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _unit_factor(unit: str, field: str) -> float | None:
    unit = unit.strip().casefold().replace(".", "").replace("·", "").replace(" ", "")
    return {
        "power_w": {"w": 1, "kw": 1000, "mw": 0.001},
        "voltage_v": {"v": 1, "mv": 0.001},
        "current_a": {"a": 1, "ma": 0.001},
        "energy_kwh": {"kwh": 1, "wh": 0.001},
    }[field].get(unit)


def meter_fields(device: dict) -> list[str]:
    fields = []
    for code, (field, default_unit) in METER_CODES.items():
        spec = status_functions(device).get(code)
        if isinstance(spec, dict) and _unit_factor(str(properties(spec).get("unit") or default_unit), field) is not None:
            if field not in fields:
                fields.append(field)
    return fields


def decode_metering(device: dict, status: dict) -> dict:
    """Do not mistake add_ele (an interval increment) for a lifetime total."""
    result = dict.fromkeys(meter_fields(device))
    for code, (field, default_unit) in METER_CODES.items():
        spec = status_functions(device).get(code)
        value = _number(status.get(code))
        if not isinstance(spec, dict) or value is None:
            continue
        prop = properties(spec)
        try:
            scale = int(prop.get("scale", 0))
        except (ValueError, TypeError, OverflowError):
            continue
        factor = _unit_factor(str(prop.get("unit") or default_unit), field)
        if not 0 <= scale <= 9 or factor is None:
            continue
        converted = value / (10 ** scale) * factor
        if math.isfinite(converted):
            result[field] = converted
    return result


def energy_history_spec(device: dict) -> dict | None:
    """Only incremental energy is summed; cumulative counters are not."""
    spec = status_functions(device).get("add_ele")
    if not isinstance(spec, dict) or not str(spec.get("dp_id", "")).isdigit():
        return None
    unit = str(properties(spec).get("unit") or "kWh")
    return spec if _unit_factor(unit, "energy_kwh") is not None else None


def month_bounds(month: str, *, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    try:
        start = date.fromisoformat(month + "-01")
    except (ValueError, TypeError):
        raise AccountError("Choose a valid month for energy usage.", "validation") from None
    if start > today:
        raise AccountError("Energy usage is not available for a future month.", "validation")
    return start, min(date(start.year, start.month, monthrange(start.year, start.month)[1]), today)


def decode_energy_history(raw, month: str, spec: dict, *, today: date | None = None) -> dict:
    """Range-statistics v2 returns scaled engineering units, unlike live DPs.

    See Tuya's public socket example: getStatisticsRangDay values are displayed
    directly, while live cur_power/voltage/current are scaled by their schema.
    Missing days stay missing, including a completely empty vendor response.
    """
    start, end = month_bounds(month, today=today)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    values = raw.get("result", raw) if isinstance(raw, dict) else None
    if not isinstance(values, dict):
        raise AccountError("The account returned an unexpected energy history.", "response")
    factor = _unit_factor(str(properties(spec).get("unit") or "kWh"), "energy_kwh")
    if factor is None:
        raise AccountError("This device uses an unsupported energy unit.", "unsupported")
    days, reported = [], []
    for day in range(1, end.day + 1):
        current = start.replace(day=day)
        value = _number(values.get(current.strftime("%Y%m%d")))
        energy = value * factor if value is not None else None
        days.append({"date": current.isoformat(), "energy_kwh": energy})
        if energy is not None:
            reported.append(energy)
    return {"month": month, "days": days, "total_kwh": math.fsum(reported) if reported else None,
            "reported_days": len(reported), "expected_days": len(days), "source": "vendor"}
