"""Command-line entry point: `agentforge api | worker | version`."""

import argparse
import sys
from collections.abc import Sequence

from agentforge import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentforge")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("api", help="Run the HTTP API server")
    commands.add_parser("worker", help="Run the background worker")
    commands.add_parser("version", help="Print the version")
    args = parser.parse_args(argv)

    if args.command == "version":
        print(__version__)
    elif args.command == "api":
        import uvicorn

        from agentforge.core.settings import get_settings

        settings = get_settings()
        uvicorn.run(
            "agentforge.api.app:create_app",
            factory=True,
            host=settings.api_host,
            port=settings.api_port,
            log_config=None,  # logging is configured by create_app
            access_log=False,
            proxy_headers=False,
        )
    elif args.command == "worker":
        from agentforge.worker.main import run_worker

        run_worker()
    return 0


if __name__ == "__main__":
    sys.exit(main())
