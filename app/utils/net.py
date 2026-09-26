"""The client's address behind Render's proxies (Batch 27, ADR 0016).

Measured in production with a forged header, X-Forwarded-For arrives as

    <whatever the client wrote>,<client, added by Cloudflare>,
    <Cloudflare edge, added by Render>, <Render internal>

so the client is the rightmost entry that is neither internal nor a Cloudflare
edge. Whatever a client writes sits to the left of it and is never reached.
"""
from __future__ import annotations

import ipaddress

# https://www.cloudflare.com/ips/, last changed there on 2023-09-28; checked 2026-09-26.
CLOUDFLARE = tuple(ipaddress.ip_network(net) for net in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
))
# The proxies' own networks: private, carrier-grade NAT, loopback, link-local.
INTERNAL = tuple(ipaddress.ip_network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
    "127.0.0.0/8", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
))
PROXIES = INTERNAL + CLOUDFLARE


def _is_proxy(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Compared within one IP version only: `in` does not check it for us.
    return any(address.version == net.version and address in net for net in PROXIES)


def client_address(forwarded: str | None, peer: str | None) -> str:
    """The rightmost address in X-Forwarded-For that is not a proxy's; without
    one, the peer the connection came from."""
    for entry in reversed((forwarded or "").split(",")):
        try:
            address = ipaddress.ip_address(entry.strip())
        except ValueError:
            continue
        if not _is_proxy(address):
            return str(address)
    return peer or "unknown"
