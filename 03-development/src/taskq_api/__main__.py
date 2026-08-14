"""`python -m taskq_api` entry point.

[FR-09] — delegating to uvicorn keeps the ASGI surface identical to
production (NFR-10).
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Start uvicorn against the app factory.

    Citations:
    - taskq_api.__main__:main  per FR-09 (runner entry-point)
    """
    # [FR-09]
    uvicorn.run("taskq_api.app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
