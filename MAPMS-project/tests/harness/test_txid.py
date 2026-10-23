import os
import psycopg

from tests.harness.txid import get_current_txid


def test_txid_is_stable_inside_one_transaction():
    with psycopg.connect(
        host="localhost",
        port=int(os.getenv("MAPMS_DB_PORT", "5432")),
        dbname="mapms",
        user="mapms",
        password="mapms_dev_password",
        connect_timeout=3,
    ) as connection:
        with connection.transaction():
            first = get_current_txid(connection)
            second = get_current_txid(connection)

            assert first == second
            assert first > 0
