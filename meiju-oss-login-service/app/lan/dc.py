"""干衣机（0xDC）完整控制表 —— 依据设备自带 lua 协议脚本 T_0000_DC_5.lua。

midea-local 只实现了 power / start / ai_switch 三个 setter，所以能下发的东西很少。
设备真正的控制表在云端下发的 lua 里（commandSpec + bits2Config + controlTable），
App 运行时就是按这张表拼报文，所以它能控制全部功能。

本模块把这张表翻译成 Python：
  - CONTROL_SPEC：字段 -> (控制体字节下标, 起始 bit, 位宽)
  - 2/4 bit 字段按 lua 的 bits2Config / bits4Config 映射成整字节值
  - 其余字节填 0xFF，表示「该字段不变」

正文偏移换算：lua 的 offset 是以整个帧计的 bit 位，控制体起始于帧第 12 字节
（1-based），因此 byte_index = offset // 8 - 11。该换算已用真实报文核对：
ai_switch(234) -> 18，与 midea-local 的 MessageSetAISwitch 中 body[18] 一致。
"""

from __future__ import annotations

from typing import Any

from midealocal.devices.dc.message import MessageDCBase
from midealocal.message import ListTypes, MessageType

# 字段 -> (byte, bit, width)
CONTROL_SPEC: dict[str, tuple[int, int, int]] = {
    "power": (0, 0, 8),
    "control_status": (1, 0, 8),
    "program": (3, 0, 8),
    "dry_time": (4, 0, 16),
    "intensity": (8, 0, 8),
    "dryness_level": (9, 0, 8),
    "dry_temp": (10, 0, 8),
    "light": (11, 0, 2),
    "baby_lock": (11, 2, 2),
    "prevent_wrinkle_switch": (11, 4, 2),
    "appointment": (11, 6, 2),
    "damp_dry_signal": (12, 0, 2),
    "steam_switch": (12, 2, 2),
    "sterilize": (12, 4, 2),
    "remind_sound": (12, 6, 2),
    "appointment_time": (13, 0, 16),
    "ai_switch": (18, 2, 2),
    "bucket_clean_switch": (18, 4, 2),
    "eco_dry_switch": (18, 6, 2),
    "steam": (19, 0, 4),
    "prevent_wrinkle": (19, 4, 4),
    "material": (20, 0, 8),
}

# lua bits2Config：2bit 字段 -> 取值对应的整字节值
BITS2: dict[int, dict[int, int]] = {
    0: {0: 0x3F, 1: 0x7F, 3: 0xFF},
    2: {0: 0xCF, 1: 0xDF, 3: 0xFF},
    4: {0: 0xF3, 1: 0xF7, 3: 0xFF},
    6: {0: 0xFC, 1: 0xFD, 3: 0xFF},
}
# lua bits4Config
BITS4: dict[int, int] = {0: 0x0F, 4: 0xF0}

# 友好名 -> lua 字段名（用户口中的「童锁」就是 baby_lock）
ALIASES: dict[str, str] = {
    "child_lock": "baby_lock",
    "lock": "baby_lock",
    "temperature": "dry_temp",
    "sound": "remind_sound",
    "wrinkle": "prevent_wrinkle_switch",
    "appointment_switch": "appointment",
}

# 状态回报里存在的字段（lua reportMapping），可用于读回确认
STATUS_SPEC: dict[str, tuple[int, int]] = {
    "power": (1, 8),
    "running_status": (2, 8),
    "program": (4, 8),
    "dry_time": (5, 16),
    "intensity": (9, 8),
    "dryness_level": (10, 8),
    "dry_temp": (11, 8),
    "appointment_time": (14, 16),
    "progress": (16, 8),
    "remain_time": (17, 16),
    "project_no": (21, 8),
    "error_code": (24, 8),
    "door_warn": (25, 8),
    "ai_switch": (27, 8),
    "material": (28, 8),
    "water_box": (29, 8),
}

PROGRAM_NAMES = {
    0: "cotton", 1: "fiber", 2: "mixed_wash", 3: "jean", 4: "bedsheet", 5: "outdoor",
    6: "down_jacket", 7: "plush", 8: "wool", 9: "dehumidify", 10: "cold_air_fresh_air",
    11: "hot_air_dry", 12: "sport_clothes", 13: "underwear", 14: "baby_clothes", 15: "shirt",
    16: "standard", 17: "quick_dry", 18: "fresh_air", 19: "low_temp_dry", 20: "eco_dry",
    21: "quick_dry_30", 22: "towel", 23: "intelligent_dry", 24: "steam_care", 25: "big",
    26: "fixed_time_dry", 27: "night_dry", 28: "bracket_dry", 29: "western_trouser",
    30: "dehumidification", 31: "smart_dry", 32: "four_piece_suit", 33: "warm_clothes",
    34: "quick_dry_20", 35: "steam_sterilize", 36: "enzyme", 37: "big_60",
    38: "steam_no_iron", 39: "air_wash", 40: "bed_clothes", 41: "little_fast_dry",
    42: "small_piece_dry", 43: "big_dry", 44: "wool_nurse", 45: "sun_quilt",
    46: "fresh_remove_smell", 47: "bucket_self_clean", 48: "silk", 49: "sterilize",
}
PROGRAM_BY_NAME = {v: k for k, v in PROGRAM_NAMES.items()}


def normalize_field(name: str) -> str:
    key = name.strip().lower()
    return ALIASES.get(key, key)


def build_body(updates: dict[str, Any]) -> bytearray:
    """按控制表拼 21 字节控制体，未指定的字节保持 0xFF（不变）。"""
    body = bytearray([0xFF] * 21)
    for raw_name, raw_value in updates.items():
        name = normalize_field(raw_name)
        if name not in CONTROL_SPEC:
            raise ValueError(f"机型不支持字段 `{raw_name}`；可用字段：{', '.join(sorted(CONTROL_SPEC))}")
        byte, bit, width = CONTROL_SPEC[name]
        value = raw_value
        if name == "program" and isinstance(value, str) and value in PROGRAM_BY_NAME:
            value = PROGRAM_BY_NAME[value]
        value = int(value)
        if width == 8:
            body[byte] = value & 0xFF
        elif width == 16:
            body[byte] = value & 0xFF
            body[byte + 1] = (value >> 8) & 0xFF
        elif width == 2:
            if value not in BITS2[bit]:
                raise ValueError(f"字段 `{name}` 只接受 0 / 1 / 3（收到 {value}）")
            body[byte] = BITS2[bit][value]
        elif width == 4:
            if value not in BITS4:
                raise ValueError(f"字段 `{name}` 只接受 0 / 4（收到 {value}）")
            body[byte] = BITS4[value]
    return body


class MessageDCControl(MessageDCBase):
    """把一个现成的控制体发出去。"""

    def __init__(self, protocol_version: int, body: bytearray) -> None:
        super().__init__(
            protocol_version=protocol_version,
            message_type=MessageType.set,
            body_type=ListTypes.X02,
        )
        self._control_body = body

    @property
    def _body(self) -> bytearray:
        return self._control_body


def decode_status(body: bytes) -> dict[str, Any]:
    """按 lua reportMapping 解出全部状态字段（含 midealocal 没暴露的）。"""
    out: dict[str, Any] = {}
    for name, (byte, width) in STATUS_SPEC.items():
        if byte >= len(body):
            continue
        if width == 16:
            value = body[byte] | (body[byte + 1] << 8) if byte + 1 < len(body) else body[byte]
        else:
            value = body[byte]
        if name == "program":
            out[name] = PROGRAM_NAMES.get(value, value)
        elif name in ("power", "door_warn", "ai_switch"):
            out[name] = bool(value)
        else:
            out[name] = value
    return out


# ---------------------------------------------------------------- 状态体抓取
_LATEST: dict[str, bytes] = {}


def install_capture() -> None:
    """让 midealocal 在解析状态体时顺便留一份原始字节（读回确认要用）。"""
    from midealocal.devices.dc import message as dc_message

    body_cls = dc_message.DCGeneralMessageBody
    if getattr(body_cls, "_meiju_capture_installed", False):
        return
    original = body_cls.__init__

    def patched(self, body):  # type: ignore[no-untyped-def]
        original(self, body)
        _LATEST["body"] = bytes(body)

    body_cls.__init__ = patched  # type: ignore[method-assign]
    body_cls._meiju_capture_installed = True  # type: ignore[attr-defined]


def latest_status_body() -> bytes | None:
    return _LATEST.get("body")


def send_control(device, updates: dict[str, Any]) -> bytearray:  # type: ignore[no-untyped-def]
    """按控制表拼报文并发送，返回实际下发的控制体。"""
    body = build_body(updates)
    message = MessageDCControl(
        protocol_version=device._message_protocol_version,  # noqa: SLF001
        body=body,
    )
    device.build_send(message)
    return body


# 导入即安装，保证 _open_with_keys 内部那次 refresh_status 也能抓到状态体
install_capture()
