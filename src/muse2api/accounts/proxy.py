from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit

from pydantic import BaseModel, Field

from ..errors import InvalidRequest


class AccountProxy(BaseModel):
    """One account's HTTP or SOCKS5 proxy, with credentials separate from the URL."""

    server: str
    username: str = ""
    password: str = Field(default="", repr=False)

    def public(self) -> dict[str, object]:
        return {"server": self.server, "username": self.username,
                "has_password": bool(self.password)}

    def httpx_url(self) -> str:
        """A URL for httpx; ``socks5h`` becomes ``socks5`` (socksio does DNS)."""
        parsed = urlsplit(self.server)
        scheme = "socks5" if parsed.scheme == "socks5h" else parsed.scheme
        auth = ""
        if self.username or self.password:
            auth = quote(self.username, safe="") + ":" + quote(self.password, safe="") + "@"
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        return f"{scheme}://{auth}{host}:{parsed.port}"


def normalize_proxy(value: str) -> AccountProxy:
    """Validate ``http(s)``/SOCKS5 input and strip credentials from the server.

    Credentials may be supplied in the URL; they are percent-decoded and moved to
    the separate fields so they are never stored in the normalised server string.
    """
    raw = (value or "").strip()
    if not raw or any(ch.isspace() or ord(ch) < 32 for ch in raw):
        raise InvalidRequest("proxy server must be an http://, socks5:// or socks5h:// URL")
    try:
        parsed = urlsplit(raw)
        if parsed.scheme not in ("http", "socks5", "socks5h") or not parsed.hostname:
            raise ValueError
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError
        port = parsed.port or (1080 if parsed.scheme in ("socks5", "socks5h") else 80)
        if not 1 <= port <= 65535:
            raise ValueError
        host = parsed.hostname
        if any(ch in host for ch in "/\\@%"):
            raise ValueError
        host = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        server = f"{parsed.scheme}://{host}:{port}"
        return AccountProxy(server=server, username=username, password=password)
    except (ValueError, UnicodeError):
        raise InvalidRequest(
            "proxy server must be http://host:port, socks5://host:port or "
            "socks5h://host:port (HTTPS proxy endpoints are not supported)"
        ) from None


def proxy_from_input(server: str, username: str = "", password: str = "",
                     *, existing: AccountProxy | None = None) -> AccountProxy:
    """Build a saved proxy from an API payload.

    A blank password keeps the saved one only when the server and username did
    not change. A changed route never silently inherits the old password.
    """
    parsed = normalize_proxy(server)
    inline = bool(urlsplit(server).username or urlsplit(server).password)
    user = parsed.username if inline else (username or "")
    secret = parsed.password if inline else (password or "")
    if (not secret and existing is not None and existing.server == parsed.server
            and existing.username == user):
        secret = existing.password
    return AccountProxy(server=parsed.server, username=user, password=secret)
