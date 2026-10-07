#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export PYTHONPATH="$ROOT/coupons:$ROOT/share:$ROOT/monitor${PYTHONPATH:+:$PYTHONPATH}"
export KDDI_ENTRY_IP=198.51.100.10
PYTHON=${PYTHON:-python3}
NODE=${NODE:-node}
"$PYTHON" -m unittest discover -s "$ROOT/coupons" -p 'test_*.py' -v
"$PYTHON" -m unittest discover -s "$ROOT/monitor" -p 'test_*.py' -v
"$PYTHON" -m unittest discover -s "$ROOT/share" -p 'test_*.py' -v
"$NODE" "$ROOT/share/test_worker.mjs"
"$PYTHON" "$ROOT/coupons/coupons.py" --help >/dev/null
"$PYTHON" "$ROOT/coupons/personal.py" --username personal_owner_example >/dev/null
"$PYTHON" "$ROOT/share/deploy_pages.py" --help >/dev/null
"$PYTHON" - "$ROOT" <<'PY'
import ast,json,sys
from pathlib import Path
root=Path(sys.argv[1])
for p in root.rglob('*.py'):
    ast.parse(p.read_text(),filename=str(p))
for p in (root/'deploy').glob('*.json.example'):
    json.loads(p.read_text())
print('PYTHON_SYNTAX_AND_JSON_TEMPLATES_OK')
PY
