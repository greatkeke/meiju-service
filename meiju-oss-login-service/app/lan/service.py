"""Midea LAN discovery and control, built on the vendored midea-local.

两条相互独立的链路：

  * `keys`    —— 只走云端（`/v2/iot/secure/getToken`），**不需要在局域网**。
                 美居已下线 v1 接口，v2 需要 homegroupId + applianceCodes 列表。
  * `discover`/`status`/`set` —— 只走局域网（UDP 6445 发现 + 6444/6443 控制），
                 需要 token/key，完全离线。

Usage:
    python lan_service.py keys                  # 云端刷新所有设备的 token/key
    python lan_service.py discover [--target IP]
    python lan_service.py status <device_id>
    python lan_service.py dump  <device_id>
    python lan_service.py set   <device_id> <attribute> <value>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from aiohttp import ClientSession

# vendored midea-local：直接加进 sys.path，避免把它做成需要构建的包
_VENDOR = Path(__file__).resolve().parents[2] / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from midealocal.cloud import MeijuCloud  # noqa: E402
from midealocal.devices import device_selector  # noqa: E402
from midealocal.discover import discover  # noqa: E402

VAR_DIR = Path(__file__).resolve().parents[2] / "var"
VAR_DIR.mkdir(parents=True, exist_ok=True)
KEYS_CACHE = VAR_DIR / "lan_keys.json"
CACHE_VERSION = 2


# ---------------------------------------------------------------- credentials
def credentials_from_env() -> tuple[str, str] | None:
    """账号密码只从环境变量来，绝不落库、不硬编码。

    MEIJU_ACCOUNT / MEIJU_PASSWORD
    """
    account = os.environ.get("MEIJU_ACCOUNT", "").strip()
    password = os.environ.get("MEIJU_PASSWORD", "")
    if not account or not password:
        return None
    return account, password


# ---------------------------------------------------------------- cache
def load_cache() -> dict[str, Any]:
    """读取缓存（兼容旧格式：直接是 {id: entry}）。"""
    if not KEYS_CACHE.exists():
        return {"version": CACHE_VERSION, "fetched_at": None, "valid": False, "devices": {}}
    try:
        raw = json.loads(KEYS_CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": CACHE_VERSION, "fetched_at": None, "valid": False, "devices": {}}
    if "devices" in raw and isinstance(raw["devices"], dict):
        raw.setdefault("version", CACHE_VERSION)
        raw.setdefault("valid", True)
        raw.setdefault("fetched_at", None)
        return raw
    return {
        "version": CACHE_VERSION,
        "fetched_at": _file_mtime_iso(),
        "valid": True,
        "devices": raw,
    }


def save_cache(devices: dict[str, Any], account: str) -> dict[str, Any]:
    cache = {
        "version": CACHE_VERSION,
        "fetched_at": utc_now_iso(),
        "account": account,
        "valid": True,
        "devices": devices,
    }
    KEYS_CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        KEYS_CACHE.chmod(0o600)
    except OSError:
        pass
    return cache


def mark_cache_invalid(reason: str) -> None:
    """密钥全部失效时标记缓存不可用，促使调用方重新登录取密钥。"""
    cache = load_cache()
    cache["valid"] = False
    cache["invalid_reason"] = reason
    KEYS_CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _file_mtime_iso() -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(KEYS_CACHE.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def cache_age_seconds(cache: dict[str, Any]) -> int | None:
    fetched = cache.get("fetched_at")
    if not fetched:
        return None
    from datetime import datetime, timezone

    try:
        then = datetime.fromisoformat(str(fetched))
    except ValueError:
        return None
    return int((datetime.now(timezone.utc) - then).total_seconds())


def load_keys() -> dict[str, dict[str, Any]]:
    cache = load_cache()
    devices = cache.get("devices") or {}
    if not devices:
        raise SystemExit("没有密钥缓存，请先在页面登录一次（或运行 keys 命令）")
    return devices


# ---------------------------------------------------------------- cloud: token/key
async def fetch_all_keys(account: str, password: str) -> dict[str, Any]:
    """用账号密码登录云端，取每台设备的 token/key 并缓存。密码不落盘。"""
    devices: dict[str, Any] = {}
    async with ClientSession() as session:
        cloud = MeijuCloud(
            cloud_name="美的美居", session=session, account=account, password=password
        )
        if not await cloud.login():
            raise SystemExit("云端登录失败：账号或密码不正确")
        homes = await cloud.list_home() or {}
        print(f"家庭组: {homes}")
        for home_id in homes:
            appliances = await cloud.list_appliances(str(home_id)) or {}
            for appliance_id, info in appliances.items():
                keys = await cloud.get_cloud_keys(appliance_id)
                if not keys:
                    continue
                devices[str(appliance_id)] = {
                    "name": info.get("name"),
                    "type": info.get("type"),
                    "sn": info.get("sn"),
                    "model": info.get("model"),
                    "keys": {
                        str(method): {"token": kv["token"], "key": kv["key"]}
                        for method, kv in keys.items()
                    },
                }
    save_cache(devices, account)
    print(f"已缓存 {len(devices)} 台设备的密钥")
    return load_cache()


# ---------------------------------------------------------------- LAN
def _candidate_targets() -> list[str]:
    """广播地址候选：本机各网段广播 + 全局广播。"""
    import socket

    targets = {"255.255.255.255"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("127.", "169.254.")):
                continue
            parts = ip.split(".")
            if len(parts) == 4:
                targets.add(".".join(parts[:3]) + ".255")
    except OSError:
        pass
    return sorted(targets)


async def discover_all(
    extra_target: str | None = None, cidr: str | None = None
) -> dict[int, dict[str, Any]]:
    """先广播，再（必要时）单播扫网段。

    UDP 广播无法穿过 Tailscale 这类三层隧道，但单播可以，所以当广播一无所获时
    用单播逐 IP 探测同一个 /24。设备 id 与云端 applianceCode 一致。
    """
    found: dict[int, dict[str, Any]] = {}
    targets = _candidate_targets()
    if extra_target:
        targets = [extra_target] + targets
    for target in targets:
        try:
            part = await discover(ip_address=target)
        except Exception:  # noqa: BLE001
            continue
        found.update(part)
        if part:
            print(f"[discover] 广播 {target} -> {len(part)} 台")
    if found:
        return found

    # 广播不通（如 Tailscale）：按探测到的网段逐个单播扫描
    from .subnets import resolve_cidrs

    cidrs = resolve_cidrs(cidr)
    if not cidrs:
        print("[discover] 没有可扫描的网段；请设置 MEIJU_LAN_CIDR")
        return found

    print(f"[discover] 广播无结果，改为单播扫描: {', '.join(cidrs)}")
    for target_cidr in cidrs:
        try:
            part = await sweep_cidr_hosts(target_cidr)
        except Exception as exc:  # noqa: BLE001
            print(f"[discover] 扫描 {target_cidr} 失败: {type(exc).__name__}: {exc}")
            continue
        if part:
            found.update(part)
            break  # 找到一个网段有设备就够，不必再扫其它
    return found


async def sweep_cidr_hosts(
    cidr: str, per_port: tuple[int, ...] = (6445, 20086), reply_wait: float = 10.0
) -> dict[int, dict[str, Any]]:
    """逐 IP 单播发现（可穿三层隧道）。"""
    import ipaddress
    import socket
    import time as _time

    from midealocal.discover import BROADCAST_MSG, _parse_discover_response

    net = ipaddress.ip_network(cidr, strict=False)
    loop = asyncio.get_running_loop()
    found: dict[int, dict[str, Any]] = {}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    try:
        for host in net.hosts():
            for port in per_port:
                try:
                    await loop.sock_sendto(sock, BROADCAST_MSG, (str(host), port))
                except OSError:
                    pass

        deadline = _time.time() + reply_wait
        while _time.time() < deadline:
            try:
                device_id, info = await asyncio.wait_for(
                    _parse_discover_response(loop, sock, found), timeout=2.0
                )
            except (asyncio.TimeoutError, ConnectionResetError, OSError):
                continue
            except Exception:  # noqa: BLE001 - midea-local 对无效回包会抛各种异常
                continue
            if info:
                found[device_id] = info
                print(
                    f"[discover] ✓ {info['ip_address']}:{info['port']} id={device_id} "
                    f"type=0x{int(info['type']):02X} sn={info['sn']}"
                )
    finally:
        sock.close()
    return found


async def cmd_discover(extra_target: str | None) -> None:
    found = await discover_all(extra_target)
    if not found:
        print("未发现设备。请确认本机与设备在同一局域网（当前网段可能不同）。")
        print("可指定广播地址重试：python lan_service.py discover --target 10.0.0.255")
        return
    print(f"\n发现 {len(found)} 台设备：")
    for device_id, info in found.items():
        print(
            f"  id={device_id}  ip={info.get('ip_address')}:{info.get('port')}  "
            f"type=0x{int(info.get('type') or 0):02X}  protocol={info.get('protocol')}\n"
            f"     sn={info.get('sn')}  model={info.get('model')}"
        )


def _device_from(device_id: int, info: dict[str, Any], entry: dict[str, Any], method: str = "1"):
    keys = entry["keys"]
    kv = keys.get(method) or next(iter(keys.values()))
    device = device_selector(
        name=str(device_id),
        device_id=device_id,
        device_type=int(info["type"]),
        ip_address=info["ip_address"],
        port=int(info["port"]),
        token=kv["token"],
        key=kv["key"],
        device_protocol=info["protocol"],
        model=str(info.get("model") or ""),
        subtype=0,
        customize="",
        mac=info.get("mac"),
        serial_number=info.get("sn"),
    )
    if device is not None:
        # 记录机型码，供控制接口做「可下发属性」提示
        device._meiju_type = int(info["type"])  # noqa: SLF001
    return device


ENDPOINTS_CACHE = VAR_DIR / "lan_endpoints.json"
_ENDPOINT_FIELDS = ("ip_address", "port", "protocol", "type", "sn", "model", "mac")


def load_endpoints() -> dict[str, dict[str, Any]]:
    """上次成功发现的设备地址，用于免扫描直连（对 Tailscale 场景很关键）。"""
    if not ENDPOINTS_CACHE.exists():
        return {}
    try:
        data = json.loads(ENDPOINTS_CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_endpoints(found: dict[int, dict[str, Any]]) -> None:
    if not found:
        return
    data = load_endpoints()
    for device_id, info in found.items():
        data[str(device_id)] = {k: info.get(k) for k in _ENDPOINT_FIELDS}
    try:
        ENDPOINTS_CACHE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _match_info(
    device_id: int | str,
    found: dict[int, dict[str, Any]],
    cache: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    """在发现结果里定位设备：先按 id，再按云端 SN，最后反查 SN。"""
    key = str(device_id)
    entry = cache.get(key)
    info = found.get(int(device_id)) if key.isdigit() else None
    if info is None and entry is not None:
        target_sn = str(entry.get("sn") or "")
        if target_sn:
            for candidate in found.values():
                if str(candidate.get("sn") or "") == target_sn:
                    info = candidate
                    break
    if info is None:
        for candidate in found.values():
            sn = str(candidate.get("sn") or "")
            if not sn:
                continue
            for cached_id, cached in cache.items():
                if str(cached.get("sn") or "") == sn:
                    return candidate, cached, cached_id
    return info, entry, key


async def _connect_with(entry: dict[str, Any], info: dict[str, Any], device_id: int | str):
    """用一台设备的全部密钥候选尝试握手，返回可用 device。"""
    methods = sorted((entry.get("keys") or {}).keys()) or ["1"]
    errors: list[str] = []
    for candidate_method in methods:
        try:
            device = _device_from(
                int(info.get("device_id") or device_id), info, entry, candidate_method
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"method {candidate_method}: 构造失败 {type(exc).__name__}")
            continue
        try:
            await device.open()
            await asyncio.sleep(1.0)
            if not getattr(device, "available", False):
                raise RuntimeError("连接未就绪（认证可能失败）")
            await device.refresh_status()
            await asyncio.sleep(1.0)
            device._meiju_method = candidate_method  # noqa: SLF001
            return device
        except Exception as exc:  # noqa: BLE001
            errors.append(f"method {candidate_method}: {type(exc).__name__}: {exc}")
            try:
                await device.close()
            except Exception:  # noqa: BLE001
                pass
    raise RuntimeError("; ".join(errors) or "所有密钥候选都失败")


async def _open_with_keys(
    device_id: int | str,
    extra_target: str | None = None,
    method: str = "1",
):
    """定位并连接一台局域网设备。

    优先使用上次发现的地址直接连接（省掉整段扫描，Tailscale 下能快十几秒），
    失败后才重新扫描并刷新地址缓存。返回的 device 上带有：
      _meiju_source      —— cached / sweep
      _meiju_elapsed_ms  —— 本次定位耗时
    """
    started = time.time()
    cache = load_keys()
    cached_info = None if extra_target else load_endpoints().get(str(device_id))

    if cached_info:
        entry = cache.get(str(device_id))
        if entry is not None:
            try:
                device = await _connect_with(entry, cached_info, device_id)
                device._meiju_source = "cached"  # noqa: SLF001
                device._meiju_elapsed_ms = int((time.time() - started) * 1000)  # noqa: SLF001
                return device
            except Exception:  # noqa: BLE001 - 地址可能已变，回退到扫描
                pass

    found = await discover_all(extra_target)
    save_endpoints(found)
    if not found:
        raise SystemExit("局域网内没发现任何设备：本机与设备不在同一网段，或设备全部离线。")

    info, entry, _key = _match_info(device_id, found, cache)
    if info is None:
        lines = [
            f"局域网内没发现 device_id={device_id}。",
            "",
            f"本次扫描到 {len(found)} 台：",
        ]
        for did, dev in found.items():
            lines.append(
                f"  id={did}  sn={dev.get('sn')}  type=0x{int(dev.get('type') or 0):02X}  ip={dev.get('ip_address')}"
            )
        if entry:
            lines.append("")
            lines.append(f"该设备在云端的 SN 是 {entry.get('sn')}，不在上面的列表里。")
            lines.append("常见原因：这台设备不走 Wi-Fi（BLE 网关机型），或当前不在线。")
        raise SystemExit("\n".join(lines))

    if entry is None:
        raise SystemExit(f"id={device_id} 没有缓存密钥，请在页面重新登录以获取密钥")

    try:
        device = await _connect_with(entry, info, device_id)
    except RuntimeError as exc:
        mark_cache_invalid("所有密钥候选都认证失败（密钥可能已轮换）")
        raise SystemExit(
            f"无法与设备建立会话（{exc}）。\n"
            "请在页面重新登录以刷新密钥（或设置 MEIJU_ACCOUNT/MEIJU_PASSWORD 后运行 keys）。"
        ) from exc

    device._meiju_source = "sweep"  # noqa: SLF001
    device._meiju_elapsed_ms = int((time.time() - started) * 1000)  # noqa: SLF001
    return device

async def cmd_status(device_id: int, extra_target: str | None) -> None:
    device = await _open_with_keys(device_id, extra_target)
    try:
        payload = {
            "device_id": device_id,
            "class": type(device).__name__,
            "available": getattr(device, "available", None),
            "attributes": {
                name: device.get_attribute(name)
                for name in sorted(getattr(device, "attributes", {}) or {})
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    finally:
        await device.close()


async def cmd_dump(device_id: int, extra_target: str | None) -> None:
    device = await _open_with_keys(device_id, extra_target)
    try:
        names = sorted(getattr(device, "attributes", {}) or {})
        print(f"共 {len(names)} 个属性：")
        for name in names:
            print(f"   {name:34s} = {device.get_attribute(name)}")
    finally:
        await device.close()


async def cmd_set(device_id: int, attr: str, raw: str, extra_target: str | None) -> None:
    device = await _open_with_keys(device_id, extra_target)
    value: Any
    if raw.lower() in ("true", "on"):
        value = True
    elif raw.lower() in ("false", "off"):
        value = False
    else:
        try:
            value = float(raw) if "." in raw else int(raw)
        except ValueError:
            value = raw
    try:
        before = device.get_attribute(attr)
        print(f"设置前 {attr} = {before}")
        device.set_attribute(attr, value)
        await asyncio.sleep(2.0)
        await device.refresh_status()
        await asyncio.sleep(2.0)
        print(f"设置后 {attr} = {device.get_attribute(attr)}")
    finally:
        await device.close()


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return
    cmd = argv[0]
    target = None
    if "--target" in argv:
        idx = argv.index("--target")
        target = argv[idx + 1]
        del argv[idx : idx + 2]

    cidr = None
    if "--cidr" in argv:
        idx = argv.index("--cidr")
        cidr = argv[idx + 1]
        del argv[idx : idx + 2]
    if cidr:
        os.environ["MEIJU_LAN_CIDR"] = cidr

    if cmd == "keys":
        creds = credentials_from_env()
        if creds is None:
            raise SystemExit(
                "未提供账号密码。请设置环境变量后重试：\n"
                '  $env:MEIJU_ACCOUNT="你的美居账号"\n'
                '  $env:MEIJU_PASSWORD="你的密码"'
            )
        asyncio.run(fetch_all_keys(*creds))
    elif cmd == "discover":
        asyncio.run(cmd_discover(target))
    elif cmd == "status":
        asyncio.run(cmd_status(int(argv[1]), target))
    elif cmd == "dump":
        asyncio.run(cmd_dump(int(argv[1]), target))
    elif cmd == "set":
        asyncio.run(cmd_set(int(argv[1]), argv[2], argv[3], target))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
