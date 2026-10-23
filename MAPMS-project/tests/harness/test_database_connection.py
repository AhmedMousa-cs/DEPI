import os
import psycopg


def test_postgres_connection_and_extensions():
    with psycopg.connect(
        host="localhost",
        port=int(os.getenv("MAPMS_DB_PORT", "5432")),
        dbname="mapms",
        user="mapms",
        password="mapms_dev_password",
        connect_timeout=3,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_database(),
                       EXISTS (
                           SELECT 1 FROM pg_extension
                           WHERE extname = 'btree_gist'
                       ),
                       EXISTS (
                           SELECT 1 FROM pg_extension
                           WHERE extname = 'pg_trgm'
                       )
                """
            )

            database, has_btree_gist, has_pg_trgm = cursor.fetchone()

    assert database == "mapms"
    assert has_btree_gist
    assert has_pg_trgm
