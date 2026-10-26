"""设备 lua 协议脚本解析：从各机型 lua 里提取「功能字段清单」。

不同机型的 lua 是不同代协议引擎，字段声明方式也不同：
  - 0xDC：静态表 commandSpec（bit 偏移式）
  - 0xAC：keyT 数组逐条 push 字段名（key-value 式）
  - 0xCA/0xE1：uptable/info 等
  - 0x9B：modeTable/statusTable/powerTable 等按模式分表
  - 0xE3/0xB6：bitBandList/msgBytes 等
本模块负责「列出功能」；具体控制编码目前只有 DC 完整实现（app/lan/dc.py）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# vendored midea-local 可能需要（dc.py 依赖）
_VENDOR = Path(__file__).resolve().parents[2] / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

VAR_DIR = Path(__file__).resolve().parents[2] / "var"
LUA_DIR = VAR_DIR / "lua"
PARSED_DIR = VAR_DIR / "lua-parsed"

# 排除词：字符串扫描时过滤掉的非字段
STOP = {
    "local", "require", "return", "then", "else", "elseif", "end", "for", "in",
    "do", "while", "if", "function", "true", "false", "nil", "and", "or", "not",
    "version", "bit", "table", "string", "math", "os", "io", "cjson", "crc8",
}


def _find_balanced(text: str, marker: str) -> str:
    m = re.search(marker, text)
    if not m:
        return ""
    i = m.end() - 1
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[m.start() : j + 1]
    return ""


def _static_table_keys(text: str, name: str) -> list[str]:
    """从静态表 local name = { [..] = {...} } 里取键名。"""
    body = _find_balanced(text, rf"local\s+{name}\s*=\s*\{{")
    return re.findall(r'"name"\s*\]\s*=\s*"([a-zA-Z][\w]*)"', body) or re.findall(
        r'\[\s*"name"\s*\]\s*=\s*"([a-zA-Z][\w]*)"', body
    )


def _ac_fields(text: str) -> list[str]:
    return re.findall(r'keyT\[[^\]]*\]\s*=\s*"([a-zA-Z][\w]*)"', text)


def _dc_fields(text: str) -> list[str]:
    body = _find_balanced(text, r"local\s+commandSpec\s*=\s*\{{")
    return re.findall(r'([a-zA-Z][\w]*)\s*=\s*\{', body)


def _generic_fields(text: str) -> list[str]:
    """兜底：从疑似控制表的赋值里取形如 name = { 的字段名。"""
    names = set()
    for body_name in ("statusTable", "modeTable", "infoM", "info", "rs", "cmd", "tb", "uptable"):
        body = _find_balanced(text, rf"local\s+{body_name}\s*=\s*\{{")
        for n in re.findall(r'\[?"?([a-z][a-z0-9_]{2,30})"?\]?\s*=\s*\{', body):
            if n not in STOP:
                names.add(n)
        for n in re.findall(r'\[?"?([a-z][a-z0-9_]{2,30})"?\]?\s*=\s*"[a-zA-Z_]', body):
            if n not in STOP:
                names.add(n)
    return sorted(names)


def extract_fields(lua_path: Path, device_type: int) -> list[str]:
    text = lua_path.read_text(encoding="utf-8", errors="ignore")
    fields: list[str] = []
    if device_type == 0xDC:
        from .dc import CONTROL_SPEC

        fields = list(CONTROL_SPEC)  # 已验证的 22 字段
    elif device_type == 0xAC:
        fields = _ac_fields(text)  # keyT 逐条 push
    else:
        # 通用：形如 ["field_name"] 的注册表项（E3/B6/CA/E1 都是这种风格）
        fields = re.findall(r'\["([a-z][a-z0-9_]{2,40})"\]', text)
        fields += _generic_fields(text)
    seen, ordered = set(), []
    for f in fields:
        f = f.strip()
        if f and f not in seen and f not in STOP:
            seen.add(f)
            ordered.append(f)
    return ordered


def load_for_type(device_type: int, sn: str | None = None) -> dict[str, Any]:
    """取某机型的解析结果（缓存到 var/lua-parsed/<0xXX>.json）。"""
    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = PARSED_DIR / f"0x{device_type:02X}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    lua_files = list(LUA_DIR.glob(f"*{device_type:02X}/*.lua")) or list(LUA_DIR.glob(f"*-0x{device_type:02X}/*.lua"))
    if not lua_files:
        return {"device_type": f"0x{device_type:02X}", "lua": None, "fields": [], "controllable": []}
    fields = extract_fields(lua_files[0], device_type)
    # 可控字段 = 我们真正实现了下发编码的（目前只有 DC）
    controllable: list[str] = []
    if device_type == 0xDC:
        from .dc import CONTROL_SPEC

        controllable = sorted(CONTROL_SPEC)
    result: dict[str, Any] = {
        "device_type": f"0x{device_type:02X}",
        "lua": lua_files[0].name,
        "fields": fields,
        "controllable": controllable,
    }
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def all_parsed() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not LUA_DIR.exists():
        return out
    types: set[int] = set()
    for p in LUA_DIR.glob("*/*.lua"):
        m = re.search(r"-0x([0-9A-Fa-f]{2})\\", p.parent.name + "\\")
        m2 = re.search(r"-0x([0-9A-Fa-f]{2})", p.parent.name)
        if m2:
            types.add(int(m2.group(1), 16))
    for t in sorted(types):
        out[f"0x{t:02X}"] = load_for_type(t)
    return out
