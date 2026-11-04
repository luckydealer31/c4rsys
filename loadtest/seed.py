"""Data of the load test (audit item 12), made through the API of the backend itself, with the
fixtures of the tests: one company, VEHICLES vehicles, CLIENTS clients, one rental issued on each
vehicle after HISTORY finished ones before it, as a company has after a year or so.
Prints the company and the session cookie of its owner for k6. Run only on a stack of its own,
never on a server with real data (scripts/loadtest.sh):
  python manage.py shell < /loadtest/seed.py"""

import os
import random
import sys
from datetime import timedelta
from unittest import mock

from django.test import Client, override_settings
from django.utils import timezone

sys.path.insert(0, "/app")

from c4rsys.rentals.models import Rental  # noqa: E402
from tests.accounts.helpers import CODE, post  # noqa: E402
from tests.companies.helpers import key, named_user  # noqa: E402
from tests.rentals.scenario import add_client, add_vehicle, build, issue  # noqa: E402

VEHICLES = int(os.environ.get("C4R_LOAD_VEHICLES", "50"))
CLIENTS = int(os.environ.get("C4R_LOAD_CLIENTS", "500"))
HISTORY = int(os.environ.get("C4R_LOAD_HISTORY", "2000"))

with (
    override_settings(ALLOWED_HOSTS=["*"], SECURE_SSL_REDIRECT=False),
    mock.patch("c4rsys.accounts.security.generate_code", return_value=CODE),
):
    owner = Client()
    named_user(owner, "load@c4rsys.test")
    s = build(owner)
    clients = [s.client] + [
        add_client(owner, s.cmp, first=f"Client{n}", phone=f"+668{n:08d}") for n in range(CLIENTS)
    ]
    vehicles = [s.vehicle] + [
        add_vehicle(owner, s, s.location, name=f"Car {n}") for n in range(VEHICLES - 1)
    ]
    # Each vehicle: ROUNDS finished rentals one after another in the past, then one still out.
    # Through the API, so every rental has its order and charges, as in real work.
    rounds = max(1, HISTORY // len(vehicles))
    first = timezone.now() - timedelta(days=3 * rounds + 3)
    n = 0
    for vehicle in vehicles:
        s.vehicle = vehicle
        for r in range(rounds + 1):
            s.client = clients[n % len(clients)]
            n += 1
            start = first + timedelta(days=3 * r, hours=random.uniform(0, 12))  # noqa: S311
            out = issue(owner, s, actual_start=start.isoformat())
            assert out.status_code == 201, out.content
            if r == rounds:
                break
            rental = out.json()["rental"]
            back = post(
                owner,
                f"/api/c/{s.cmp}/rentals/{rental['id']}/return",
                {
                    "actual_end": (start + timedelta(days=2)).isoformat(),
                    "return_location_id": s.location,
                    "odometer": "10000",
                    "fuel": "8",
                    "expected_version": rental["version"],
                },
                key(),
            )
            assert back.status_code == 200, back.content
    print(f"C4R_LOAD_RENTALS={Rental.objects.filter(company__public_id=s.cmp).count()}")
    print(f"C4R_LOAD_COMPANY={s.cmp}")
    print(f"C4R_LOAD_SESSION={owner.cookies['c4r_session'].value}")
