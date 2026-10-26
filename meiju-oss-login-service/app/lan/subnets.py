"""网段探测：从多个来源推断应该扫描哪些 CIDR。

来源优先级：
  1. 环境变量 MEIJU_LAN_CIDR（逗号分隔）——显式指定，最高优先级
  2. 已缓存设备地址所在的 /24 —— 知道设备 IP 就等于知道网段，最可靠
  3. 本机网卡网段（排除回环/链路本地/CGNAT）
  4. Windows 路由表中的私有网段 —— 覆盖 Tailscale 这类「三层路由到家庭网段」的情况
"""

from __future__ import annotations

import ipaddress
import os
from typing import Iterable

MAX_CIDRS = 6
MAX_HOSTS = 1024  # 超过这个规模的网段不扫，避免误扫大网


def _normalize(cidr: str) -> ipaddress.IPv4Network | None:
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        return None
    if not isinstance(net, ipaddress.IPv4Network):
        return None
    return net


def _usable(net: ipaddress.IPv4Network) -> bool:
    if net.prefixlen < 22 or net.prefixlen > 30:  # 只扫 /22~ /30
        return False
    if net.is_loopback or net.is_link_local or net.is_multicast:
        return False
    if net.network_address in ipaddress.ip_network("100.64.0.0/10"):  # CGNAT / Tailscale 自身
        return False
    if net.num_addresses > MAX_HOSTS:
        return False
    return True


def _from_env() -> list[ipaddress.IPv4Network]:
    raw = os.environ.get("MEIJU_LAN_CIDR", "")
    nets = []
    for part in raw.split(","):
        if not part.strip():
            continue
        net = _normalize(part)
        if net is not None:
            nets.append(net)
    return nets


def _from_cached_endpoints() -> list[ipaddress.IPv4Network]:
    try:
        from .service import load_endpoints
    except Exception:  # noqa: BLE001
        return []
    nets = []
    for info in load_endpoints().values():
        ip = info.get("ip_address")
        if not ip:
            continue
        net = _normalize(f"{ip}/24")
        if net is not None:
            nets.append(net)
    return nets


def _from_local_interfaces() -> list[ipaddress.IPv4Network]:
    try:
        import ifaddr  # midealocal 已依赖
    except Exception:  # noqa: BLE001
        return []
    nets = []
    try:
        for adapter in ifaddr.get_adapters():
            for ip_obj in adapter.ips:
                raw_ip = ip_obj.ip
                if isinstance(raw_ip, tuple):
                    raw_ip = raw_ip[0]
                prefix = getattr(ip_obj, "network_prefix", 24) or 24
                net = _normalize(f"{raw_ip}/{prefix}")
                if net is not None:
                    nets.append(net)
    except Exception:  # noqa: BLE001
        return []
    return nets


def _from_windows_routes() -> list[ipaddress.IPv4Network]:
    """解析 route print，取私有网段（含 VPN 路由进来的家庭网段）。"""
    if os.name != "nt":
        return []
    try:
        import subprocess

        out = subprocess.run(
            ["route", "print", "-4"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:  # noqa: BLE001
        return []
    nets = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        dest, mask = parts[0], parts[1]
        if dest.count(".") != 3 or mask.count(".") != 3:
            continue
        net = _normalize(f"{dest}/{mask}")
        if net is not None:
            nets.append(net)
    return nets


def detect_cidrs() -> list[str]:
    """返回本次应该扫描的 CIDR 列表（已去重、限量）。"""
    ordered: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()

    def add(nets: Iterable[ipaddress.IPv4Network]) -> None:
        for net in nets:
            key = str(net)
            if key in seen or not _usable(net):
                continue
            seen.add(key)
            ordered.append(net)

    add(_from_env())
    add(_from_cached_endpoints())
    add(_from_local_interfaces())
    add(_from_windows_routes())
    return [str(net) for net in ordered[:MAX_CIDRS]]


def resolve_cidrs(explicit: str | None = None) -> list[str]:
    """把显式参数 / 环境变量 / 自动探测合并成扫描列表。"""
    if explicit:
        nets = [n for n in (_normalize(p) for p in explicit.split(",")) if n]
        if nets:
            return [str(n) for n in nets]
    env_nets = _from_env()
    if env_nets:
        return [str(n) for n in env_nets]
    return detect_cidrs()
