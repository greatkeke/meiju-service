"""Simulated smart-home devices, TSL-driven.

Why TSL: the APK does not hard-code property names for each appliance. It fetches a
TSL template at runtime (`ITSLTemplateService`, `NewTSLTemplateApiService`), caches it
in `TSLTemplateDao`, and renders controls from it
(`TSLModelBase` module types JS_ONE_BUTTON / JS_MULTI_BUTTON / JS_ONE_SLIDER /
JS_TWO_SLIDER, `TSLStyleData{style,value}`). Property writes go out as
`NewTSLBatchPropertiesReq{applianceCode, props[{code,value}]}`.

So the clone keeps a per-device schema and lets the UI render whatever properties the
schema declares, instead of hard-coding an air-conditioner panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

Unit = str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class PropSchema:
    """One TSL property: how to render it and what values it accepts."""

    code: str
    label: str
    kind: str  # switch | enum | number | slider
    unit: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    step: int | None = None
    options: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "label": self.label, "kind": self.kind}
        if self.unit:
            payload["unit"] = self.unit
        if self.minimum is not None:
            payload["min"] = self.minimum
        if self.maximum is not None:
            payload["max"] = self.maximum
        if self.step is not None:
            payload["step"] = self.step
        if self.options:
            payload["options"] = self.options
        return payload


@dataclass
class Device:
    appliance_code: int
    sn: str
    name: str
    type: str
    type_label: str
    smart_product_id: str
    model_number: str
    room: str
    online_status: int = 1
    schema: list[PropSchema] = field(default_factory=list)
    props: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=_now)

    def prop_schema(self, code: str) -> PropSchema | None:
        for item in self.schema:
            if item.code == code:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        """Device payload: mirrors DeviceControlRsp plus the TSL schema the UI needs."""
        return {
            "applianceCode": self.appliance_code,
            "sn": self.sn,
            "name": self.name,
            "type": self.type,
            "typeLabel": self.type_label,
            "smartProductId": self.smart_product_id,
            "modelNumber": self.model_number,
            "room": self.room,
            "onlineStatus": self.online_status,
            "props": dict(self.props),
            "schema": [item.to_dict() for item in self.schema],
            "updatedAt": self.updated_at,
        }


class DeviceError(ValueError):
    """Illegal property write; the gateway reports these as code 1002-class errors."""


def _ac() -> Device:
    return Device(
        appliance_code=1234500001,
        sn="AC00DEMO0001",
        name="客厅空调",
        type="0xAC",
        type_label="空调",
        smart_product_id="1001",
        model_number="KFR-35GW",
        room="客厅",
        schema=[
            PropSchema("power", "电源", "switch"),
            PropSchema(
                "mode",
                "模式",
                "enum",
                options=[
                    {"value": "cool", "label": "制冷"},
                    {"value": "heat", "label": "制热"},
                    {"value": "fan", "label": "送风"},
                    {"value": "dry", "label": "除湿"},
                    {"value": "auto", "label": "自动"},
                ],
            ),
            PropSchema("targetTemperature", "温度", "slider", unit="°C", minimum=16, maximum=30, step=1),
            PropSchema(
                "fanSpeed",
                "风量",
                "enum",
                options=[
                    {"value": "auto", "label": "自动"},
                    {"value": "low", "label": "低"},
                    {"value": "mid", "label": "中"},
                    {"value": "high", "label": "高"},
                ],
            ),
            PropSchema("swing", "上下扫风", "switch"),
            PropSchema("eco", "节能", "switch"),
        ],
        props={
            "power": "off",
            "mode": "cool",
            "targetTemperature": 26,
            "fanSpeed": "auto",
            "swing": "off",
            "eco": "off",
        },
    )


def _water_heater() -> Device:
    return Device(
        appliance_code=1234500002,
        sn="WH00DEMO0002",
        name="主卫热水器",
        type="0xE3",
        type_label="热水器",
        smart_product_id="1002",
        model_number="F60-32DE",
        room="卫生间",
        schema=[
            PropSchema("power", "电源", "switch"),
            PropSchema("targetTemperature", "水温", "slider", unit="°C", minimum=35, maximum=75, step=1),
        ],
        props={"power": "off", "targetTemperature": 50},
    )


def _light() -> Device:
    return Device(
        appliance_code=1234500003,
        sn="LT00DEMO0003",
        name="餐厅灯",
        type="0x2A",
        type_label="灯光",
        smart_product_id="1003",
        model_number="MD-L1",
        room="餐厅",
        schema=[
            PropSchema("power", "电源", "switch"),
            PropSchema("brightness", "亮度", "slider", unit="%", minimum=1, maximum=100, step=1),
            PropSchema("colorTemperature", "色温", "slider", unit="K", minimum=2700, maximum=6500, step=100),
        ],
        props={"power": "off", "brightness": 80, "colorTemperature": 4000},
    )


class DeviceRegistry:
    """In-memory device store; validation is driven by each device's schema."""

    def __init__(self) -> None:
        self._devices: dict[int, Device] = {}
        for device in (_ac(), _water_heater(), _light()):
            self._devices[device.appliance_code] = device

    # ------------------------------------------------------------------ reads
    def list(self) -> list[Device]:
        return list(self._devices.values())

    def get(self, appliance_code: int) -> Device:
        device = self._devices.get(appliance_code)
        if not device:
            raise DeviceError(f"unknown applianceCode: {appliance_code}")
        return device

    # ------------------------------------------------------------------ writes
    def set_props(self, appliance_code: int, props: list[dict[str, Any]]) -> Device:
        device = self.get(appliance_code)
        if device.online_status != 1:
            raise DeviceError("device is offline")
        if not props:
            raise DeviceError("props must not be empty")

        staged: dict[str, Any] = {}
        for item in props:
            code = str(item.get("code", "")).strip()
            if not code:
                raise DeviceError("prop code must not be empty")
            schema = device.prop_schema(code)
            if schema is None:
                raise DeviceError(f"unsupported property: {code}")
            staged[code] = self._validate(schema, item.get("value"))

        device.props.update(staged)
        device.updated_at = _now()
        return device

    # ------------------------------------------------------------------ validation
    @staticmethod
    def _validate(schema: PropSchema, value: Any) -> Any:
        if schema.kind == "switch":
            text = str(value).lower()
            if text not in ("on", "off"):
                raise DeviceError(f"{schema.code} must be on/off, got {value!r}")
            return text

        if schema.kind == "enum":
            allowed = [option["value"] for option in schema.options]
            text = str(value).lower()
            if text not in allowed:
                raise DeviceError(f"{schema.code} must be one of {allowed}, got {value!r}")
            return text

        # number / slider
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise DeviceError(f"{schema.code} must be an integer, got {value!r}") from exc
        if schema.minimum is not None and number < schema.minimum:
            raise DeviceError(f"{schema.code} must be >= {schema.minimum}, got {number}")
        if schema.maximum is not None and number > schema.maximum:
            raise DeviceError(f"{schema.code} must be <= {schema.maximum}, got {number}")
        return number


registry = DeviceRegistry()
