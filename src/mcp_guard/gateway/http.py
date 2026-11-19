"""MCP Streamable HTTP on top of the session manager (aiohttp).

URL per server: /<token>/<server>/mcp

  POST    one JSON-RPC message. Requests get a JSON answer (200).
          Notifications and responses get 202. `initialize` starts a session and
          the answer carries the Mcp-Session-Id header. Other messages must send it.
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


def _json(status: int, body: Any, headers: dict[str, str] | None = None) -> web.Response:
    return web.Response(status=status, body=json.dumps(body, separators=(",", ":")).encode("utf-8"),
                        content_type="application/json", headers=headers)


def _safe_id(msg: jsonrpc.Message) -> Any:
    rid = msg.id
    return rid if isinstance(rid, (str, int)) and not isinstance(rid, bool) else None


def create_app(gw: GatewayConfig, manager: SessionManager) -> web.Application:
    networks = auth.parse_networks(gw.allowed_ips)

    @web.middleware
    async def guard(request: web.Request, handler):
        if not auth.token_matches(request.match_info.get("token"), gw.token):
            log.warning("refused a request with a wrong or missing token (peer %s)", request.remote)
            raise web.HTTPNotFound()
        if not auth.origin_allowed(request.headers.get("Origin")):
            log.warning("refused a request from origin %r", request.headers.get("Origin"))
            raise web.HTTPForbidden()
        ip = auth.client_ip(request.remote, request.headers, gw.trust_proxy_header)
        if not auth.ip_allowed(ip, networks):
            log.warning("refused a request from %s: not in allowed_ips", ip)
            raise web.HTTPForbidden()
        return await handler(request)

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
            return _json(400, jsonrpc.make_error(None, jsonrpc.PARSE_ERROR, "Parse error"))
        if msg.kind is Kind.INVALID:
            return _json(400, jsonrpc.make_error(_safe_id(msg), jsonrpc.INVALID_REQUEST, "Invalid request"))
        if msg.kind is Kind.BATCH:
            return _json(400, jsonrpc.make_error(None, jsonrpc.INVALID_REQUEST, "Batches are not supported."))

        request_id = msg.id if msg.kind is Kind.REQUEST else None
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

        answer = await session.handle(msg, body)
        headers = {SESSION_HEADER: session.id}
        if answer is None:
            return web.Response(status=202, headers=headers)
        return _json(200, answer, headers)

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
