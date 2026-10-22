"""`agentforge user create|disable|enable|reset-password|list` (Release 1 has no sign-up)."""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.auth.passwords import WeakPasswordError, validate_new_password
from agentforge.auth.rbac import Role
from agentforge.auth.users import (
    UserExistsError,
    UserNotFoundError,
    create_user,
    list_users,
    reset_password,
    set_disabled,
)
from agentforge.core.settings import get_settings
from agentforge.db.engine import create_app_engine, create_sessionmaker
from agentforge.db.migrate import DEFAULT_ORG_ID


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        raise WeakPasswordError("Passwords do not match")
    return first


async def _with_sessions(
    work: Callable[[async_sessionmaker[AsyncSession]], Awaitable[None]],
) -> None:
    settings = get_settings()
    if settings.database_url is None:
        raise SystemExit("AGENTFORGE_DATABASE_URL is not set")
    engine = create_app_engine(settings.database_url, pool_size=1)
    try:
        await work(create_sessionmaker(engine))
    finally:
        await engine.dispose()


def run_user_command(args: argparse.Namespace) -> int:
    password = ""
    try:
        if args.user_command in ("create", "reset-password"):
            password = _read_password(args.password_stdin)
            validate_new_password(password)

        async def work(sessions: async_sessionmaker[AsyncSession]) -> None:
            if args.user_command == "create":
                await create_user(
                    sessions,
                    org_id=DEFAULT_ORG_ID,
                    email=args.email,
                    password=password,
                    role=Role(args.role),
                    can_review=args.can_review or args.role == Role.ADMIN.value,
                )
                print(f"Created {args.role} {args.email}")
            elif args.user_command in ("disable", "enable"):
                await set_disabled(sessions, args.email, disabled=args.user_command == "disable")
                print(f"{args.user_command.capitalize()}d {args.email}")
            elif args.user_command == "reset-password":
                await reset_password(sessions, args.email, password)
                print(f"Password reset for {args.email}; all their sessions were signed out")
            else:
                for u in await list_users(sessions, DEFAULT_ORG_ID):
                    flags = " (reviewer)" if u.can_review else ""
                    state = " [disabled]" if u.disabled else ""
                    print(f"{u.email}  {u.role}{flags}{state}")

        asyncio.run(_with_sessions(work))
    except WeakPasswordError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except UserExistsError as error:
        print(f"Error: a user with email {error} already exists", file=sys.stderr)
        return 1
    except UserNotFoundError as error:
        print(f"Error: no user with email {error}", file=sys.stderr)
        return 1
    return 0
