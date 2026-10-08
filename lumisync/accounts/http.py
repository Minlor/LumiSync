"""Bounded HTTPS requests with safe errors; authentication never follows redirects."""

from __future__ import annotations

import requests

from .errors import AccountError


def request_json(session, method: str, url: str, **kwargs) -> dict:
    try:
        response = session.request(
            method, url, timeout=(5, 15), allow_redirects=False, **kwargs
        )
    except requests.RequestException:
        raise AccountError("Could not reach the vendor service. Check your internet connection and try again.", "network") from None
    if response.status_code in (401, 403):
        raise AccountError("The vendor rejected this connection. Check your credentials or sign in again.", "authentication")
    if response.status_code == 429:
        raise AccountError("The vendor rate limit was reached. Wait before trying again.", "rate_limit")
    if not 200 <= response.status_code < 300:
        raise AccountError(f"The vendor service could not complete the request (HTTP {response.status_code}).", "service")
    try:
        result = response.json()
    except ValueError:
        raise AccountError("The vendor returned an unreadable response.", "response") from None
    if not isinstance(result, dict):
        raise AccountError("The vendor returned an unexpected response.", "response")
    return result
