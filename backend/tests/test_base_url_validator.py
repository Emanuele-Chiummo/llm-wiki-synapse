"""Tests for base_url validation (SEC-BASEURL-1)."""

import pytest
from app.base_url_validator import validate_base_url


def test_none_base_url_allowed():
    """None/empty base_url is allowed (no validation)."""
    validate_base_url(None)
    validate_base_url("")


def test_localhost_allowed():
    """localhost and 127.0.0.1 are allowed."""
    validate_base_url("http://localhost:8000")
    validate_base_url("https://localhost:8000")
    validate_base_url("http://127.0.0.1:5000")


def test_host_docker_internal_allowed():
    """host.docker.internal is allowed (Docker Desktop / TrueNAS gotcha)."""
    validate_base_url("http://host.docker.internal:11434")


def test_private_ipv4_rfc1918_allowed():
    """Private IPv4 ranges are allowed."""
    validate_base_url("http://10.0.0.1:8000")
    validate_base_url("http://10.255.255.255:8000")
    validate_base_url("http://172.16.0.1:8000")
    validate_base_url("http://172.31.255.255:8000")
    validate_base_url("http://192.168.1.1:8000")
    validate_base_url("http://192.168.255.255:8000")


def test_known_provider_hosts_allowed():
    """Known provider hosts are allowed."""
    validate_base_url("https://api.anthropic.com/v1")
    validate_base_url("https://api.openai.com/v1")
    validate_base_url("https://openai.azure.com/v1")


def test_azure_openai_wildcard_allowed():
    """*.openai.azure.com subdomains are allowed."""
    validate_base_url("https://myorg.openai.azure.com/v1")
    validate_base_url("https://prod-east.openai.azure.com/v1")


def test_invalid_scheme_rejected():
    """Non-http(s) schemes are rejected."""
    with pytest.raises(ValueError, match="scheme must be http or https"):
        validate_base_url("ftp://example.com")
    with pytest.raises(ValueError, match="scheme must be http or https"):
        validate_base_url("file:///etc/passwd")


def test_public_host_rejected():
    """Public hosts not in allowlist are rejected."""
    with pytest.raises(ValueError, match="not allowed"):
        validate_base_url("https://evil.example.com:8000")
    with pytest.raises(ValueError, match="not allowed"):
        validate_base_url("https://8.8.8.8:443")  # Google DNS


def test_no_hostname_rejected():
    """URLs without hostname are rejected."""
    with pytest.raises(ValueError, match="no hostname"):
        validate_base_url("http://")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
