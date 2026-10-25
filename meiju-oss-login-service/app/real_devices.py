"""Real-device backend: forwards the clone's device routes to the Midea cloud.

Enabled when a real `accessToken` is supplied (env `MIDEA_ACCESS_TOKEN`, or posted to
`/v1/session/real-token`). Aliases are the ones recovered from the APK:

    POST /v1/user/device/list                 设备列表
    POST /v1/appliance/thing/template/get     TSL 模板（控制属性码来源）
    POST /v1/thing/properties/{code}          读属性
    POST /v1/thing/batch/properties           写属性

Everything here is best-effort in one respect only: the TSL template payload shape is
not documented in the APK's Java layer (it is server-defined), so `schema_for()` tries
a few known shapes and otherwise falls back to a generic AC schema, marking
`schemaSource` so a caller can tell the difference.
"""

from __future__ import annotations

from typing import Any

from .providers.midea import MideaProvider

LIST_ALIAS = "/v1/user/device/list"
TEMPLATE_ALIAS = "/v1/appliance/thing/template/get"

FALLBACK_SCHEMA: dict[str, Any] = {
    "props": [
        {"code": "power", "name": "电源", "type": "switch", "values": ["on", "off"]},
        {
            "code": "targetTemperature",
            "name": "设定温度",
            "type": "slider",
            "min": 16,
            "max": 30,
            "step": 1,
            "unit": "℃",
        },
        {
            "code": "mode",
            "name": "模式",
            "type": "enum",
            "options": [
                {"value": "cool", "label": "制冷"},
                {"value": "heat", "label": "制热"},
                {"value": "fan", "label": "送风"},
                {"value": "dry", "label": "除湿"},
                {"value": "auto", "label": "自动"},
            ],
        },
        {
            "code": "fanSpeed",
            "name": "风速",
            "type": "enum",
            "options": [
                {"value": "low", "label": "低"},
                {"value": "mid", "label": "中"},
                {"value": "high", "label": "高"},
                {"value": "auto", "label": "自动"},
            ],
        },
    ]
}


class RealDevices:
    """Thin adapter over MideaProvider's signed transport."""

    def __init__(self, provider: MideaProvider, access_token: str) -> None:
        self.provider = provider
        self.access_token = access_token

    # ------------------------------------------------------------------ transport
    def _call(self, alias: str, body: dict) -> tuple[int, str, Any]:
        reply = self.provider._post(alias, body, access_token=self.access_token)  # noqa: SLF001
        return reply.code, reply.msg, reply.data

    # ------------------------------------------------------------------ queries
    def raw_list(self) -> Any:
        code, msg, data = self._call(LIST_ALIAS, self.provider._envelope("", {}))
        if code != 0:
            raise ValueError(f"device list failed: {code} {msg}")
        return data

    def raw_template(self, appliance_codes: list[int]) -> Any:
        code, msg, data = self._call(
            TEMPLATE_ALIAS,
            self.provider._envelope("", {"applianceCodes": appliance_codes}),  # noqa: SLF001
        )
        if code != 0:
            raise ValueError(f"template failed: {code} {msg}")
        return data

    def raw_props(self, appliance_code: int) -> Any:
        code, msg, data = self._call(f"/v1/thing/properties/{appliance_code}", self.provider._envelope("", {}))
        if code != 0:
            raise ValueError(f"properties failed: {code} {msg}")
        return data

    def write_props(self, appliance_code: int, props: list[dict]) -> Any:
        body = self.provider._envelope(
            "", {"applianceCode": appliance_code, "props": props}
        )
        code, msg, data = self._call("/v1/thing/batch/properties", body)
        if code != 0:
            raise ValueError(f"set properties failed: {code} {msg}")
        return data

    # ------------------------------------------------------------------ shaping
    def _schema_from(self, template: Any, appliance_code: int) -> tuple[dict, str]:
        """Try to lift a property schema out of the TSL template payload."""
        candidates: list[Any] = []
        if isinstance(template, dict):
            for key in ("template", "templates", "applianceList", "data"):
                value = template.get(key)
                if value is not None:
                    candidates.append(value)
            if isinstance(template.get("list"), list):
                candidates.extend(template["list"])
        elif isinstance(template, list):
            candidates.extend(template)

        for item in candidates:
            if isinstance(item, list):
                for entry in item:
                    if isinstance(entry, dict) and entry.get("applianceCode") in (None, appliance_code):
                        if isinstance(entry.get("props"), list) and entry["props"]:
                            return {"props": entry["props"]}, "template"
            if isinstance(item, dict):
                entry_code = item.get("applianceCode")
                if entry_code is not None and int(entry_code) != appliance_code:
                    continue
                props = item.get("props") or item.get("propertyList")
                if isinstance(props, list) and props:
                    return {"props": props}, "template"
        return FALLBACK_SCHEMA, "fallback"

    def devices(self) -> list[dict[str, Any]]:
        raw = self.raw_list()
        entries: list[dict] = []
        if isinstance(raw, dict):
            for key in ("applianceList", "list", "devices"):
                if isinstance(raw.get(key), list):
                    entries = raw[key]
                    break
        elif isinstance(raw, list):
            entries = raw

        codes = [int(item["applianceCode"]) for item in entries if isinstance(item, dict) and item.get("applianceCode")]
        template: Any = None
        if codes:
            try:
                template = self.raw_template(codes)
            except Exception:  # noqa: BLE001 - 模板拿不到就退回兜底 schema
                template = None

        shaped: list[dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            code = item.get("applianceCode")
            if code is None:
                continue
            schema, source = self._schema_from(template, int(code))
            shaped.append(
                {
                    "applianceCode": int(code),
                    "name": item.get("name") or item.get("nickName") or f"设备 {code}",
                    "type": item.get("type") or "",
                    "typeLabel": item.get("type") or "设备",
                    "online": str(item.get("online", "1")) not in ("0", "false", "False"),
                    "schema": schema,
                    "schemaSource": source,
                    "raw": item,
                }
            )
        return shaped


# ---------------------------------------------------------------------- runtime switch
_override_token: str = ""


def set_override_token(token: str) -> None:
    global _override_token
    _override_token = token.strip()


def active_token() -> str:
    from .config import settings

    return _override_token or settings.midea_access_token


def client() -> "RealDevices | None":
    """Build the real-device client when a token is available, else None (simulated)."""
    token = active_token()
    if not token:
        return None

    from .config import settings
    from .providers.midea import MideaProvider

    provider = MideaProvider(
        secret=settings.midea_secret or "prod_secret123@muc",
        base_url=settings.midea_base,
        device_id=settings.midea_device_id,
        iot_key=settings.midea_iot_key,
    )
    return RealDevices(provider, token)
