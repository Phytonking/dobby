import logging
import os
from functools import lru_cache
from ipaddress import ip_address, ip_network

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

# Networks a browser may reach the dashboard from in local mode: loopback, RFC1918 and
# unique-local IPv6. Anything else — a VPN or overlay network such as Tailscale, which
# addresses nodes outside these ranges — is opted in through LOCAL_NETWORKS rather than
# being trusted by default.
DEFAULT_NETWORKS = (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7",
)


@lru_cache(maxsize=8)
def _parse_networks(raw: str) -> tuple:
    values = [value.strip() for value in raw.split(",") if value.strip()]
    try:
        return tuple(ip_network(value) for value in values or DEFAULT_NETWORKS)
    except ValueError as error:
        raise RuntimeError(f"LOCAL_NETWORKS contains an invalid network: {error}") from None


def local_networks() -> tuple:
    """Networks counted as local.

    LOCAL_NETWORKS (comma-separated CIDRs) replaces the defaults entirely, so it is the one
    place that decides what "local" means for a given deployment.
    """
    return _parse_networks(os.environ.get("LOCAL_NETWORKS", ""))


def local_mode() -> bool:
    # strip() because .env files are often CRLF; "true\r" must still count.
    return os.environ.get("LOCAL_MODE", "").strip().lower() in ("1", "true", "yes")


def dashboard_urls() -> list[str]:
    """Origins browsers may load the dashboard from, without trailing slashes.

    Comma-separated so one deployment can serve a LAN address and a Tailscale address at
    once; the first entry is the canonical origin that post-login redirects land on.
    LOCAL_MODE pins it to localhost so a server's DASHBOARD_URL can stay in .env.
    """
    if local_mode():
        return ["http://localhost:3000"]
    raw = os.environ.get("DASHBOARD_URL", "") or "http://localhost:3000"
    urls = [url.strip().rstrip("/") for url in raw.split(",") if url.strip()]
    return urls or ["http://localhost:3000"]


def dashboard_url() -> str:
    """The canonical dashboard origin — where post-login redirects send the browser."""
    return dashboard_urls()[0]


def auth_mode() -> str:
    mode = os.environ.get("AUTH_MODE", "oauth")
    if mode not in {"oauth", "local", "both"}:
        raise RuntimeError("AUTH_MODE must be oauth, local, or both")
    return mode


def client_host(request: Request) -> str:
    """The peer address the local-network checks judge, for diagnosing a refusal."""
    return request.client.host if request.client else "unknown"


def is_local_request(request: Request) -> bool:
    try:
        address = ip_address(request.client.host)
        address = getattr(address, "ipv4_mapped", None) or address
        return any(address in network for network in local_networks())
    except (ValueError, AttributeError):
        return False


def require_provider(provider: str, request: Request) -> None:
    mode = auth_mode()
    if (provider == "local" and mode == "oauth") or (provider != "local" and mode == "local"):
        raise HTTPException(404, "Login method disabled")
    if provider == "local" and not is_local_request(request):
        log.warning("local_login_denied client=%s — add its range to LOCAL_NETWORKS to allow it",
                    client_host(request))
        raise HTTPException(403, "Local login requires a connection from the local network")
