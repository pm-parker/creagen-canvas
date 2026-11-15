"""SSRF 防护：外部 URL 目标校验（DNS 解析后拒私网/环回/链路本地/保留段）。"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UrlBlocked(ValueError):
    """目标地址不允许（私网/环回/保留段，或 URL 形态非法）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _is_blocked_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def assert_public_http_url(url: str) -> None:
    """校验 http(s) URL 且解析出的所有地址均为公网；违规抛 UrlBlocked。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UrlBlocked("URL_INVALID", "仅支持 http(s) URL")
    host = parsed.hostname
    # 字面 IP 直接判定
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as e:
            raise UrlBlocked("DNS_FAILED", f"域名解析失败: {host}") from e
        ips = []
        for info in infos:
            addr = str(info[4][0])
            # IPv6 带 scope（fe80::1%eth0）剥离
            ips.append(addr.split("%", 1)[0])
    for ip in ips:
        if _is_blocked_ip(ip):
            raise UrlBlocked(
                "SSRF_BLOCKED", f"目标地址不允许访问（内网/保留段）: {host}"
            )
