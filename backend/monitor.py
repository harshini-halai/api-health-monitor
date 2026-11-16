import ipaddress
import logging
import socket
import time
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from backend import config
from backend.models import DEGRADED, DOWN, OPERATIONAL

log = logging.getLogger("whm.monitor")

MAX_REDIRECTS = 5
MAX_BODY_BYTES = 256 * 1024  # only read this much HTML to find the <title>
SLOW_MS = 2000  # a 2xx slower than this is "degraded"
TOTAL_TIMEOUT = 30  # hard cap for one check, including redirects
ALLOWED_PORTS = {80, 443, 8080, 8443}
HEADERS = {
    "User-Agent": "WebsiteHealthMonitor/1.0 (+uptime check)",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}


class UnsafeURLError(ValueError):
    """The URL is invalid or points somewhere we refuse to connect to."""


def coerce_scheme(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    return url


def normalize_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path or "/", "", p.query, ""))


def _ip_is_public(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def validate_public_url(url: str) -> str:
    """Validate a URL and make sure every IP it resolves to is public.
    Returns the normalized URL or raises UnsafeURLError."""
    if len(url) > 2048:
        raise UnsafeURLError("URL is too long")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("Only HTTP and HTTPS URLs are allowed")
    if not parsed.hostname:
        raise UnsafeURLError("Invalid URL")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise UnsafeURLError("Invalid port in URL")
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError("Only ports 80, 443, 8080 and 8443 can be monitored")
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise UnsafeURLError("DNS lookup failed: this domain could not be resolved")
    if not infos:
        raise UnsafeURLError("DNS lookup failed: this domain could not be resolved")
    for info in infos:
        ip = info[4][0].split("%")[0]
        if not _ip_is_public(ip):
            raise UnsafeURLError("This address points to a private or reserved network and cannot be monitored")
    return normalize_url(url)


def classify(status_code: int, response_time_ms: float) -> str:
    """2xx fast -> operational | 2xx slow, 3xx, 4xx -> degraded | 5xx -> down."""
    if status_code >= 500:
        return DOWN
    if 200 <= status_code < 300:
        return DEGRADED if response_time_ms > SLOW_MS else OPERATIONAL
    return DEGRADED


def _result(url: str, **kw) -> dict:
    base = {
        "requested_url": url,
        "final_url": None,
        "status_code": None,
        "response_time_ms": None,
        "is_reachable": False,
        "health_status": DOWN,
        "page_title": None,
        "content_type": None,
        "server": None,
        "redirect_count": 0,
        "error_message": None,
    }
    base.update(kw)
    return base


def _extract_title(response, content_type: str):
    if "text/html" not in content_type.lower():
        return None
    try:
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=16384):
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_BODY_BYTES:
                break
        html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        if soup.title and soup.title.string:
            return " ".join(soup.title.string.split())[:300]
    except Exception:
        pass
    return None


def check_website(url: str) -> dict:
    """Check one website. Never raises: failures are returned as a 'down' result."""
    started = time.perf_counter()
    current = url
    redirects = 0
    response = None
    try:
        with requests.Session() as session:
            while True:
                if time.perf_counter() - started > TOTAL_TIMEOUT:
                    raise requests.exceptions.Timeout()
                validate_public_url(current)  # every hop is validated, not just the first
                response = session.get(
                    current,
                    timeout=config.REQUEST_TIMEOUT_SECONDS,
                    allow_redirects=False,
                    stream=True,
                    headers=HEADERS,
                )
                if response.is_redirect:
                    if redirects >= MAX_REDIRECTS:
                        raise requests.exceptions.TooManyRedirects()
                    nxt = urljoin(current, response.headers.get("Location", ""))
                    response.close()
                    current = nxt
                    redirects += 1
                    continue
                break

            # Response time = time until response headers arrived (incl. redirects).
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            code = response.status_code
            content_type = response.headers.get("Content-Type")
            server = response.headers.get("Server")
            title = _extract_title(response, content_type or "")
            health = classify(code, elapsed)

            error = None
            if code >= 500:
                error = f"Server error (HTTP {code})"
            elif code >= 400:
                error = f"Client error (HTTP {code}): the server responded but did not serve the page normally"
            elif code >= 300 or code < 200:
                error = f"Unexpected response (HTTP {code})"
            elif health == DEGRADED:
                error = f"Slow response ({elapsed:.0f} ms, threshold {SLOW_MS} ms)"

            return _result(
                url,
                final_url=current,
                status_code=code,
                response_time_ms=elapsed,
                is_reachable=True,
                health_status=health,
                page_title=title,
                content_type=content_type[:200] if content_type else None,
                server=server[:200] if server else None,
                redirect_count=redirects,
                error_message=error,
            )
    except UnsafeURLError as e:
        return _result(url, redirect_count=redirects, error_message=str(e))
    except requests.exceptions.Timeout:
        return _result(url, redirect_count=redirects, error_message="Request timed out")
    except requests.exceptions.SSLError:
        return _result(url, redirect_count=redirects, error_message="SSL/TLS error: the certificate could not be verified")
    except requests.exceptions.TooManyRedirects:
        return _result(url, redirect_count=redirects, error_message="Too many redirects")
    except requests.exceptions.ConnectionError:
        return _result(url, redirect_count=redirects, error_message="Could not connect to the server")
    except (requests.exceptions.InvalidURL, requests.exceptions.InvalidSchema, requests.exceptions.MissingSchema):
        return _result(url, redirect_count=redirects, error_message="Invalid URL")
    except requests.exceptions.RequestException as e:
        return _result(url, redirect_count=redirects, error_message=f"Request failed ({type(e).__name__})")
    except Exception:
        log.exception("Unexpected error while checking %s", url)
        return _result(url, redirect_count=redirects, error_message="Unexpected error while checking the website")
    finally:
        if response is not None:
            response.close()