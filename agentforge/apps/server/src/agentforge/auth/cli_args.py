"""Argument definitions for `agentforge user ...`.

Kept free of heavy imports: the top-level CLI builds every sub-parser on each start, including
the container health check that runs every few seconds. agentforge.auth.cli (database, argon2)
is imported only when a `user` command actually runs.
"""

import argparse

ROLES = ("admin", "member")  # mirrors agentforge.auth.rbac.Role (checked by a unit test)


def add_user_commands(user: argparse.ArgumentParser) -> None:
    """Fill in the `user` sub-command parser created by agentforge.cli."""
    actions = user.add_subparsers(dest="user_command", required=True)

    create = actions.add_parser("create", help="Create a user in the default organization")
    create.add_argument("--email", required=True)
    create.add_argument("--role", choices=ROLES, default="member")
    create.add_argument("--can-review", action="store_true", help="Allow deciding reviews")
    create.add_argument("--password-stdin", action="store_true", help="Read password from stdin")

    for name, text in (("disable", "Disable a user"), ("enable", "Re-enable a user")):
        action = actions.add_parser(name, help=f"{text} (disabling signs them out everywhere)")
        action.add_argument("--email", required=True)

    reset = actions.add_parser("reset-password", help="Set a new password; signs out everywhere")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password-stdin", action="store_true")

    actions.add_parser("list", help="List users in the default organization")
