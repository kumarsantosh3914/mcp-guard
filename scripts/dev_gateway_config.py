#!/usr/bin/env python3
"""Development helper: write a gateway config that serves the fake test server.

The owner runs this, not an agent, because it writes a file outside this project.

Usage:
  .venv/bin/python scripts/dev_gateway_config.py [--local] [--keep-token] [--allow-ip RANGE ...]

  --local            also allow 127.0.0.1, for tests on this Mac without the tunnel
  --keep-token       keep the token from the current config, so the claude.ai URL stays the same
  --allow-ip RANGE   also allow this IP or range (repeat for more), for example your own
                     public IP while testing through the tunnel. Remove it after the test.
  --fake-protocol V  make the fake server always answer with protocol version V
  --stateless        run the gateway without sessions (no Mcp-Session-Id), an experiment

Writes ~/Library/Application Support/mcp-guard/config.toml (or $MCP_GUARD_CONFIG).
If the file exists, a timestamped backup is written first.
Prints the URL path to use in claude.ai. The token in it is a password: keep it secret.
"""
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANTHROPIC_OUTBOUND = ["160.79.104.0/21", "153.61.208.0/20"]


def config_path():
    env = os.environ.get("MCP_GUARD_CONFIG")
    if env:
        return Path(env)
    return Path.home() / "Library" / "Application Support" / "mcp-guard" / "config.toml"


def toml_string(value):
    return json.dumps(value)  # TOML basic strings accept JSON string escapes


def build(token, local, extra_ips=(), fake_protocol=None, stateless=False):
    python = ROOT / ".venv" / "bin" / "python"
    fake = ROOT / "tests" / "fakes" / "fake_mcp_server.py"
    ips = ANTHROPIC_OUTBOUND + (["127.0.0.1/32"] if local else []) + list(extra_ips)
    fake_args = [str(fake)] + (["--protocol-version", fake_protocol] if fake_protocol else [])
    lines = [
        "# Written by scripts/dev_gateway_config.py on %s" % time.strftime("%Y-%m-%d %H:%M"),
        "timeout_seconds = 45",
        "",
        "[gateway]",
        "port = 8787",
        "token = %s" % toml_string(token),
        "allowed_ips = [%s]" % ", ".join(toml_string(ip) for ip in ips),
        "stateless = %s" % ("true" if stateless else "false"),
        "",
        "[gateway.servers.fake]",
        "command = %s" % toml_string(str(python)),
        "args = [%s]" % ", ".join(toml_string(a) for a in fake_args),
        "",
    ]
    return "\n".join(lines)


def existing_token(path):
    import tomllib

    try:
        token = tomllib.loads(path.read_text(encoding="utf-8")).get("gateway", {}).get("token")
    except (OSError, ValueError):
        return None
    return token if isinstance(token, str) else None


def parse_args(args):
    local, keep, ips, protocol, stateless = False, False, [], None, False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--local":
            local = True
        elif a == "--stateless":
            stateless = True
        elif a == "--keep-token":
            keep = True
        elif a == "--allow-ip" and i + 1 < len(args):
            ips.append(args[i + 1])
            i += 1
        elif a == "--fake-protocol" and i + 1 < len(args):
            protocol = args[i + 1]
            i += 1
        else:
            return None
        i += 1
    return local, keep, ips, protocol, stateless


def main(argv):
    parsed = parse_args(argv[1:])
    if parsed is None:
        print(__doc__)
        return 2
    local, keep, extra_ips, fake_protocol, stateless = parsed

    token = existing_token(config_path()) if keep else None
    if keep and not token:
        print("No token in the current config, so a new one is made.")
    token = token or secrets.token_urlsafe(32)
    text = build(token, local, extra_ips, fake_protocol, stateless)

    try:  # check the file with mcp-guard's own parser when it is installed
        import tomllib

        sys.path.insert(0, str(ROOT / "src"))
        from mcp_guard.config import parse_config

        parse_config(tomllib.loads(text))
    except ImportError:
        pass

    path = config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name("config.backup-%s.toml" % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
        print("Backup:  %s" % backup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)  # it holds the token
    tmp.replace(path)

    print("Config:  %s" % path)
    if local:
        print("Note:    127.0.0.1 is allowed, so programs on this Mac can call the gateway.")
    if extra_ips:
        print("Note:    also allowed: %s. Run this script again without --allow-ip after the test." % ", ".join(extra_ips))
    print("Token:   %s" % ("kept" if keep and existing_token(path) == token else "new"))
    print("")
    print("Path for claude.ai (append it to the tunnel's https URL):")
    print("  /%s/fake/mcp" % token)
    print("")
    print("Local URL (for curl tests with --local):")
    print("  http://127.0.0.1:8787/%s/fake/mcp" % token)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
