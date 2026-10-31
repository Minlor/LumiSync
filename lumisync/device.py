"""Typed device descriptor shared by discovery, drivers and the sync engine.

Device descriptors used to be loose dictionaries with two names for the same
field (``device_id``/``devId``, ``segment_count_override``/
``segmentCountOverride``, ``sku``/``model``).  This module gives every layer a
single typed object and normalizes the legacy spellings once, when settings are
loaded.

``Device`` deliberately implements the mapping protocol.  The GUI still hands
its models plain dictionaries in a few compatibility paths (and third-party
integrations may construct descriptors directly), so a ``Device`` can be used
wherever a mapping was used before.  New code should prefer the typed
attributes.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field, fields
from typing import Any, Optional

CORE_FIELDS = frozenset(
    {
        "device_id",
        "name",
        "model",
        "sku",
        "transport",
        "device_kind",
        "ip",
        "port",
        "mac",
        "ble_address",
        "protocol_version",
        "segment_count_override",
        "matrix_size",
        "manual",
        "account_id",
        "vendor_account",
        "local_transport",
        "cloud_transport",
        "color_temp_min",
        "color_temp_max",
    }
)

ALIASES = {
    "devId": "device_id",
    "segmentCountOverride": "segment_count_override",
    "localKey": "local_key",
    "Device_Port": "device_port",
    "devicePort": "device_port",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _integer(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _boolean(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off", ""):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _segment_override(value: Any) -> Optional[int]:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if 0 < count <= 255 else None


def _matrix_size(value: Any) -> str:
    if not value:
        return ""
    try:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return f"{int(value[0])}x{int(value[1])}"
    except (TypeError, ValueError):
        return ""
    return str(value)


def _protocol_version(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 3.3


def _transport(data: Mapping[str, Any]) -> str:
    transport = str(data.get("transport") or "").strip().lower()
    if transport:
        return transport
    kind = str(data.get("type") or "").strip().lower()
    if kind in ("ble", "idotmatrix", "idotmatrix_ble"):
        return "ble"
    if kind in ("tuya", "lsc"):
        return "tuya"
    return "lan"


@dataclass(eq=False)
class Device(MutableMapping[str, Any]):
    """A device descriptor with normalized, typed core fields.

    Vendor-specific fields (Tuya data-point maps, Govee cloud capabilities,
    firmware metadata, …) are kept in :attr:`extra`.  They are still available
    through the mapping API, but they no longer leak into the shared contract.
    """

    device_id: str = ""
    name: str = ""
    model: str = ""
    sku: str = ""
    transport: str = "lan"
    device_kind: str = ""
    ip: str = ""
    port: int = 0
    mac: str = ""
    ble_address: str = ""
    protocol_version: float = 3.3
    segment_count_override: Optional[int] = None
    matrix_size: str = ""
    manual: bool = False
    account_id: str = ""
    vendor_account: str = ""
    local_transport: str = ""
    cloud_transport: str = ""
    color_temp_min: int = 2700
    color_temp_max: int = 6500
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------- construction

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | "Device" | None) -> "Device":
        """Normalize a descriptor, accepting every historical field spelling."""
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            return cls()

        device_id = _text(data.get("device_id") or data.get("devId"))
        override = data.get("segment_count_override")
        if override in (None, ""):
            override = data.get("segmentCountOverride")
        model = _text(data.get("model"))
        sku = _text(data.get("sku"))
        matrix = _matrix_size(data.get("matrix_size"))

        extra: dict[str, Any] = {}
        for key, value in data.items():
            if key in CORE_FIELDS or key in ("type",) or value is None:
                continue
            canonical = ALIASES.get(str(key), str(key))
            if canonical in CORE_FIELDS:
                continue
            extra.setdefault(canonical, value)
        # ``type`` still selects a driver for descriptors created before the
        # transport field existed; keep it when it is not implied by transport.
        kind = data.get("type")
        if kind and str(kind).strip().lower() not in ("", "lan", "govee", "govee_lan"):
            extra["type"] = kind

        return cls(
            device_id=device_id,
            name=_text(data.get("name")),
            model=model,
            sku=sku,
            transport=_transport(data),
            device_kind=_text(data.get("device_kind")),
            ip=_text(data.get("ip")),
            port=_integer(data.get("port"), 0),
            mac=_text(data.get("mac")),
            ble_address=_text(data.get("ble_address")),
            protocol_version=_protocol_version(data.get("protocol_version", 3.3)),
            segment_count_override=_segment_override(override),
            matrix_size=matrix,
            manual=_boolean(data.get("manual")),
            account_id=_text(data.get("account_id")),
            vendor_account=_text(data.get("vendor_account")),
            local_transport=_text(data.get("local_transport")),
            cloud_transport=_text(data.get("cloud_transport")),
            color_temp_min=_integer(data.get("color_temp_min"), 2700),
            color_temp_max=_integer(data.get("color_temp_max"), 6500),
            extra=extra,
        )

    # --------------------------------------------------------------- properties

    @property
    def catalog_sku(self) -> str:
        """The model used for capability-catalog lookups."""
        return self.sku or self.model

    @property
    def is_cloud(self) -> bool:
        return self.transport in ("govee_cloud", "govee_account", "tuya_cloud")

    @property
    def is_ble(self) -> bool:
        return self.transport == "ble"

    @property
    def is_pooled(self) -> bool:
        return self.transport in ("ble", "tuya")

    @property
    def identity(self) -> str:
        """Stable key, preferring MAC over weaker IP/model fallbacks."""
        return (
            self.mac
            or self.device_id
            or self.ble_address
            or self.ip
            or self.model
        )

    # ------------------------------------------------------------------ mapping

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical descriptor persisted in ``settings.json``."""
        result: dict[str, Any] = {}
        for key in (
            "device_id",
            "name",
            "model",
            "sku",
            "device_kind",
            "ip",
            "mac",
            "ble_address",
            "matrix_size",
            "account_id",
            "vendor_account",
            "local_transport",
            "cloud_transport",
        ):
            value = getattr(self, key)
            if value:
                result[key] = value
        if self.transport and self.transport != "lan":
            result["transport"] = self.transport
        if self.port:
            result["port"] = int(self.port)
        if self.protocol_version != 3.3:
            result["protocol_version"] = self.protocol_version
        if self.segment_count_override is not None:
            result["segment_count_override"] = int(self.segment_count_override)
        if self.manual:
            result["manual"] = True
        if self.color_temp_min != 2700:
            result["color_temp_min"] = int(self.color_temp_min)
        if self.color_temp_max != 6500:
            result["color_temp_max"] = int(self.color_temp_max)
        result.update(self.extra)
        return result

    def __getitem__(self, key: str) -> Any:
        key = ALIASES.get(key, key)
        if key in CORE_FIELDS:
            return getattr(self, key)
        return self.extra[key]

    def __setitem__(self, key: str, value: Any) -> None:
        key = ALIASES.get(key, key)
        if key in CORE_FIELDS:
            if key == "segment_count_override":
                value = _segment_override(value)
            elif key == "port":
                value = _integer(value, 0)
            elif key == "protocol_version":
                value = float(value or 3.3)
            elif key == "manual":
                value = _boolean(value)
            elif key in ("color_temp_min", "color_temp_max"):
                value = _integer(value, 2700 if key == "color_temp_min" else 6500)
            elif key in ("device_id", "name", "model", "sku", "transport", "device_kind",
                         "ip", "mac", "ble_address", "matrix_size", "account_id",
                         "vendor_account", "local_transport", "cloud_transport"):
                value = _text(value)
            setattr(self, key, value)
            return
        if value is None:
            self.extra.pop(key, None)
        else:
            self.extra[key] = value

    def __delitem__(self, key: str) -> None:
        key = ALIASES.get(key, key)
        if key in CORE_FIELDS:
            default_field = next(item for item in fields(self) if item.name == key)
            setattr(self, key, default_field.default)
            return
        del self.extra[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __contains__(self, key: object) -> bool:
        # ``get``/``[]`` expose typed defaults (transport == "lan"), but
        # ``in`` should describe fields that are actually stored. This keeps
        # checks such as ``"cloud_transport" in device`` truthful.
        return isinstance(key, str) and ALIASES.get(key, key) in self.to_dict()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Device):
            return self.to_dict() == other.to_dict()
        if isinstance(other, Mapping):
            return self.to_dict() == Device.from_dict(other).to_dict()
        return NotImplemented

    def copy(self) -> "Device":
        return Device.from_dict(self.to_dict())

    def __deepcopy__(self, memo: dict[int, Any]) -> "Device":
        import copy

        return Device.from_dict(copy.deepcopy(self.to_dict(), memo))

    def __repr__(self) -> str:
        return f"Device({self.to_dict()!r})"


DeviceLike = Mapping[str, Any] | Device


def as_device(device: DeviceLike | None) -> Device:
    """Coerce a mapping or :class:`Device` into a typed descriptor."""
    return Device.from_dict(device)


def normalize_device(device: DeviceLike | None) -> dict[str, Any]:
    """Return the canonical dictionary form of a descriptor."""
    return as_device(device).to_dict()


__all__ = ["Device", "DeviceLike", "as_device", "normalize_device", "ALIASES", "CORE_FIELDS"]
