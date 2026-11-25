"""The booking API: see the seat map, hold a seat, confirm it.

    uv run api        # http://127.0.0.1:8000/docs
"""

from contextlib import asynccontextmanager
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from booking import seats
from booking.db import make_pool

pool = make_pool()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await pool.open(wait=True)
    yield
    await pool.close()


app = FastAPI(title="Seat booking", lifespan=lifespan)


class HoldRequest(BaseModel):
    customer_id: str


class ConfirmRequest(BaseModel):
    reservation_id: UUID


@app.get("/shows/{show_id}/seats")
async def get_seats(show_id: int):
    async with pool.connection() as conn:
        return await seats.seat_map(conn, show_id)


@app.post("/shows/{show_id}/seats/{seat_id}/hold", status_code=201)
async def hold(show_id: int, seat_id: str, body: HoldRequest):
    async with pool.connection() as conn:
        held = await seats.hold_seat(conn, show_id, seat_id, body.customer_id)
    if held is None:
        raise HTTPException(409, "This seat is not available. Choose another seat.")
    return {"reservation_id": held["id"], "expires_at": held["expires_at"]}


@app.post("/shows/{show_id}/seats/{seat_id}/confirm")
async def confirm(show_id: int, seat_id: str, body: ConfirmRequest):
    async with pool.connection() as conn:
        confirmed = await seats.confirm_seat(conn, show_id, seat_id, body.reservation_id)
    if not confirmed:
        raise HTTPException(409, "The hold has expired or belongs to another reservation.")
    return {"seat_id": seat_id, "status": "CONFIRMED"}


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8000)
