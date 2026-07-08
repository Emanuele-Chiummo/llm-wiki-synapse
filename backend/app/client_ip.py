"""
Shared client-IP resolution (ADR-0033 §2.3).

Extracted so both the source-classification middleware (``app.main``) and the
rate limiter (``app.rate_limit``) resolve the effective client IP through the
SAME trusted-proxy logic — no divergence, no ``app.main`` import from a router.
"""

from __future__ import annotations

import ipaddress

from starlette.types import Scope

from app.config import settings


def resolve_source_ip(scope: Scope) -> str | None:
    """
    Resolve the effective client IP for a request (ADR-0033 §2.3).

    Trust model:
    1. Default: use scope["client"][0] (transport peer — the actual TCP peer ASGI
       reports). Never trusts X-Forwarded-For by default.
    2. If the transport peer is in MCP_TRUSTED_PROXIES (settings.mcp_trusted_proxies_list),
       read the LAST X-Forwarded-For entry appended by that proxy (proxy-attested client).
       "Last" means rightmost non-empty hop after stripping the proxy's own append
       — practically the last comma-separated IP in the XFF chain NOT added by the proxy.
    3. On any parse failure → return None (caller treats as PUBLIC — fail-safe).

    CF-Connecting-IP / CF-Ray are intentionally NOT used for IP resolution here
    (they are PUBLIC *signals* handled separately in _classify_source).
    """
    try:
        peer_ip: str = scope["client"][0]
    except (KeyError, TypeError, IndexError):
        return None  # no transport peer → PUBLIC (fail-safe)

    trusted = settings.mcp_trusted_proxies_list
    if not trusted:
        return peer_ip  # default: trust only the transport peer

    # Check if peer is trusted
    peer_is_trusted = False
    for cidr_or_ip in trusted:
        try:
            network = ipaddress.ip_network(cidr_or_ip.strip(), strict=False)
            if ipaddress.ip_address(peer_ip) in network:
                peer_is_trusted = True
                break
        except (ValueError, TypeError):
            continue

    if not peer_is_trusted:
        return peer_ip  # peer not trusted → use peer IP as-is

    # Peer is trusted: extract the last XFF hop (proxy-attested client).
    headers: dict[bytes, bytes] = dict(scope.get("headers", []))
    xff: bytes = headers.get(b"x-forwarded-for", b"")
    if not xff:
        return peer_ip  # no XFF header from trusted proxy → use peer

    hops = [h.strip() for h in xff.decode("utf-8", errors="replace").split(",")]
    hops = [h for h in hops if h]
    if not hops:
        return peer_ip

    # Take the LAST hop (rightmost) — the proxy-attested client IP.
    # The leftmost is client-controlled; the rightmost is the most recently appended.
    return hops[-1]
