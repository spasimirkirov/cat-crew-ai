"""Every eval scenario's check is honest: it fails on the untouched seed and passes on a correct
solution. A check that passes on the seed would score a model that did nothing."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.evals.scenarios import SCENARIOS, Outcome

# Reference solutions: files to write over the seed (None deletes), the reply, and the tools "used".
SOLUTIONS: dict[str, dict] = {
    "off-by-one": {"files": {"pages.py": "def page(items, number, size=10):\n    start = (number - 1) * size\n    return items[start:start + size]\n"}},
    "leap-year": {"files": {"calendar_utils.py": "def is_leap(year):\n    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n"}},
    "empty-input": {"files": {"stats.py": "def average(values):\n    return sum(values) / len(values) if values else 0.0\n"}},
    "csv-quotes": {"files": {"rows.py": "import csv, io\n\n\ndef load_rows(text):\n    return list(csv.DictReader(io.StringIO(text.strip())))\n"}},
    "traceback": {"files": {"report.py": "from config import load\n\n\ndef header():\n    cfg = load()\n    return f\"Prices in {cfg['currency_code']}, VAT {int(cfg['vat'] * 100)}%\"\n"}},
    "type-bug": {"files": {"cart.py": "def cart_total(raw_prices):\n    return sum(float(r.strip().lstrip('€')) for r in raw_prices)\n"}},
    "lru-cache": {
        "files": {
            "lru.py": (
                "from collections import OrderedDict\n\n\nclass LRUCache:\n    def __init__(self, capacity):\n        self.cap, self.d = capacity, OrderedDict()\n\n"
                "    def get(self, key):\n        if key not in self.d:\n            return None\n        self.d.move_to_end(key)\n        return self.d[key]\n\n"
                "    def put(self, key, value):\n        self.d[key] = value\n        self.d.move_to_end(key)\n        if len(self.d) > self.cap:\n            self.d.popitem(last=False)\n\n"
                "    def __len__(self):\n        return len(self.d)\n"
            )
        }
    },
    "cli-flag": {
        "files": {
            "todo.py": (
                "import argparse, json\n\nTODOS = [('buy milk', False), ('ship v1', True)]\n\n\ndef main(argv=None):\n"
                "    parser = argparse.ArgumentParser()\n    sub = parser.add_subparsers(dest='cmd', required=True)\n"
                "    ls = sub.add_parser('list')\n    ls.add_argument('--json', action='store_true')\n    args = parser.parse_args(argv)\n"
                "    if args.json:\n        print(json.dumps([{'title': t, 'done': d} for t, d in TODOS]))\n        return\n"
                "    for title, done in TODOS:\n        print(f\"[{'x' if done else ' '}] {title}\")\n\n\nif __name__ == '__main__':\n    main()\n"
            )
        }
    },
    "validation": {
        "files": {
            "signup.py": (
                "def create_user(email, password):\n    at = email.find('@')\n    if at < 1 or '.' not in email[at:]:\n        raise ValueError('email')\n"
                "    if len(password) < 10:\n        raise ValueError('password')\n    return {'email': email}\n"
            )
        }
    },
    "performance": {"files": {"dedupe.py": "def unique(items):\n    return list(dict.fromkeys(items))\n"}},
    "config-migration": {
        "files": {
            "themes/dark.json": '{"name": "dark", "color": "#38BDF8"}\n',
            "themes/light.json": '{"name": "light", "color": "#0369A1"}\n',
            "theme.py": "import json\n\n\ndef accent(name):\n    with open(f'themes/{name}.json', encoding='utf-8') as fh:\n        return json.load(fh)['color']\n",
        }
    },
    "dead-code": {
        "files": {
            "orders.py": "def order_total(lines):\n    return sum(q * p for q, p in lines)\n",
            "api.py": "from orders import order_total\n\n\ndef total(lines):\n    return order_total(lines)\n",
        }
    },
    "write-tests": {
        "files": {
            "test_discount.py": (
                "import unittest\nfrom discount import discount\n\n\nclass T(unittest.TestCase):\n"
                "    def test_edges(self):\n        self.assertEqual(discount(99.99), 99.99)\n        self.assertEqual(discount(100), 90.0)\n        self.assertEqual(discount(200), 180.0)\n"
            )
        }
    },
    "broken-links": {"files": {"index.html": '<nav><a href="about.html">About</a> <a href="contact.html">Contact</a> <a href="index.html">Home</a></nav>\n'}},
    "sql-injection": {
        "files": {
            "users_db.py": (
                "import sqlite3\n\n\ndef connect():\n    db = sqlite3.connect(':memory:')\n    db.execute('CREATE TABLE users (name TEXT, role TEXT)')\n"
                "    db.executemany('INSERT INTO users VALUES (?, ?)', [('ada', 'admin'), ('bob', 'user')])\n    return db\n\n\n"
                "def find(db, name):\n    return db.execute('SELECT name, role FROM users WHERE name = ?', (name,)).fetchall()\n"
            )
        }
    },
    "hardcoded-secret": {"files": {"settings.py": "import os\n\nDEBUG = False\nPAYMENT_API_KEY = os.environ.get('PAYMENT_API_KEY', '')\n"}},
    "no-over-edit": {"files": {"greet.py": "def greet(name):\n    return f'Hi, {name}!'\n"}},
    "scoped-delete": {"files": {"build/app.bundle.js": None, "build/assets/logo.png": None}},
    "where-is-it-set": {"reply": "7 times: MAX_RETRIES = 7 in net/http_client.py overrides config.RETRIES (3).", "tools": ["read_file"]},
}


def _seed(scenario, root: Path) -> None:
    for rel, body in scenario.files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


@pytest.mark.parametrize("sid", sorted(SOLUTIONS))
def test_the_check_fails_on_the_seed_and_passes_on_a_solution(sid: str, tmp_path: Path) -> None:
    scenario = SCENARIOS[sid]
    seed = tmp_path / "seed"
    seed.mkdir()
    _seed(scenario, seed)
    ok, why = scenario.check(Outcome(seed, "", []))
    assert not ok, f"{sid}: the untouched seed passes ({why})"

    solved = tmp_path / "solved"
    shutil.copytree(seed, solved)
    plan = SOLUTIONS[sid]
    for rel, body in (plan.get("files") or {}).items():
        path = solved / rel
        if body is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    ok, why = scenario.check(Outcome(solved, plan.get("reply", "done"), list(plan.get("tools", ["write_file"]))))
    assert ok, f"{sid}: a correct solution fails ({why})"


def test_every_new_scenario_has_a_reference_solution() -> None:
    from backend.evals.scenarios_extra import EXTRA

    assert {s.id for s in EXTRA} == set(SOLUTIONS)
    assert len(SCENARIOS) >= 25
