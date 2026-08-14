"""`python -m taskq_api` entry point.

[FR-09] — delegating to uvicorn keeps the ASGI surface identical to
production (NFR-10).
[FR-03] — AC-3.2 exposes a `key create --scope <scope>` subcommand that
prints exactly one plaintext line on stdout, then exits.
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from taskq_api.service.auth import create_key


def main(argv: list[str] | None = None) -> None:
    """Dispatch subcommands; default is to start uvicorn.

    Citations:
    - taskq_api.__main__:main  per FR-09 (runner entry-point)
    / FR-03 AC-3.2 (`key create` subcommand)
    """
    # [FR-09] [FR-03]
    parser = argparse.ArgumentParser(prog="taskq_api")
    subparsers = parser.add_subparsers(dest="command")

    # `python -m taskq_api key create --scope write`
    key_parser = subparsers.add_parser("key", help="manage API keys")
    key_sub = key_parser.add_subparsers(dest="key_command")
    key_create = key_sub.add_parser("create", help="mint a new API key")
    key_create.add_argument(
        "--scope",
        required=True,
        help="scope to embed in the new key (e.g. read, write, admin)",
    )

    args = parser.parse_args(argv)

    if args.command == "key" and args.key_command == "create":
        # [FR-03] AC-3.2 — print exactly one plaintext line, then exit.
        plaintext = create_key(args.scope)
        sys.stdout.write(plaintext + "\n")
        sys.stdout.flush()
        return

    # Default: start uvicorn against the app factory.
    # [FR-09]
    uvicorn.run("taskq_api.app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
