# Seat Booking: Stop Double Bookings

Runnable code and experiments for the article **["How Do You Stop Two People from Booking the Same Seat?"](https://ganeshdecodes.com/blogs/stop-two-people-booking-the-same-seat/)** (Ganesh Decodes).

Two customers see seat A12 as available and click it at the same moment. Only one of them may get it. This repo turns each idea in the article into a little code plus an experiment you can run on your laptop to see the numbers yourself.

- Python + FastAPI + PostgreSQL, raw SQL, no ORM.
- The SQL is the article's SQL: [`booking/seats.py`](booking/seats.py).
- Each experiment prints a table and **exits 1 if a correctness check fails**, so the experiments are also the tests.

## Run it

You need Docker and [uv](https://docs.astral.sh/uv/).

```bash
docker compose up -d --wait    # PostgreSQL on localhost:5434, tables from schema.sql
uv run sim                     # all experiments (about 3 s)
uv run sim race                # or one: race, seats, transaction, expiry
```

Try the API by hand:

```bash
uv run api                     # http://127.0.0.1:8000/docs

curl localhost:8000/shows/1/seats
curl -X POST localhost:8000/shows/1/seats/A5/hold \
     -H 'content-type: application/json' -d '{"customer_id": "customer-A"}'
curl -X POST localhost:8000/shows/1/seats/A5/confirm \
     -H 'content-type: application/json' -d '{"reservation_id": "<id from the hold>"}'
```

| Endpoint | Does | Article |
|---|---|---|
| `GET /shows/{show_id}/seats` | The seat map. An expired hold shows as `AVAILABLE` | §1, §6 |
| `POST /shows/{show_id}/seats/{seat_id}/hold` | Claims the seat and creates its reservation in one transaction. `201`, or `409` if taken | §4, §5 |
| `POST /shows/{show_id}/seats/{seat_id}/confirm` | Confirms only the reservation that still owns an unexpired hold. `200`, or `409` | §7 |

## The experiments

Numbers from one run on a MacBook (Apple silicon), PostgreSQL 16 in Docker, 50 database connections.

### `race`: 1,000 customers click A12 at the same moment (§2, §4)

| mode | customers | told "A12 is yours" | reservations saved | time |
|---|---|---|---|---|
| naive: read, check, then write | 1000 | 50 | 50 | 130 ms |
| conditional UPDATE | 1000 | **1** | 1 | 110 ms |

The naive version reads the status, checks it in Python, then writes, all inside a transaction. Every request that read A12 before the first write committed saw `AVAILABLE`, so 50 customers (one per open connection) were told they got the same seat. A transaction alone doesn't fix this.

The conditional `UPDATE` puts the check inside the write (`WHERE status = 'AVAILABLE' OR the hold has expired`). PostgreSQL's row lock makes the other 999 wait and re-check the row, and none of them match. Exactly 1 winner, every run, and no slower.

### `seats`: 2,000 customers, 100 seats, each clicks a random seat (§4)

| mode | seats | customers told they got a seat | seats given to 2+ customers |
|---|---|---|---|
| naive: read, check, then write | 100 | 127 | 24 |
| conditional UPDATE | 100 | **100** | **0** |

### `transaction`: the reservation insert fails, 20 times (§5)

| mode | failed inserts | seats stuck HELD with no reservation |
|---|---|---|
| claim and insert, two separate commits | 20 | 20 |
| one transaction | 20 | **0** |

The failure is simulated with a missing customer id (the column is `NOT NULL`). Without a transaction, the seat change is already committed when the insert fails, so the seat is held by nobody. With one transaction, the failed insert rolls the seat back to `AVAILABLE`.

### `expiry`: holds expire without a cleanup job (§6, §7)

The hold is 1 second here instead of the article's 5 minutes.

| step | what happens | result |
|---|---|---|
| 1 | A holds A12 | held |
| 2 | B tries A12 straight away | rejected |
| 3 | Seat map after 1.2 s | AVAILABLE |
| 4 | B tries A12 again | held |
| 5 | A confirms with its old reservation | rejected |
| 6 | Someone confirms A12 with another reservation id | rejected |
| 7 | B confirms | confirmed |

No background job ran. The claim itself treats an expired hold as available (step 4), and confirm checks the reservation id and the expiry (steps 5-7), so a late checkout can't take a seat that has moved on.

## The code

```
schema.sql          show_seats and reservations (the article's tables) + a demo show
booking/seats.py    the article's SQL: hold_seat, confirm_seat, seat_map
                    and two wrong-on-purpose versions for the experiments
booking/api.py      FastAPI: the three endpoints above
booking/sim.py      the experiments
booking/db.py       the connection pool
```

## Not included (on purpose)

To keep the code small, these parts of the article are described but not built:

- **Payments.** The article explains why a payment in progress needs its own state (such as `PAYMENT_PENDING`) before a hold can expire. The flash-sale repo covers payments and webhooks: [system-design-flash-sale](https://github.com/ganeshpc007/system-design-flash-sale).
- **A cleanup worker.** Optional housekeeping: the experiments show the system stays correct without one.
- **Marking the replaced reservation as expired.** When an expired hold is taken over, its old reservation row stays `HELD` in the history table, as in the article's example. The seat row is what decides who owns the seat.
