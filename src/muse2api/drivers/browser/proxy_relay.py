from __future__ import annotations

import ipaddress
import socket
import socketserver
import threading
from select import select
from urllib.parse import urlsplit

from ...accounts.proxy import AccountProxy


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise OSError("proxy closed the connection")
        data.extend(chunk)
    return bytes(data)


class _RelayServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        relay: ProxyRelay = self.server.relay  # type: ignore[attr-defined]
        client = self.request
        upstream = None
        try:
            if not relay.track(client):
                return
            client.settimeout(15)
            head = bytearray()
            while not head.endswith(b"\r\n\r\n"):
                chunk = client.recv(1)
                if not chunk:
                    return
                head.extend(chunk)
                if len(head) > 65536:
                    return
            lines = bytes(head[:-4]).split(b"\r\n")
            method, target, version = lines[0].split(b" ", 2)
            # Chromium always CONNECTs for HTTPS. Reject plain HTTP rather than
            # accidentally sending an absolute request to the wrong place.
            if method != b"CONNECT":
                return
            if b"://" in target:
                return
            host_raw, _, port_raw = target.decode("ascii").rpartition(":")
            if not host_raw or not port_raw.isdigit():
                return
            host = host_raw.strip("[]")
            port = int(port_raw)
            if not host or not 1 <= port <= 65535:
                return
            upstream = relay.connect(host, port)
            if not relay.track(upstream):
                return
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            client.settimeout(30)
            upstream.settimeout(30)
            while not relay.closed:
                readable, _, _ = select([client, upstream], [], [], 1)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is client else client).sendall(data)
        except (OSError, ValueError, UnicodeError):
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n"
                               b"Content-Length: 0\r\n\r\n")
            except OSError:
                pass
        finally:
            for sock in (client, upstream):
                if sock is not None:
                    relay.untrack(sock)
                    try:
                        sock.close()
                    except OSError:
                        pass


class ProxyRelay:
    """A loopback HTTP proxy that forwards CONNECTs to one account's proxy.

    Chromium's per-context ``proxyServer`` has no place for proxy credentials and
    does not implement SOCKS authentication, so every account context points at a
    tiny loopback relay that speaks the authenticated upstream protocol.
    """

    def __init__(self, proxy: AccountProxy) -> None:
        self.proxy = proxy
        parsed = urlsplit(proxy.server)
        self.scheme = parsed.scheme
        self.address = (parsed.hostname or "", parsed.port or 80)
        self._sockets: set[socket.socket] = set()
        self._lock = threading.Lock()
        self.closed = False
        self.server = _RelayServer(("127.0.0.1", 0), _RelayHandler)
        self.server.relay = self  # type: ignore[attr-defined]
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()

    def connect(self, host: str, port: int) -> socket.socket:
        sock = socket.create_connection(self.address, timeout=15)
        try:
            if self.scheme in ("socks5", "socks5h"):
                self._socks_handshake(sock, host, port)
            else:
                headers = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}"]
                if self.proxy.username or self.proxy.password:
                    import base64
                    raw = f"{self.proxy.username}:{self.proxy.password}".encode("latin-1")
                    headers.append("Proxy-Authorization: Basic " + base64.b64encode(raw).decode())
                sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("latin-1"))
                response = bytearray()
                while not response.endswith(b"\r\n\r\n"):
                    chunk = sock.recv(1)
                    if not chunk:
                        raise OSError("HTTP proxy closed the connection")
                    response.extend(chunk)
                    if len(response) > 65536:
                        raise OSError("HTTP proxy response too large")
                if not response.startswith(b"HTTP/1.1 200") and not response.startswith(b"HTTP/1.0 200"):
                    raise OSError("HTTP proxy rejected CONNECT")
            return sock
        except Exception:
            sock.close()
            raise

    def _socks_handshake(self, sock: socket.socket, host: str, port: int) -> None:
        method = 2 if self.proxy.username or self.proxy.password else 0
        sock.sendall(bytes((5, 1, method)))
        if _read_exact(sock, 2) != bytes((5, method)):
            raise OSError("SOCKS5 authentication method rejected")
        if method == 2:
            user = self.proxy.username.encode("latin-1")
            secret = self.proxy.password.encode("latin-1")
            if len(user) > 255 or len(secret) > 255:
                raise OSError("SOCKS5 credentials are too long")
            sock.sendall(bytes((1, len(user))) + user + bytes((len(secret),)) + secret)
            if _read_exact(sock, 2) != bytes((1, 0)):
                raise OSError("SOCKS5 authentication failed")
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            if self.scheme == "socks5h":
                name = host.encode("idna")
                if not 1 <= len(name) <= 255:
                    raise OSError("SOCKS5 hostname is invalid") from None
                destination = bytes((3, len(name))) + name
            else:
                info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
                addr = ipaddress.ip_address(info[4][0])
                destination = bytes((1 if addr.version == 4 else 4,)) + addr.packed
        else:
            destination = bytes((1 if addr.version == 4 else 4,)) + addr.packed
        sock.sendall(bytes((5, 1, 0)) + destination + int(port).to_bytes(2, "big"))
        reply = _read_exact(sock, 4)
        if reply[:3] != bytes((5, 0, 0)):
            raise OSError("SOCKS5 connection rejected")
        length = {1: 4, 4: 16}.get(reply[3])
        if reply[3] == 3:
            length = _read_exact(sock, 1)[0]
        if length is None:
            raise OSError("SOCKS5 response is invalid")
        _read_exact(sock, length + 2)

    def track(self, sock: socket.socket) -> bool:
        with self._lock:
            if self.closed:
                return False
            self._sockets.add(sock)
            return True

    def untrack(self, sock: socket.socket) -> None:
        with self._lock:
            self._sockets.discard(sock)

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
            sockets = list(self._sockets)
            self._sockets.clear()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
