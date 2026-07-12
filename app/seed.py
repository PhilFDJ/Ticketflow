"""Populate the database with sample events so the app looks alive on first run.

Safe to run repeatedly: it clears existing sample data first.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import db


def reset():
    with db.cursor() as conn:
        for t in ("tickets", "order_items", "orders", "ticket_types", "events"):
            conn.execute(f"DELETE FROM {t}")


def days_from_now(d, hour=19):
    t = time.localtime(time.time() + d * 86400)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, 0, 0, 0, 0, -1)))


SAMPLES = [
    {
        "title": "Friday Night Live: The Wildcards",
        "venue": "The Brickyard, Manchester",
        "description": "An electric night of indie rock with support from local "
                       "favourites. Doors 7pm, first act 8pm. Over 18s only.",
        "image": "#4f46e5",
        "days": 9,
        "tickets": [("Early Bird", 1200, 50), ("General Admission", 1800, 200),
                    ("VIP (incl. drink)", 3500, 30)],
    },
    {
        "title": "Sunday Jazz Brunch",
        "venue": "Riverside Rooms, Bristol",
        "description": "Bottomless coffee, a three-piece jazz trio, and the best "
                       "eggs in the city. A relaxed late-morning session.",
        "image": "#0ea5e9",
        "days": 5,
        "tickets": [("Single", 2500, 60), ("Table for Two", 4500, 25)],
    },
    {
        "title": "Craft Beer & Street Food Festival",
        "venue": "Baltic Market, Liverpool",
        "description": "30 independent breweries, 12 street-food traders, live DJs "
                       "all afternoon. Entry includes a souvenir tasting glass.",
        "image": "#f59e0b",
        "days": 21,
        "tickets": [("Session 1 (12–4pm)", 1500, 300),
                    ("Session 2 (5–9pm)", 1500, 300),
                    ("All Day", 2500, 150)],
    },
    {
        "title": "An Evening with the Northern Comedy Collective",
        "venue": "Union Chapel, Leeds",
        "description": "Five of the sharpest stand-ups on the circuit for one "
                       "unmissable night. Recommended 16+.",
        "image": "#ec4899",
        "days": 14,
        "tickets": [("Balcony", 1600, 80), ("Stalls", 2200, 120)],
    },
]


def run():
    db.init_db()
    reset()
    for s in SAMPLES:
        eid = db.create_event(
            title=s["title"], description=s["description"], venue=s["venue"],
            starts_at=days_from_now(s["days"]), image_url=s["image"],
            currency="GBP", published=True,
        )
        for i, (name, price, qty) in enumerate(s["tickets"]):
            db.add_ticket_type(eid, name, price, qty, sort=i)
    print(f"Seeded {len(SAMPLES)} events into {db.DB_PATH}")


if __name__ == "__main__":
    run()
