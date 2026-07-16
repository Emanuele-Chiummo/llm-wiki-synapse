"""Base URL validation for provider_config (SEC-BASEURL-1)."""

import ipaddress
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Known provider hosts (allowlist — backwards-compatible with existing configs)
_KNOWN_PROVIDER_HOSTS = {
    "api.anthropic.com",
    "api.openai.com",
    "openai.azure.com",
    "localhost",
    "127.0.0.1",
    "host.docker.internal",  # Critical: Docker Desktop / TrueNAS (SEC-BASEURL-1 gotcha)
}


def _is_private_ipv4(ip_str: str) -> bool:
    """Check if an IP is in RFC1918 private range."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private
    except ValueError:
        return False


def validate_base_url(base_url: str | None) -> None:
    """
    Validate base_url for provider config (SEC-BASEURL-1).

    Raises ValueError if the URL is disallowed.

    Rules:
    1. Must use http or https scheme
    2. Host must be one of:
       - localhost, 127.0.0.1
       - host.docker.internal (Docker Desktop / TrueNAS)
       - Private IPv4 (RFC1918: 10.x, 172.16-31.x, 192.168.x)
       - Known provider hosts (api.anthropic.com, api.openai.com, etc.)
    """
    if not base_url:
        return  # Empty/None is OK

    try:
        parsed = urlparse(base_url)
    except Exception as exc:
        raise ValueError(f"Invalid URL: {base_url}") from exc

    # Require http or https
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"base_url scheme must be http or https, not {parsed.scheme!r}"
        )

    # Extract hostname (strip port)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"base_url has no hostname: {base_url}")

    # Check allowlist
    if hostname in _KNOWN_PROVIDER_HOSTS:
        return  # Allowed

    # Check private IP ranges
    if _is_private_ipv4(hostname):
        return  # Allowed (RFC1918)

    # Check for *.openai.azure.com (Azure OpenAI)
    if hostname.endswith(".openai.azure.com"):
        return  # Allowed

    # Reject everything else
    raise ValueError(
        f"base_url host {hostname!r} is not allowed. Must be one of: "
        f"localhost, 127.0.0.1, host.docker.internal, private RFC1918 IP, "
        f"or a known provider host (api.anthropic.com, api.openai.com, *.openai.azure.com). "
        f"base_url={base_url}"
    )
