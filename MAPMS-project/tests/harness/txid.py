from psycopg import Connection


def get_current_txid(conn: Connection) -> int:
    """Return the ID of the current PostgreSQL transaction."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT txid_current()")
        result = cursor.fetchone()

    if result is None:
        raise RuntimeError("PostgreSQL did not return a transaction ID")

    return int(result[0])
