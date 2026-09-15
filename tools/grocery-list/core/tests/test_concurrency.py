"""Requests that change the same item at the same moment.

Each WhatsApp message is its own engine process. Two people adding the same
new item, or one adding it while another marks it bought, both looked for the
row, both found none, and both inserted it: the second hit the items UNIQUE
constraint and its request died with an IntegrityError.
"""

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import groups
import grocery
import people

APP = Path(__file__).resolve().parent.parent
CAIO = "+19175550182"
ALEX = "+5511900000078"
ITEM = "Mixed Race Item"

WORKER = r"""
import contextlib, io, sys, time
sys.path.insert(0, sys.argv[1])
import cli
start = float(sys.argv[2])
while time.time() < start:
    time.sleep(0.001)
with contextlib.redirect_stdout(io.StringIO()):
    cli.run(sys.argv[3:])
"""


def ingest(actor, quantity):
    return ["ingest", "--store", "Costco", "--source-type", "text", "--source-ref", "",
            "--raw-text", "x", "--items-json", json.dumps([{"name": ITEM, "quantity": quantity}]),
            "--actor", actor]


def buy(actor):
    return ["buy", "--store", "Costco", ITEM, "--actor", actor]


class SameItemRaceTests(unittest.TestCase):
    ROUNDS = 15
    JOBS = [ingest(CAIO, 2), ingest(ALEX, 3), buy(CAIO), ingest(ALEX, 1), buy(ALEX),
            ["list", "--store", "Costco", "--actor", CAIO]]

    def fixture(self, path):
        conn = grocery.connect(path)
        home = groups.group_row(conn, None, create=True)["id"]
        groups.add_member(conn, None, CAIO, "owner")
        groups.add_member(conn, None, ALEX, "member")
        people.remember_person(conn, CAIO, "pt", "Jo")
        grocery.ingest_items(conn, "Costco", grocery.parse_items_json('["Pão"]'), "text",
                             actor=CAIO, group_id=home)
        conn.close()

    def test_adding_and_buying_one_new_item_at_once_all_succeed(self):
        failures, wrong = [], []
        with tempfile.TemporaryDirectory() as root:
            fixture = Path(root) / "fixture.sqlite3"
            self.fixture(fixture)
            for round_ in range(self.ROUNDS):
                target = Path(root) / f"round{round_}.sqlite3"
                shutil.copy(fixture, target)
                start = time.time() + 0.8
                procs = [subprocess.Popen(
                    [sys.executable, "-c", WORKER, str(APP), str(start), "--db", str(target), *argv],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for argv in self.JOBS]
                for proc in procs:
                    _, err = proc.communicate(timeout=60)
                    if proc.returncode:
                        failures.append((err.strip().splitlines() or ["?"])[-1])

                conn = sqlite3.connect(target)
                rows = conn.execute(
                    "SELECT quantity, canonical_name FROM items WHERE name = ?", (ITEM,)).fetchall()
                added = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE item_name = ? AND action = 'added'",
                    (ITEM,)).fetchone()[0]
                conn.close()
                # One row; merges keep the larger quantity, so every order ends at 3.
                if len(rows) != 1 or rows[0][0] != 3 or not rows[0][1] or added != 1:
                    wrong.append((round_, rows, added))
        self.assertEqual(failures, [], f"{len(failures)} of "
                         f"{self.ROUNDS * len(self.JOBS)} concurrent requests failed")
        self.assertEqual(wrong, [], "the item did not end as one row of quantity 3")


if __name__ == "__main__":
    unittest.main()
