from __future__ import annotations

import sys

from .server import build_server


def main() -> None:
    server = build_server()
    transport = "stdio"
    if "--http" in sys.argv:
        transport = "http"
    server.run(transport=transport)


if __name__ == "__main__":
    main()
