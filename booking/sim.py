"""Experiments: one per idea in the article. Each prints a Markdown table and checks the
result; the run exits with code 1 if a check fails, so the experiments are also the tests.

    uv run sim                  # all experiments
    uv run sim race             # or one: race, seats, transaction, expiry
"""

import asyncio
import random
import sys
import time
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from booking import seats
from booking.db import DATABASE_URL, make_pool

failures: list[str] = []


def check(ok: bool, message: str) -> None:
    print(f"{'✓' if ok else '✗'} {message}")
    if not ok:
        failures.append(message)


def table(header: list[str], rows: list[list]) -> None:
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(str(cell) for cell in row) + " |")
    print()


async def new_show(pool, show_id: int, seat_ids: list[str]) -> None:
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM reservations WHERE show_id = %s", (show_id,))
        await conn.execute("DELETE FROM show_seats WHERE show_id = %s", (show_id,))
        for seat_id in seat_ids:
            await conn.execute("INSERT INTO show_seats (show_id, seat_id, status) VALUES (%s, %s, 'AVAILABLE')",
                               (show_id, seat_id))


async def try_hold(pool, hold, show_id: int, seat_id: str, customer_id: str):
    async with pool.connection() as conn:
        return await hold(conn, show_id, seat_id, customer_id)


async def count(pool, sql: str, *params) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        return (await cur.fetchone())["count"]


MODES = [("naive: read, check, then write", seats.hold_seat_naive),
         ("conditional UPDATE", seats.hold_seat)]


async def race(pool, customers: int = 1000) -> None:
    print(f"## race: {customers} customers click A12 at the same moment\n")
    rows = []
    for number, (mode, hold) in enumerate(MODES):
        show_id = 100 + number
        await new_show(pool, show_id, ["A12"])
        started = time.perf_counter()
        results = await asyncio.gather(*(try_hold(pool, hold, show_id, "A12", f"customer-{i}")
                                         for i in range(customers)))
        ms = (time.perf_counter() - started) * 1000
        told = sum(r is not None for r in results)
        saved = await count(pool, "SELECT count(*) FROM reservations WHERE show_id = %s", show_id)
        rows.append([mode, customers, told, saved, f"{ms:.0f} ms"])
        if hold is seats.hold_seat:
            check(told == 1 and saved == 1, f"conditional UPDATE: exactly 1 customer holds A12 (got {told})")
    table(["mode", "customers", 'told "A12 is yours"', "reservations saved", "time"], rows)


async def full_show(pool, customers: int = 2000) -> None:
    seat_ids = [f"{row}{n}" for row in "ABCDEFGHIJ" for n in range(1, 11)]
    print(f"## seats: {customers} customers, {len(seat_ids)} seats, each customer clicks a random seat\n")
    rows = []
    for number, (mode, hold) in enumerate(MODES):
        show_id = 200 + number
        await new_show(pool, show_id, seat_ids)
        random.seed(number)
        picks = [random.choice(seat_ids) for _ in range(customers)]
        results = await asyncio.gather(*(try_hold(pool, hold, show_id, seat, f"customer-{i}")
                                         for i, seat in enumerate(picks)))
        told = sum(r is not None for r in results)
        double = await count(pool, """
            SELECT count(*) FROM (
                SELECT seat_id FROM reservations WHERE show_id = %s GROUP BY seat_id HAVING count(*) > 1
            ) AS double_booked""", show_id)
        rows.append([mode, len(seat_ids), told, double])
        if hold is seats.hold_seat:
            check(told == len(seat_ids) and double == 0,
                  f"conditional UPDATE: {len(seat_ids)} seats, {told} holds, {double} seats given twice")
    table(["mode", "seats", "customers told they got a seat", "seats given to 2+ customers"], rows)


async def transaction(pool, attempts: int = 20) -> None:
    print(f"## transaction: the reservation insert fails, {attempts} times\n")
    print("The failure is simulated with a missing customer id (the column is NOT NULL).\n")
    seat_ids = [f"A{n}" for n in range(1, attempts + 1)]
    orphans_sql = """
        SELECT count(*) FROM show_seats s
        WHERE s.show_id = %s AND s.status = 'HELD'
          AND NOT EXISTS (SELECT 1 FROM reservations r WHERE r.id = s.reservation_id)"""
    rows = []

    show_id = 300
    await new_show(pool, show_id, seat_ids)
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True, row_factory=dict_row) as conn:
        for seat_id in seat_ids:
            try:
                await seats.hold_seat_two_commits(conn, show_id, seat_id, None)
            except psycopg.errors.NotNullViolation:
                pass
    rows.append(["claim and insert, two separate commits", attempts,
                 await count(pool, orphans_sql, show_id)])

    show_id = 301
    await new_show(pool, show_id, seat_ids)
    for seat_id in seat_ids:
        try:
            await try_hold(pool, seats.hold_seat, show_id, seat_id, None)
        except psycopg.errors.NotNullViolation:
            pass
    orphans = await count(pool, orphans_sql, show_id)
    available = await count(pool, "SELECT count(*) FROM show_seats WHERE show_id = %s AND status = 'AVAILABLE'",
                            show_id)
    rows.append(["one transaction", attempts, orphans])
    check(orphans == 0 and available == attempts,
          f"one transaction: {orphans} seats stuck HELD, {available}/{attempts} still available")
    table(["mode", "failed inserts", "seats stuck HELD with no reservation"], rows)


async def expiry(pool, hold_seconds: float = 1.0) -> None:
    print(f"## expiry: a {hold_seconds:g} s hold (5 minutes in the article), no cleanup job running\n")
    show_id = 400
    await new_show(pool, show_id, ["A12"])
    rows = []

    async def step(what: str, result: str, expected: str) -> None:
        rows.append([len(rows) + 1, what, result, expected])
        check(result == expected, f"{what}: {result}")

    async with pool.connection() as conn:
        a = await seats.hold_seat(conn, show_id, "A12", "customer-A", hold_seconds)
        await step("A holds A12", "held" if a else "rejected", "held")
        b = await seats.hold_seat(conn, show_id, "A12", "customer-B", hold_seconds)
        await step("B tries A12 straight away", "held" if b else "rejected", "rejected")

        await asyncio.sleep(hold_seconds + 0.2)
        status = next(s["status"] for s in await seats.seat_map(conn, show_id) if s["seat_id"] == "A12")
        await step(f"Seat map after {hold_seconds + 0.2:g} s", status, "AVAILABLE")
        b = await seats.hold_seat(conn, show_id, "A12", "customer-B")
        await step("B tries A12 again", "held" if b else "rejected", "held")

        ok = await seats.confirm_seat(conn, show_id, "A12", a["id"])
        await step("A confirms with its old reservation", "confirmed" if ok else "rejected", "rejected")
        ok = await seats.confirm_seat(conn, show_id, "A12", uuid4())
        await step("Someone confirms A12 with another reservation id", "confirmed" if ok else "rejected", "rejected")
        ok = await seats.confirm_seat(conn, show_id, "A12", b["id"])
        await step("B confirms", "confirmed" if ok else "rejected", "confirmed")
    print()
    table(["step", "what happens", "result", "expected"], rows)


EXPERIMENTS = {"race": race, "seats": full_show, "transaction": transaction, "expiry": expiry}


async def run(names: list[str]) -> None:
    pool = make_pool(size=50)
    await pool.open(wait=True)
    try:
        for name in names:
            await EXPERIMENTS[name](pool)
    finally:
        await pool.close()


def main() -> None:
    names = sys.argv[1:] or list(EXPERIMENTS)
    unknown = [n for n in names if n not in EXPERIMENTS]
    if unknown:
        sys.exit(f"Unknown experiment: {', '.join(unknown)}. Choose from: {', '.join(EXPERIMENTS)}")
    asyncio.run(run(names))
    if failures:
        sys.exit(f"✗ {len(failures)} check(s) failed")
    print("✓ All checks passed")
