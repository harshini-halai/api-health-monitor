
import time
import requests

from urllib.parse import urlparse
from bs4 import BeautifulSoup


def validate_url(url: str):
    parsed_url = urlparse(url)

    if parsed_url.scheme not in ["http", "https"]:
        raise ValueError("Only HTTP and HTTPS URLs are allowed")

    if not parsed_url.netloc:
        raise ValueError("Invalid URL")

    return url


def check_website(url: str):

    validate_url(url)

    start_time = time.perf_counter()

    try:
        response = requests.get(
            url,
            timeout=10,
            allow_redirects=True
        )

        end_time = time.perf_counter()

        response_time = round(
            (end_time - start_time) * 1000,
            2
        )

        content_type = response.headers.get(
            "Content-Type", "Unknown"
        )

        server = response.headers.get(
            "Server", "Not disclosed"
        )

        page_title = "Not available"

        if "text/html" in content_type:
            soup = BeautifulSoup(response.text, "html.parser")

            if soup.title and soup.title.string:
                page_title = soup.title.string.strip()

        return {
            "url": url,
            "final_url": response.url,
            "status_code": response.status_code,
            "response_time_ms": response_time,
            "is_available": response.status_code < 500,
            "is_healthy": 200 <= response.status_code < 400,
            "content_type": content_type,
            "server": server,
            "page_title": page_title,
            "redirect_count": len(response.history),
            "message": "Website responded successfully"
        }

    except requests.exceptions.Timeout:
        return {
            "url": url,
            "is_available": False,
            "is_healthy": False,
            "message": "Request timed out"
        }

    except requests.exceptions.RequestException as error:
        return {
            "url": url,
            "is_available": False,
            "is_healthy": False,
            "message": str(error)
        }
