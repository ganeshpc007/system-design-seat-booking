import os

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://booking:booking@localhost:5434/seat_booking")


def make_pool(size: int = 20) -> AsyncConnectionPool:
    # A fixed number of open connections, like a real server's pool.
    return AsyncConnectionPool(DATABASE_URL, min_size=size, max_size=size,
                               kwargs={"row_factory": dict_row}, open=False)
