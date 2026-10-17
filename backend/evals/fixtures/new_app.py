"""A stand-in project generator for the ``scaffold`` eval: like ``composer create-project``, it
creates the app in the folder it is given and refuses a folder that already has files."""

import sys
from pathlib import Path

if len(sys.argv) < 2:
    sys.exit("usage: python tools/new_app.py <folder>")
target = Path(sys.argv[1])
if target.exists() and any(target.iterdir()):
    sys.exit(f'Project directory "{target}" is not empty.')
for rel, body in {
    "app.py": 'def main():\n    return "ok"\n',
    "manage.py": 'print("manage")\n',
    "config/settings.json": "{}\n",
    "routes/web.py": "ROUTES = []\n",
}.items():
    path = target / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
print("App created.")
