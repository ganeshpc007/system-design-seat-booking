"""The booking rules from the article, as SQL.

Each function takes one database connection. hold_seat and confirm_seat are the safe
versions the API uses. hold_seat_naive and hold_seat_two_commits are wrong on purpose:
the experiments run them side by side with the safe versions.
"""

from uuid import UUID, uuid4

from psycopg import AsyncConnection

HOLD_SECONDS = 300  # the article's 5-minute hold

# Section 4: the availability check is inside the UPDATE itself.
CLAIM_SEAT = """
    UPDATE show_seats
    SET status = 'HELD',
        reservation_id = %(reservation_id)s,
        hold_expires_at = clock_timestamp() + make_interval(secs => %(hold_seconds)s)
    WHERE show_id = %(show_id)s
      AND seat_id = %(seat_id)s
      AND (
          status = 'AVAILABLE'
          OR (
              status = 'HELD'
              AND hold_expires_at <= clock_timestamp()
          )
      )
    RETURNING show_id, seat_id, reservation_id, hold_expires_at
"""

INSERT_RESERVATION = """
    INSERT INTO reservations (id, show_id, seat_id, customer_id, status, expires_at)
    VALUES (%(reservation_id)s, %(show_id)s, %(seat_id)s, %(customer_id)s, 'HELD', %(expires_at)s)
"""

# Section 5: claim the seat and create its reservation in one statement. The INSERT
# only reads rows the claim returned, so a failed claim inserts nothing.
HOLD_SEAT = f"""
    WITH claimed_seat AS ({CLAIM_SEAT})
    INSERT INTO reservations (id, show_id, seat_id, customer_id, status, expires_at)
    SELECT reservation_id, show_id, seat_id, %(customer_id)s, 'HELD', hold_expires_at
    FROM claimed_seat
    RETURNING id, expires_at
"""

# Section 7: confirm only the reservation that still owns an unexpired hold.
CONFIRM_SEAT = """
    UPDATE show_seats
    SET status = 'CONFIRMED',
        hold_expires_at = NULL
    WHERE show_id = %(show_id)s
      AND seat_id = %(seat_id)s
      AND status = 'HELD'
      AND reservation_id = %(reservation_id)s
      AND hold_expires_at > clock_timestamp()
    RETURNING seat_id
"""


async def hold_seat(conn: AsyncConnection, show_id: int, seat_id: str, customer_id: str | None,
                    hold_seconds: float = HOLD_SECONDS) -> dict | None:
    """Hold a seat. Returns {id, expires_at}, or None if the seat isn't available."""

    params = {"show_id": show_id, "seat_id": seat_id, "customer_id": customer_id,
              "reservation_id": uuid4(), "hold_seconds": hold_seconds}
    async with conn.transaction():
        cur = await conn.execute(HOLD_SEAT, params)
        return await cur.fetchone()


async def confirm_seat(conn: AsyncConnection, show_id: int, seat_id: str, reservation_id: UUID) -> bool:
    """Confirm a held seat. False if the hold expired or belongs to another reservation."""

    params = {"show_id": show_id, "seat_id": seat_id, "reservation_id": reservation_id}
    async with conn.transaction():
        cur = await conn.execute(CONFIRM_SEAT, params)
        if await cur.fetchone() is None:
            return False
        await conn.execute("UPDATE reservations SET status = 'CONFIRMED' WHERE id = %s", (reservation_id,))
    return True


async def seat_map(conn: AsyncConnection, show_id: int) -> list[dict]:
    """What the customer sees. An expired hold shows as AVAILABLE: no cleanup job needed."""

    cur = await conn.execute(
        """
        SELECT seat_id,
               CASE WHEN status = 'HELD' AND hold_expires_at <= clock_timestamp()
                    THEN 'AVAILABLE' ELSE status END AS status
        FROM show_seats
        WHERE show_id = %s
        ORDER BY left(seat_id, 1), substring(seat_id FROM 2)::int  -- A1, A2 ... A10
        """,
        (show_id,),
    )
    return await cur.fetchall()


# --- Wrong on purpose, for the experiments ---------------------------------------------

async def hold_seat_naive(conn: AsyncConnection, show_id: int, seat_id: str, customer_id: str,
                          hold_seconds: float = HOLD_SECONDS) -> dict | None:
    """Section 2: read the status, check it in code, then write. Still inside a transaction."""

    params = {"show_id": show_id, "seat_id": seat_id, "customer_id": customer_id,
              "reservation_id": uuid4(), "hold_seconds": hold_seconds}
    async with conn.transaction():
        cur = await conn.execute(
            "SELECT status FROM show_seats WHERE show_id = %(show_id)s AND seat_id = %(seat_id)s", params)
        if (await cur.fetchone())["status"] != "AVAILABLE":
            return None
        cur = await conn.execute(
            """
            UPDATE show_seats
            SET status = 'HELD',
                reservation_id = %(reservation_id)s,
                hold_expires_at = clock_timestamp() + make_interval(secs => %(hold_seconds)s)
            WHERE show_id = %(show_id)s AND seat_id = %(seat_id)s
            RETURNING hold_expires_at
            """,
            params,
        )
        params["expires_at"] = (await cur.fetchone())["hold_expires_at"]
        await conn.execute(INSERT_RESERVATION, params)
    return {"id": params["reservation_id"], "expires_at": params["expires_at"]}


async def hold_seat_two_commits(conn: AsyncConnection, show_id: int, seat_id: str, customer_id: str | None,
                                hold_seconds: float = HOLD_SECONDS) -> dict | None:
    """Section 5: the safe claim, but the claim and the reservation are saved separately.

    `conn` must be in autocommit mode, so each statement commits on its own.
    """

    params = {"show_id": show_id, "seat_id": seat_id, "customer_id": customer_id,
              "reservation_id": uuid4(), "hold_seconds": hold_seconds}
    cur = await conn.execute(CLAIM_SEAT, params)
    claimed = await cur.fetchone()
    if claimed is None:
        return None
    params["expires_at"] = claimed["hold_expires_at"]
    await conn.execute(INSERT_RESERVATION, params)
    return {"id": params["reservation_id"], "expires_at": params["expires_at"]}
