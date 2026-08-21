"""
SSRF guard — shared util for outbound HTTP fetches from user-supplied URLs (R13-9, B2).

Applies to all fetches where the URL comes from untrusted external input (e.g. SearXNG
result bodies). Does NOT apply to operator-configured service URLs (SEARXNG_URL,
EMBEDDING_URL, OLLAMA_URL, DATABASE_URL) — those are trusted homelab config.

Guards applied (in order, on every hop):
  1. Scheme allowlist: only ``http`` / ``https``.
  2. Host resolution: resolve hostname (ALL returned addrs, both IPv4 and IPv6) and
     reject any result that falls in a private/reserved range.
  3. Redirect cap: follow up to ``max_redirects`` (default 3) 3xx responses, re-validating
     the target URL (scheme + host) BEFORE each connecting hop.
  4. Timeouts: connect 5 s / read 10 s (configurable by caller).
  5. Response-size cap: the body is streamed and abandoned past ``max_bytes``
     (default 25 MB) — see ``MAX_RESPONSE_BYTES``.

Rejected ranges (task B2 spec):
  - 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16  (RFC1918)
  - 127.0.0.0/8                                  (loopback)
  - 169.254.0.0/16  (link-local — incl. cloud metadata 169.254.169.254)
  - 0.0.0.0/8       ("this" network — incl. 0.0.0.0)
  - ::1/128, fc00::/7, fe80::/10                 (IPv6 loopback, ULA, link-local)
  - additional reserved/documentation ranges     (defence-in-depth)

The SearXNG *query* endpoint (settings.searxng_url) is NOT passed through this function —
it is trusted operator config even when it is a private homelab IP.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

# ── Scheme allowlist ──────────────────────────────────────────────────────────
_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# ── Default redirect cap ──────────────────────────────────────────────────────
MAX_REDIRECTS: int = 3

# ── Default response-body cap (I7 — an outbound fetch is a bounded operation) ─
# safe_fetch() is the ONE path by which untrusted, externally-chosen URLs (web-search
# result links) are fetched, and BOTH callers reach for the whole body at once
# (``resp.content`` in ops/deep_research.py, ``resp.text`` in chat/web_context.py).
# They cap the EXTRACTED TEXT afterwards (deep_research_fetch_max_chars=20_000,
# chat_web_fetch_max_chars=8_000), which does nothing about how many bytes were
# buffered to get there. The read timeout does not bound it either: httpx applies it
# per read operation, not to the whole transfer, so a server that keeps trickling bytes
# streams for as long as it likes. 25 MB matches the generic upload ceiling
# (settings.max_upload_bytes) and is orders of magnitude above what either caller can
# actually use.
MAX_RESPONSE_BYTES: int = 25 * 1024 * 1024

# Headers that describe the ON-THE-WIRE encoding of the body. safe_fetch hands the
# rebuilt Response bytes that httpx has ALREADY decoded, so carrying these over would
# make httpx decode a second time — ``Content-Encoding: gzip`` over plain bytes raises
# httpx.DecodingError on first access to .text/.content.
_ENCODING_HEADERS: frozenset[str] = frozenset(
    {"content-encoding", "content-length", "transfer-encoding"}
)

# ── Private/reserved IP ranges (B2 task spec + defence-in-depth) ─────────────
# Any resolved address in ANY of these ranges → SSRFError.
_PRIVATE_CIDRS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),  # "this" network — incl. literal 0.0.0.0
    ipaddress.ip_network("10.0.0.0/8"),  # RFC1918
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT (RFC6598)
    ipaddress.ip_network("127.0.0.0/8"),  # IPv4 loopback
    ipaddress.ip_network("169.254.0.0/16"),  # link-local incl. cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),  # RFC1918
    ipaddress.ip_network("192.0.0.0/24"),  # IETF protocol (RFC6890)
    ipaddress.ip_network("192.168.0.0/16"),  # RFC1918
    ipaddress.ip_network("198.18.0.0/15"),  # benchmark testing (RFC2544)
    ipaddress.ip_network("198.51.100.0/24"),  # documentation (RFC5737)
    ipaddress.ip_network("203.0.113.0/24"),  # documentation (RFC5737)
    ipaddress.ip_network("240.0.0.0/4"),  # reserved class E
    ipaddress.ip_network("255.255.255.255/32"),  # IPv4 broadcast
    # ── IPv6 ────────────────────────────────────────────────────────────────
    ipaddress.ip_network("::/128"),  # unspecified
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64 (RFC6052)
    ipaddress.ip_network("fc00::/7"),  # ULA (unique-local, incl. fd00::/8)
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ipaddress.ip_network("ff00::/8"),  # IPv6 multicast
)


# ── Public exception ──────────────────────────────────────────────────────────


class SSRFError(ValueError):
    """
    Raised when an outbound URL is blocked by the SSRF guard.

    Inherits from ValueError so callers can catch it alongside other
    validation errors without a new exception hierarchy.
    """


class ResponseTooLargeError(SSRFError):
    """
    Raised when a response body exceeds ``max_bytes`` (see :data:`MAX_RESPONSE_BYTES`).

    Deliberately a subclass of :exc:`SSRFError`: every existing caller already treats an
    ``SSRFError`` as "this URL is not usable, skip it and carry on", which is exactly the
    right handling here — so the cap needs no caller change to degrade gracefully, while
    the distinct type still lets a future caller tell "blocked" from "too big" apart.
    """


# ── Private helpers ───────────────────────────────────────────────────────────


def _is_private_ip(ip_str: str) -> bool:
    """
    Return True iff the given IP string falls in _PRIVATE_CIDRS.

    Fail-closed: parse errors or unrecognised types return True (block, not allow).
    """
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _PRIVATE_CIDRS)
    except (ValueError, TypeError):
        return True  # cannot parse → treat as private, block


def _validate_scheme_and_host(url: str) -> tuple[str, str]:
    """
    Parse *url*, validate scheme, and return ``(scheme, host)``.

    Raises :exc:`SSRFError` on:
    - malformed URL
    - scheme not in ``_ALLOWED_SCHEMES``
    - missing or empty host
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:  # noqa: BLE001
        raise SSRFError(f"Malformed URL {url!r}: {exc}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(f"URL scheme {scheme!r} is not allowed (only http/https): {url!r}")

    host = parsed.hostname
    if not host:
        raise SSRFError(f"Missing host in URL {url!r}")

    return scheme, host


async def _check_host(host: str) -> None:
    """
    Resolve *host* via DNS and raise :exc:`SSRFError` if ANY returned address is private.

    Both IPv4 and IPv6 addresses returned by the resolver are checked.
    Uses ``socket.getaddrinfo`` in a thread executor (non-blocking).

    Raises
    ------
    SSRFError
        Host resolves to one or more private/reserved IPs, or DNS fails.
    """
    loop = asyncio.get_running_loop()
    try:
        infos: list[tuple] = await loop.run_in_executor(  # type: ignore[type-arg]
            None,
            # Lambda captures module-level socket ref → monkeypatch-friendly
            lambda: socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM),
        )
    except OSError as exc:
        raise SSRFError(f"Cannot resolve host {host!r}: {exc}") from exc

    if not infos:
        raise SSRFError(f"DNS returned no addresses for host {host!r}")

    for info in infos:
        # info tuple: (family, type, proto, canonname, sockaddr)
        # sockaddr IPv4: (address, port)  IPv6: (address, port, flow, scope)
        ip: str = info[4][0]
        if _is_private_ip(ip):
            raise SSRFError(
                f"Host {host!r} resolves to private/reserved IP {ip!r} — "
                "blocked by SSRF guard (R13-9)"
            )


# ── Public API ─────────────────────────────────────────────────────────────────


async def _read_capped(
    client: httpx.AsyncClient, request: httpx.Request, max_bytes: int
) -> httpx.Response:
    """
    Send *request* and return a fully-read Response whose body is at most *max_bytes*.

    Streams the body instead of buffering it whole (the httpx default), so an oversized
    response is abandoned mid-transfer rather than after it has already been paid for in
    RAM. A redirect's body is never downloaded at all — only its ``Location`` matters.

    Raises :exc:`ResponseTooLargeError` as soon as the cap is passed, either from the
    advertised ``Content-Length`` (cheap pre-check) or from the bytes actually received
    (authoritative — ``Content-Length`` is absent under chunked transfer-encoding and is
    in any case attacker-supplied).
    """
    response = await client.send(request, stream=True)
    try:
        if response.is_redirect:
            # Only the Location header is used; skip the body entirely.
            return httpx.Response(
                status_code=response.status_code,
                headers=_strip_encoding_headers(response.headers),
                content=b"",
                request=request,
            )

        # NOTE: the parse is deliberately kept OUT of the raise's try/except —
        # ResponseTooLargeError is itself a ValueError (via SSRFError), so guarding the
        # comparison and the raise together would swallow the very error being raised.
        declared = _parse_content_length(response.headers.get("content-length"))
        if declared is not None and declared > max_bytes:
            raise ResponseTooLargeError(
                f"Response for {request.url!s} declares {declared} bytes, "
                f"over the {max_bytes}-byte cap — not downloaded"
            )

        chunks: list[bytes] = []
        received = 0
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > max_bytes:
                raise ResponseTooLargeError(
                    f"Response body for {request.url!s} exceeded the {max_bytes}-byte "
                    "cap — download aborted"
                )
            chunks.append(chunk)

        # Rebuild as a normal (non-streaming) Response so callers keep using .content /
        # .text / .headers exactly as before. aiter_bytes() has already applied any
        # Content-Encoding, hence _strip_encoding_headers (see _ENCODING_HEADERS).
        return httpx.Response(
            status_code=response.status_code,
            headers=_strip_encoding_headers(response.headers),
            content=b"".join(chunks),
            request=request,
        )
    finally:
        await response.aclose()


def _parse_content_length(raw: str | None) -> int | None:
    """Parse a ``Content-Length`` value, or None when absent/unparseable (chunked, junk)."""
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _strip_encoding_headers(headers: httpx.Headers) -> httpx.Headers:
    """Copy *headers* without the wire-encoding ones (see :data:`_ENCODING_HEADERS`)."""
    return httpx.Headers(
        [(k, v) for k, v in headers.multi_items() if k.lower() not in _ENCODING_HEADERS]
    )


async def safe_fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    connect_timeout: float = 5.0,
    read_timeout: float = 10.0,
    max_redirects: int = MAX_REDIRECTS,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> httpx.Response:
    """
    Fetch *url* with SSRF guards applied on EVERY hop (R13-9, B2).

    Pipeline (repeated for each redirect hop):
      1. Validate scheme (http/https only) — :exc:`SSRFError` on violation.
      2. Resolve hostname and check ALL returned IPs — :exc:`SSRFError` if any is private.
      3. Issue a single request with ``follow_redirects=False``, streaming the body and
         abandoning it past *max_bytes* — :exc:`ResponseTooLargeError` on violation.
      4. On 3xx: extract ``Location``, resolve relative refs, loop (up to *max_redirects*).

    Returns the final non-redirect :class:`httpx.Response` on success, fully read.

    Raises
    ------
    SSRFError
        Scheme not allowed, host resolves to a private IP, or redirect limit exceeded.
    ResponseTooLargeError
        Body exceeds *max_bytes* (an ``SSRFError`` subclass — see that class).
    httpx.HTTPError
        Network-level failure (timeout, connection refused, etc.).
    """
    current_url = url

    for hop in range(max_redirects + 1):
        # ── Guards: scheme + host BEFORE connecting ───────────────────────────
        _, host = _validate_scheme_and_host(current_url)
        await _check_host(host)

        # ── Single-hop request (no automatic redirect following) ──────────────
        # httpx.Timeout requires either a positional default OR all four explicit.
        # Use read_timeout as the default and override only the connect timeout.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            follow_redirects=False,
            headers=headers or {},
        ) as client:
            resp = await _read_capped(client, client.build_request("GET", current_url), max_bytes)

        if not resp.is_redirect:
            return resp

        # ── 3xx redirect: validate Location BEFORE following ──────────────────
        if hop >= max_redirects:
            raise SSRFError(
                f"Too many redirects (cap={max_redirects}): " f"last URL was {current_url!r}"
            )

        location = resp.headers.get("location", "").strip()
        if not location:
            raise SSRFError(f"3xx response without a Location header for {current_url!r}")

        # Resolve relative redirects against the current URL (RFC 7231 §7.1.2)
        current_url = urljoin(current_url, location)
        logger.debug("safe_fetch: redirect hop %d → %s", hop + 1, current_url)

    # Unreachable — loop always returns or raises
    raise SSRFError(f"Redirect limit exceeded (max={max_redirects})")
