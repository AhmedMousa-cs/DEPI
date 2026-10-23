
import os
from uuid import uuid4

import psycopg

from tests.harness.concurrency import run_barrier_race


def test_barrier_race_runs_both_workers():
    completed = []

    def operation(index):
        completed.append(index)
        return index

    results = run_barrier_race(operation, participants=2)

    assert sorted(results) == [0, 1]
    assert sorted(completed) == [0, 1]


def test_concurrent_updates_do_not_apply_same_change_twice():
    port = int(os.getenv("MAPMS_DB_PORT", "5432"))
    table = f"mapms_race_probe_{uuid4().hex}"

    connection_args = {
        "host": "localhost",
        "port": port,
        "dbname": "mapms",
        "user": "mapms",
        "password": "mapms_dev_password",
        "connect_timeout": 5,
    }

    with psycopg.connect(**connection_args) as setup:
        setup.execute(
            f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)"
        )
        setup.execute(
            f"INSERT INTO {table} (id, value) VALUES (1, 0)"
        )

    try:
        def attempt_update(_worker_index):
            with psycopg.connect(**connection_args) as connection:
                with connection.transaction():
                    row = connection.execute(
                        f"""
                        UPDATE {table}
                        SET value = value + 1
                        WHERE id = 1 AND value = 0
                        RETURNING value
                        """
                    ).fetchone()

                    return row is not None

        results = run_barrier_race(
            attempt_update,
            participants=2,
        )

        assert sum(results) == 1

        with psycopg.connect(**connection_args) as verify:
            row = verify.execute(
                f"SELECT value FROM {table} WHERE id = 1"
            ).fetchone()

        assert row == (1,)

    finally:
        with psycopg.connect(**connection_args) as cleanup:
            cleanup.execute(f"DROP TABLE IF EXISTS {table}")
