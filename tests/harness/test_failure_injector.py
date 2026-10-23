
import os

import psycopg
import pytest

from tests.harness.failure_injector import (
    FailureInjector,
    InjectedFailure,
)


def test_failure_injector_does_not_fail_when_unconfigured():
    injector = FailureInjector()

    injector.hit("before_commit")


def test_failure_injector_raises_at_configured_point():
    injector = FailureInjector(fail_at="before_commit")

    with pytest.raises(InjectedFailure, match="before_commit"):
        injector.hit("before_commit")


def test_failure_injector_ignores_other_points():
    injector = FailureInjector(fail_at="before_commit")

    injector.hit("after_validation")


def test_injected_failure_rolls_back_database_changes():
    injector = FailureInjector(fail_at="before_commit")

    with psycopg.connect(
        host="localhost",
        port=int(os.getenv("MAPMS_DB_PORT", "5432")),
        dbname="mapms",
        user="mapms",
        password="mapms_dev_password",
        connect_timeout=3,
    ) as connection:
        connection.execute(
            """
            CREATE TEMP TABLE rollback_probe (
                id INTEGER PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.commit()

        with pytest.raises(InjectedFailure):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO rollback_probe (id, value) VALUES (%s, %s)",
                    (1, "must be rolled back"),
                )

                injector.hit("before_commit")

        row = connection.execute(
            "SELECT COUNT(*) FROM rollback_probe"
        ).fetchone()

        assert row[0] == 0
