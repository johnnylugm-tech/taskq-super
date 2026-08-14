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


# [FR-09] — uvicorn bind defaults; ``taskq_api.app:app`` keeps the
# development server identical to the production ASGI surface (NFR-10).
_DEFAULT_HOST: str = "0.0.0.0"
_DEFAULT_PORT: int = 8000
_DEFAULT_APP_TARGET: str = "taskq_api.app:app"


def main(argv: list[str] | None = None) -> None:
    """Dispatch subcommands; default is to start uvicorn.

    Citations:
    - taskq_api.__main__:main  per FR-09 (runner entry-point)
    / FR-03 AC-3.2 (`key create` subcommand)
    """
    # [FR-09] [FR-03]
    args = _build_parser().parse_args(argv)
    if args.command == "key" and args.key_command == "create":
        _run_key_create(args.scope)
        return
    _run_uvicorn()


def _build_parser() -> argparse.ArgumentParser:
    """Construct the `taskq_api` argparse tree.

    Kept separate from ``main`` so the parser can be inspected by tests
    without triggering dispatch (e.g. ``--help`` parsing).
    """
    # [FR-03] AC-3.2 — `python -m taskq_api key create --scope <scope>`.
    parser = argparse.ArgumentParser(prog="taskq_api")
    subparsers = parser.add_subparsers(dest="command")

    key_parser = subparsers.add_parser("key", help="manage API keys")
    key_sub = key_parser.add_subparsers(dest="key_command")
    key_create = key_sub.add_parser("create", help="mint a new API key")
    key_create.add_argument(
        "--scope",
        required=True,
        help="scope to embed in the new key (e.g. read, write, admin)",
    )
    return parser


def _run_key_create(scope: str) -> None:
    """Print exactly one plaintext line on stdout, then return.

    [FR-03] — AC-3.2 contract: the CLI exits with the key as the sole
    non-empty line so a caller can pipe it into another process.
    """
    plaintext = create_key(scope)
    sys.stdout.write(plaintext + "\n")
    sys.stdout.flush()


def _run_uvicorn() -> None:
    """Start uvicorn against the production app factory.

    [FR-09] — keeping the entry-point identical to production means the
    dev server hits exactly the same ASGI surface (NFR-10).
    """
    uvicorn.run(
        _DEFAULT_APP_TARGET,
        host=_DEFAULT_HOST,
        port=_DEFAULT_PORT,
    )


if __name__ == "__main__":
    main()
