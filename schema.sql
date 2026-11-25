-- The two tables from the article. Loaded automatically on the first `docker compose up`.

CREATE TABLE show_seats (
    show_id BIGINT NOT NULL,
    seat_id TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('AVAILABLE', 'HELD', 'CONFIRMED')),
    reservation_id UUID,
    hold_expires_at TIMESTAMPTZ,
    PRIMARY KEY (show_id, seat_id)
);

CREATE TABLE reservations (
    id UUID PRIMARY KEY,
    show_id BIGINT NOT NULL,
    seat_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('HELD', 'CONFIRMED')),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- A demo show for trying the API by hand: show 1, seats A1-A10 and B1-B10.
INSERT INTO show_seats (show_id, seat_id, status)
SELECT 1, row_letter || n, 'AVAILABLE'
FROM unnest(ARRAY['A', 'B']) AS row_letter, generate_series(1, 10) AS n;
