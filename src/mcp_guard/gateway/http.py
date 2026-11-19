"""MCP Streamable HTTP on top of the session manager (aiohttp).

URL per server: /<token>/<server>/mcp

  POST    one JSON-RPC message. Requests get an answer (200): as an SSE stream with
          one event when the client accepts text/event-stream, else as JSON
          (config: response_mode). Notifications and responses get 202.
          `initialize` starts a session and the answer carries the Mcp-Session-Id
          header. Other messages must send it.
  DELETE  ends the session.
  GET     405. This version has no server-to-client stream (allowed by the spec).

Requests are never logged with their path, because the path holds the token.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import signal
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import jsonrpc
from ..approval.client import DaemonDecider
from ..config import Config, GatewayConfig
from ..jsonrpc import Kind
from ..policy import Policy
from . import auth
from .session import SessionManager, TooManySessions

log = logging.getLogger("mcp_guard.gateway")

MAX_BODY_BYTES = 4 * 1024 * 1024
SESSION_HEADER = "Mcp-Session-Id"
JANITOR_SECONDS = 60

# Typed keys for per-request data (newer aiohttp prefers them; older versions use plain strings).
_RequestKey = getattr(web, "RequestKey", None)


def _key(name: str, kind: type) -> Any:
    return _RequestKey(name, kind) if _RequestKey is not None else name


STARTED = _key("mcp_guard_started", float)
MCP_METHOD = _key("mcp_guard_method", str)
MCP_SESSION = _key("mcp_guard_session", str)
MCP_FORMAT = _key("mcp_guard_format", str)


def _json(status: int, body: Any, headers: dict[str, str] | None = None) -> web.Response:
    return web.Response(status=status, body=json.dumps(body, separators=(",", ":")).encode("utf-8"),
                        content_type="application/json", headers=headers)


HIDDEN_HEADERS = {"authorization", "cookie", "set-cookie", "proxy-authorization"}


def _safe_headers(headers: Any) -> dict[str, str]:
    """Headers for debug logs, without credentials."""
    return {k: ("***" if k.lower() in HIDDEN_HEADERS else v) for k, v in headers.items()}


def wants_sse(accept: str | None, mode: str) -> bool:
    if mode == "sse":
        return True
    if mode == "json":
        return False
    return "text/event-stream" in (accept or "").lower()


async def _sse(request: web.Request, body: Any, headers: dict[str, str]) -> web.StreamResponse:
    """Send one JSON-RPC answer as a single SSE event, then end the stream."""
    response = web.StreamResponse(status=200, headers={
        **headers,
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
    })
    await response.prepare(request)
    data = json.dumps(body, separators=(",", ":"))
    await response.write(("event: message\ndata: %s\n\n" % data).encode("utf-8"))
    await response.write_eof()
    return response


def _safe_id(msg: jsonrpc.Message) -> Any:
    rid = msg.id
    return rid if isinstance(rid, (str, int)) and not isinstance(rid, bool) else None


def create_app(gw: GatewayConfig, manager: SessionManager) -> web.Application:
    networks = auth.parse_networks(gw.allowed_ips)

    @web.middleware
    async def guard(request: web.Request, handler):
        if not auth.token_matches(request.match_info.get("token"), gw.token):
            # Log only the first path segment that is not the token, never the token itself.
            log.warning("refused %s with a wrong or missing token (peer %s, path starts %r)",
                        request.method, request.remote, request.path[:24] if not request.match_info else "/<token>")
            raise web.HTTPNotFound()
        if not auth.origin_allowed(request.headers.get("Origin")):
            log.warning("refused a request from origin %r", request.headers.get("Origin"))
            raise web.HTTPForbidden(text="403: Forbidden. This Origin is not allowed.")
        ip = auth.client_ip(request.remote, request.headers, gw.trust_proxy_header)
        if not auth.ip_allowed(ip, networks):
            header_state = "present" if auth.PROXY_IP_HEADER in request.headers else "missing"
            log.warning("refused a request from %s: not in allowed_ips (%s header %s)",
                        ip, auth.PROXY_IP_HEADER, header_state)
            # Only a caller with the right token gets here, and it only learns its own IP.
            raise web.HTTPForbidden(text="403: Forbidden. Client IP %s is not in allowed_ips (%s header %s)."
                                         % (ip, auth.PROXY_IP_HEADER, header_state))
        if log.isEnabledFor(logging.DEBUG):
            log.debug("request headers: %s", _safe_headers(request.headers))
        request[STARTED] = asyncio.get_running_loop().time()
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            _log_request(request, exc.status)
            raise
        _log_request(request, response.status)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("response headers: %s", _safe_headers(response.headers))
        return response

    def _log_request(request: web.Request, status: int) -> None:
        sid = request.get(MCP_SESSION) or request.headers.get(SESSION_HEADER) or ""
        took_ms = (asyncio.get_running_loop().time() - request.get(STARTED, 0.0)) * 1000
        log.info("%s %s %s -> %d %s in %d ms (protocol %s, session %s, from %s)",
                 request.method,
                 request.match_info.get("server", "?"),
                 request.get(MCP_METHOD, "-"),
                 status,
                 request.get(MCP_FORMAT, ""),
                 took_ms,
                 request.headers.get("MCP-Protocol-Version", "-"),
                 sid[:6] + "..." if sid else "-",
                 auth.client_ip(request.remote, request.headers, gw.trust_proxy_header))

    async def mcp(request: web.Request) -> web.StreamResponse:
        server = request.match_info["server"]
        if server not in gw.servers:
            raise web.HTTPNotFound()
        if request.method == "DELETE":
            sid = request.headers.get(SESSION_HEADER)
            if sid and await manager.close(server, sid):
                return web.Response(status=200)
            raise web.HTTPNotFound()
        if request.method != "POST":
            raise web.HTTPMethodNotAllowed(request.method, ["POST", "DELETE"])

        body = await request.read()  # aiohttp answers 413 above MAX_BODY_BYTES
        try:
            msg = jsonrpc.parse_line(body)
        except jsonrpc.ParseError:
            request[MCP_METHOD] = "(bad JSON)"
            return _json(400, jsonrpc.make_error(None, jsonrpc.PARSE_ERROR, "Parse error"))
        request[MCP_METHOD] = msg.method or msg.kind.value
        if msg.kind is Kind.INVALID:
            return _json(400, jsonrpc.make_error(_safe_id(msg), jsonrpc.INVALID_REQUEST, "Invalid request"))
        if msg.kind is Kind.BATCH:
            return _json(400, jsonrpc.make_error(None, jsonrpc.INVALID_REQUEST, "Batches are not supported."))

        request_id = msg.id if msg.kind is Kind.REQUEST else None
        if gw.stateless:
            return await stateless(request, server, msg, body, request_id)
        if msg.kind is Kind.REQUEST and msg.method == "initialize":
            try:
                session = await manager.create(server)
            except TooManySessions:
                return _json(503, jsonrpc.make_error(request_id, jsonrpc.INTERNAL_ERROR,
                                                     "Too many open sessions for this server."))
            except OSError as exc:
                log.error("could not start MCP server %r: %s", server, exc)
                return _json(502, jsonrpc.make_error(request_id, jsonrpc.INTERNAL_ERROR,
                                                     "The MCP server could not be started."))
        else:
            sid = request.headers.get(SESSION_HEADER)
            if not sid:
                return _json(400, jsonrpc.make_error(request_id, jsonrpc.INVALID_REQUEST,
                                                     "Missing Mcp-Session-Id header."))
            found = manager.get(server, sid)
            if found is None:
                return _json(404, jsonrpc.make_error(request_id, jsonrpc.INVALID_REQUEST,
                                                     "Unknown or expired session."))
            session = found

        request[MCP_SESSION] = session.id
        answer = await session.handle(msg, body)
        if msg.method == "initialize" and log.isEnabledFor(logging.DEBUG):
            # initialize carries no user data, only versions and capabilities
            log.debug("initialize request: %s", body.decode("utf-8", "replace")[:2000])
            log.debug("initialize answer: %s", json.dumps(answer)[:2000])
        headers = {SESSION_HEADER: session.id}
        if answer is None:
            return web.Response(status=202, headers=headers)
        if wants_sse(request.headers.get("Accept"), gw.response_mode):
            request[MCP_FORMAT] = "sse"
            return await _sse(request, answer, headers)
        return _json(200, answer, headers)

    async def stateless(request: web.Request, server: str, msg: jsonrpc.Message, body: bytes,
                        request_id: Any) -> web.StreamResponse:
        """No Mcp-Session-Id. Every message goes to one shared process per server."""
        try:
            session = await manager.shared(server)
        except OSError as exc:
            log.error("could not start MCP server %r: %s", server, exc)
            return _json(502, jsonrpc.make_error(request_id, jsonrpc.INTERNAL_ERROR,
                                                 "The MCP server could not be started."))
        if msg.kind is Kind.REQUEST and msg.method == "initialize" and session.init_result is not None:
            answer: Any = jsonrpc.make_result(msg.id, session.init_result)  # already initialized: reuse
        elif msg.kind is Kind.NOTIFICATION and msg.method == "notifications/initialized":
            if not session.initialized_sent:
                session.initialized_sent = True
                await session.handle(msg, body)
            answer = None
        else:
            answer = await session.handle(msg, body)
            if msg.method == "initialize" and isinstance(answer, dict) and "result" in answer:
                session.init_result = answer["result"]
        if answer is None:
            return web.Response(status=202)
        if wants_sse(request.headers.get("Accept"), gw.response_mode):
            request[MCP_FORMAT] = "sse"
            return await _sse(request, answer, {})
        return _json(200, answer)

    app = web.Application(client_max_size=MAX_BODY_BYTES, middlewares=[guard])
    app.router.add_route("*", "/{token}/{server}/mcp", mcp)
    return app


def check_gateway_config(config: Config) -> str | None:
    """Return a problem to show the user, or None if the gateway can start."""
    gw = config.gateway
    if gw.token is None:
        return ("No gateway token is set. Add this to the [gateway] table of %s:\n\n"
                "    token = \"%s\"\n" % (config.source or "config.toml", secrets.token_urlsafe(32)))
    if not gw.servers:
        return ("No servers are set for the gateway. Add one, for example:\n\n"
                "    [gateway.servers.files]\n"
                "    command = \"npx\"\n"
                "    args = [\"-y\", \"@modelcontextprotocol/server-filesystem\", \"/Users/you/Documents\"]\n")
    return None


async def serve(config: Config, socket_path: Path, host: str = "127.0.0.1") -> None:
    gw = config.gateway
    decider = DaemonDecider(socket_path, source="web", timeout=config.timeout_seconds)
    manager = SessionManager(gw.servers, decider, Policy(config.gated_methods), config.timeout_seconds)
    runner = web.AppRunner(create_app(gw, manager), access_log=None)  # access logs would leak the token
    await runner.setup()
    site = web.TCPSite(runner, host, gw.port)
    await site.start()

    print("mcp-guard gateway listening on http://%s:%d" % (host, gw.port))
    print("Config:      %s" % (config.source or "defaults"))
    print("Allowed IPs: %s" % ", ".join(gw.allowed_ips))
    print("Mode:        %s, answers: %s" % ("stateless" if gw.stateless else "sessions", gw.response_mode))
    print("Local URLs (keep them secret, the token is the password):")
    for name in sorted(gw.servers):
        print("  %-16s http://%s:%d/%s/%s/mcp" % (name, host, gw.port, gw.token, name))
    print("For claude.ai, use the tunnel's https URL with the same path.")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)

    async def janitor() -> None:
        while True:
            await asyncio.sleep(JANITOR_SECONDS)
            await manager.close_idle()

    cleaner = asyncio.create_task(janitor())
    try:
        await stop.wait()
    finally:
        cleaner.cancel()
        await manager.close_all()
        await runner.cleanup()
        log.info("gateway stopped")


def run_gateway(config: Config, socket_path: Path) -> int:
    problem = check_gateway_config(config)
    if problem:
        print(problem)
        return 2
    try:
        asyncio.run(serve(config, socket_path))
    except OSError as exc:
        log.error("could not start the gateway: %s", exc)
        return 1
    return 0
