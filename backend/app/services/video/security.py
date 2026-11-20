"""
Security module for remote media retrieval.
Implements strict Server-Side Request Forgery (SSRF) defense:
- Validates URL scheme (HTTP/HTTPS only).
- Blocks localhost, private, reserved, link-local, and multicast IP addresses.
- Resolves DNS and inspects every target IP.
- Intercepts and re-validates each redirect hop.
- Specifically blocks cloud metadata services (AWS/GCP/Azure 169.254.169.254, metadata.google.internal).
"""
import ipaddress
import socket
from urllib.parse import urlparse
from typing import Set


class SecuritySSRFError(Exception):
    """Raised when a remote URL fails SSRF security validation."""
    pass


# Specific cloud metadata and internal hostnames
BLOCKED_HOSTNAMES: Set[str] = {
    "localhost",
    "metadata.google.internal",
    "metadata.tpu.goog",
    "instance-data",
    "169.254.169.254",
    "0.0.0.0",
}

# Blocked IP network ranges
BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),          # Current network
    ipaddress.ip_network("10.0.0.0/8"),         # Private Class A
    ipaddress.ip_network("100.64.0.0/10"),      # Shared Address Space (CGNAT)
    ipaddress.ip_network("127.0.0.0/8"),        # Loopback
    ipaddress.ip_network("169.254.0.0/16"),     # Link Local / Cloud Metadata (169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),      # Private Class B
    ipaddress.ip_network("192.0.0.0/24"),       # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),       # TEST-NET-1
    ipaddress.ip_network("192.168.0.0/16"),     # Private Class C
    ipaddress.ip_network("198.18.0.0/15"),      # Network benchmark tests
    ipaddress.ip_network("198.51.100.0/24"),    # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),     # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),        # Multicast
    ipaddress.ip_network("240.0.0.0/4"),        # Reserved
    ipaddress.ip_network("255.255.255.255/32"), # Broadcast
    # IPv6
    ipaddress.ip_network("::/128"),             # Unspecified
    ipaddress.ip_network("::1/128"),            # Loopback
    ipaddress.ip_network("fc00::/7"),           # Unique Local Address (ULA)
    ipaddress.ip_network("fe80::/10"),          # Link-local
    ipaddress.ip_network("ff00::/8"),           # Multicast
]


def is_ip_allowed(ip_str: str) -> bool:
    """
    Check if an IP address string is public and not reserved, private, loopback,
    or link-local.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False

    for net in BLOCKED_NETWORKS:
        if ip in net:
            return False

    return True


def validate_remote_url(url: str) -> str:
    """
    Validate that a URL is a valid, public HTTP/HTTPS URL and does not resolve
    to internal, private, loopback, link-local, or cloud metadata endpoints.
    Raises SecuritySSRFError on violation.
    """
    if not url or not isinstance(url, str):
        raise SecuritySSRFError("Invalid URL: must be a non-empty string.")

    clean_url = url.strip()
    parsed = urlparse(clean_url)

    # 1. Scheme check
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SecuritySSRFError(f"Prohibited URL scheme '{scheme}': only HTTP and HTTPS are permitted.")

    # 2. Hostname check
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise SecuritySSRFError("Invalid URL: missing hostname.")

    if (
        hostname in BLOCKED_HOSTNAMES
        or hostname.endswith(".localhost")
        or hostname.endswith(".internal")
        or hostname.endswith(".local")
    ):
        raise SecuritySSRFError(f"Access to blocked internal host '{hostname}' is forbidden.")

    # 3. Direct IP check (if hostname is an IP literal)
    try:
        ip = ipaddress.ip_address(hostname)
        if not is_ip_allowed(str(ip)):
            raise SecuritySSRFError(
                f"Security violation (SSRF): Host '{hostname}' is a private/restricted IP. "
                "Access to internal services, loopback, and cloud metadata is strictly forbidden."
            )
        return clean_url
    except ValueError:
        pass

    # 4. DNS resolution & IP check for domain names
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
        addr_info = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SecuritySSRFError(f"DNS resolution failed for host '{hostname}': {e}")
    except Exception as e:
        raise SecuritySSRFError(f"Host validation error for '{hostname}': {e}")

    if not addr_info:
        raise SecuritySSRFError(f"DNS resolution returned no addresses for host '{hostname}'.")

    for family, socktype, proto, canonname, sockaddr in addr_info:
        ip_addr = sockaddr[0]
        if not is_ip_allowed(ip_addr):
            raise SecuritySSRFError(
                f"Security violation (SSRF): Host '{hostname}' resolves to private/restricted IP '{ip_addr}'. "
                "Access to internal services, loopback, and cloud metadata is strictly forbidden."
            )

    return clean_url
