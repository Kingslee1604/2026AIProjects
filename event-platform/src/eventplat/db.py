"""Typed wrapper around pg8000, which ships without type information.

Keeping every pg8000 call here lets the rest of the package stay strictly typed.
"""

from typing import Any, Self, cast

from pg8000.native import Connection  # pyright: ignore[reportMissingTypeStubs]

from eventplat.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        pg = settings.postgres
        self._con = Connection(
            user=pg.user, password=pg.password, host=pg.host, port=pg.port, database=pg.database
        )

    def execute(self, sql: str, **params: object) -> None:
        self._con.run(sql, **params)  # pyright: ignore[reportUnknownMemberType]

    def query(self, sql: str, **params: object) -> list[list[Any]]:
        rows = self._con.run(sql, **params)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return cast(list[list[Any]], rows or [])

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
