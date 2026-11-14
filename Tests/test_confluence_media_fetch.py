"""Unit-тесты загрузки медиавложений Confluence с редиректами и нормализацией домена."""

import json
from unittest.mock import MagicMock

import aiohttp
import pytest

from Scripts.bot.handlers import base as base_handler


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b"", headers: dict | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, handler):
        self._handler = handler
        self.calls: list[dict] = []

    def get(self, url, auth=None, timeout=None, allow_redirects=True):
        self.calls.append({
            "url": url,
            "auth": auth,
            "allow_redirects": allow_redirects,
        })
        return self._handler(url, auth, allow_redirects)


def test_get_confluence_origin_strips_space_path(monkeypatch):
    monkeypatch.setattr(
        base_handler,
        "CONFLUENCE_BASE_URL",
        "https://axon-bim.atlassian.net/wiki/spaces/~70121c7daeab8fb0d42e5a9d66704bad7c375/overview?homepageId=196707",
    )
    monkeypatch.setattr(base_handler, "CONFLUENCE_PUBLIC_URL", "https://wiki.axonbim.internal")

    assert base_handler._get_confluence_origin() == "https://axon-bim.atlassian.net"


def test_get_confluence_origin_falls_back_to_public_url(monkeypatch):
    monkeypatch.setattr(base_handler, "CONFLUENCE_BASE_URL", "https://docs.yourcompany.internal")
    monkeypatch.setattr(base_handler, "CONFLUENCE_PUBLIC_URL", "https://axon-bim.atlassian.net")

    assert base_handler._get_confluence_origin() == "https://axon-bim.atlassian.net"


@pytest.mark.asyncio
async def test_fetch_media_bytes_rewrites_old_domain_to_configured_origin(monkeypatch):
    monkeypatch.setattr(
        base_handler,
        "CONFLUENCE_BASE_URL",
        "https://axon-bim.atlassian.net/wiki/spaces/~70121c7/overview",
    )
    monkeypatch.setattr(base_handler, "CONFLUENCE_EMAIL", "bot@example.com")
    monkeypatch.setattr(base_handler, "CONFLUENCE_API_TOKEN", "secret-token")
    monkeypatch.setattr(base_handler, "MEDIA_CACHE_DIR", MagicMock())

    png_bytes = b"\x89PNG\r\n\x1a\nfake"

    def handler(url, auth, allow_redirects):
        assert allow_redirects is False
        assert auth is not None
        assert url.startswith("https://axon-bim.atlassian.net/")
        if "/wiki/rest/api/content/655918/child/attachment" in url:
            payload = json.dumps({"results": [{"_links": {"download": "/download/attachments/655918/13044805.png"}}]}).encode()
            return _FakeResponse(200, payload, {"Content-Type": "application/json"})
        if "/wiki/download/attachments/655918/13044805.png" in url:
            return _FakeResponse(200, png_bytes, {"Content-Type": "image/png"})
        return _FakeResponse(404)

    session = _FakeSession(handler)
    result = await base_handler.fetch_media_bytes(
        session,
        "https://ivankiryushovsworkspace-25173842.atlassian.net/wiki/download/attachments/655918/13044805.png",
    )

    assert result == (png_bytes, "13044805.png")
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_fetch_with_confluence_auth_preserves_auth_on_atlassian_redirect(monkeypatch):
    monkeypatch.setattr(base_handler, "CONFLUENCE_BASE_URL", "https://docs.yourcompany.internal")
    monkeypatch.setattr(base_handler, "CONFLUENCE_PUBLIC_URL", "https://wiki.axonbim.internal")

    auth = aiohttp.BasicAuth("bot@example.com", "secret")
    png_bytes = b"\x89PNG\r\n\x1a\nredirected"

    def handler(url, req_auth, allow_redirects):
        assert req_auth == auth
        if "ivankiryushovsworkspace-25173842.atlassian.net" in url:
            return _FakeResponse(
                302,
                headers={"Location": "https://axon-bim.atlassian.net/wiki/download/attachments/655918/13044805.png"},
            )
        return _FakeResponse(200, png_bytes, {"Content-Type": "image/png"})

    session = _FakeSession(handler)
    status, content_type, data = await base_handler._fetch_with_confluence_auth(
        session,
        "https://ivankiryushovsworkspace-25173842.atlassian.net/wiki/download/attachments/655918/13044805.png",
        auth,
    )

    assert status == 200
    assert content_type == "image/png"
    assert data == png_bytes
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_fetch_with_confluence_auth_blocks_untrusted_redirect(monkeypatch):
    monkeypatch.setattr(base_handler, "CONFLUENCE_BASE_URL", "https://axon-bim.atlassian.net")
    auth = aiohttp.BasicAuth("bot@example.com", "secret")

    def handler(url, req_auth, allow_redirects):
        return _FakeResponse(302, headers={"Location": "https://evil.example.com/steal"})

    session = _FakeSession(handler)
    status, _, data = await base_handler._fetch_with_confluence_auth(
        session,
        "https://axon-bim.atlassian.net/wiki/download/attachments/655918/13044805.png",
        auth,
    )

    assert status == 302
    assert data == b""
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_fetch_media_bytes_rejects_html_login_page(monkeypatch):
    monkeypatch.setattr(base_handler, "CONFLUENCE_BASE_URL", "https://axon-bim.atlassian.net")
    monkeypatch.setattr(base_handler, "CONFLUENCE_EMAIL", "bot@example.com")
    monkeypatch.setattr(base_handler, "CONFLUENCE_API_TOKEN", "secret")

    def handler(url, auth, allow_redirects):
        return _FakeResponse(200, b"<html>Login</html>", {"Content-Type": "text/html; charset=utf-8"})

    session = _FakeSession(handler)
    result = await base_handler.fetch_media_bytes(
        session,
        "https://axon-bim.atlassian.net/wiki/download/attachments/655918/13044805.png",
    )

    assert result is None



@pytest.mark.asyncio
async def test_fetch_with_confluence_auth_follows_media_cdn_without_auth(monkeypatch):
    monkeypatch.setattr(base_handler, "CONFLUENCE_BASE_URL", "https://axon-bim.atlassian.net")
    auth = aiohttp.BasicAuth("bot@example.com", "secret")
    png_bytes = b"\x89PNG\r\n\x1a\ncdn"

    def handler(url, req_auth, allow_redirects):
        if "axon-bim.atlassian.net" in url:
            assert req_auth == auth
            return _FakeResponse(
                302,
                headers={"Location": "https://api.media.atlassian.com/file/abc/binary?token=signed"},
            )
        assert url.startswith("https://api.media.atlassian.com/")
        assert req_auth is None
        return _FakeResponse(200, png_bytes, {"Content-Type": "image/png"})

    session = _FakeSession(handler)
    status, content_type, data = await base_handler._fetch_with_confluence_auth(
        session,
        "https://axon-bim.atlassian.net/wiki/rest/api/content/655918/child/attachment/att328470/download",
        auth,
    )

    assert status == 200
    assert content_type == "image/png"
    assert data == png_bytes
    assert len(session.calls) == 2
