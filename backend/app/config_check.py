"""Validate configuration and print the effective settings, with secrets redacted.

    python3 -m app.config_check            # human readable
    python3 -m app.config_check --json     # machine readable

Exit codes:
    0  valid (warnings may still be printed)
    1  invalid — the application would refuse to start
    2  the check itself could not run

This exists so a misconfiguration is discovered by an operator running one
command, rather than as a confusing 500 on a request hours later.
"""
from __future__ import annotations

import argparse
import json
import sys

from .settings import ConfigError, FIELDS, load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m app.config_check")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--ha-options", default="/data/options.json",
                        help="path to the Home Assistant add-on options file")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(ha_options_path=args.ha_options)
    except ConfigError as exc:
        if args.json:
            print(json.dumps({"valid": False, "errors": exc.errors}, indent=2))
        else:
            print("CONFIGURATION INVALID — the application would refuse to start.\n")
            for err in exc.errors:
                print(f"  ERROR  {err}")
            print("\nFix the above and re-run this command.")
        return 1
    except Exception as exc:  # noqa: BLE001 - a checker that crashes is useless
        print(f"config_check failed to run: {exc}", file=sys.stderr)
        return 2

    payload = {
        "valid": True,
        "warnings": list(settings.warnings),
        "settings": settings.redacted(),
        "sources": settings.sources,
        "derived": {
            "data_dir": settings.data_dir,
            "images_dir": settings.images_dir,
            "database": _describe_db(settings),
            "mcp_api": settings.mcp_api,
            "ai_enabled": settings.ai_enabled,
        },
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print("CONFIGURATION VALID\n")
    print(f"  data dir : {settings.data_dir}")
    print(f"  database : {_describe_db(settings)}")
    print(f"  auth     : {'DISABLED (single local user)' if settings.DISABLE_AUTH else 'enabled (JWT)'}")
    print(f"  AI       : {settings.AI_PROVIDER or 'disabled'}")
    print(f"  MCP      : {'on port %d' % settings.MCP_PORT if settings.MCP_ENABLED else 'disabled'}")
    print()

    non_default = [f for f in FIELDS if settings.sources.get(f.name) != "default"]
    if non_default:
        print("  Explicitly set (source):")
        red = settings.redacted()
        for f in non_default:
            print(f"    {f.env_var:34} = {red[f.name]!r:<28} [{settings.sources[f.name]}]")
        print()

    if settings.warnings:
        for warn in settings.warnings:
            print(f"  WARNING  {warn}\n")
    else:
        print("  No warnings.\n")
    return 0


def _describe_db(settings) -> str:
    """Never print a database URL verbatim — it may embed credentials."""
    uri = settings.sqlalchemy_uri
    if uri.startswith("sqlite"):
        return f"sqlite ({uri.replace('sqlite:///', '')})"
    scheme = uri.split("://", 1)[0]
    return f"{scheme} (connection details hidden)"


if __name__ == "__main__":
    raise SystemExit(main())
