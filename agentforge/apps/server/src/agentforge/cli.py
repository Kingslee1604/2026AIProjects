"""Command-line entry point: `agentforge api | worker | healthcheck | version`."""

import argparse
import sys
from collections.abc import Sequence

from agentforge import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentforge")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("api", help="Run the HTTP API server")
    commands.add_parser("worker", help="Run the background worker")
    health = commands.add_parser("healthcheck", help="Exit 0 if a local process is healthy")
    health.add_argument("target", choices=["api", "worker"])
    migrate = commands.add_parser(
        "migrate", help="Upgrade the database schema and seed defaults (runs as schema owner)"
    )
    migrate.add_argument("--revision", default="head", help="Target revision (default: head)")
    from agentforge.auth.cli_args import add_user_commands

    add_user_commands(commands.add_parser("user", help="Manage user accounts (no public sign-up)"))
    commands.add_parser("version", help="Print the version")
    args = parser.parse_args(argv)

    if args.command == "version":
        print(__version__)
    elif args.command == "user":
        from agentforge.auth.cli import run_user_command

        return run_user_command(args)
    elif args.command == "migrate":
        from agentforge.core.logging import configure_logging, get_logger
        from agentforge.core.settings import get_settings
        from agentforge.db.migrate import seed_defaults, upgrade

        settings = get_settings()
        configure_logging(settings.log_level, settings.log_format)
        if settings.migration_database_url is None:
            print("AGENTFORGE_MIGRATION_DATABASE_URL is not set", file=sys.stderr)
            return 2
        upgrade(settings.migration_database_url, args.revision)
        seed_defaults(settings.migration_database_url)
        get_logger("agentforge.migrate").info("migrations_applied", revision=args.revision)
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
    elif args.command == "healthcheck":
        from agentforge.core.health import api_is_healthy, worker_is_healthy
        from agentforge.core.settings import get_settings

        settings = get_settings()
        healthy = api_is_healthy(settings) if args.target == "api" else worker_is_healthy(settings)
        return 0 if healthy else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
