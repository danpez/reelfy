"""E2E real del motor pro por HTTP (envuelve tests/e2e_pro.sh).

Pesado (compose de 6 clips 4K + whisper + LLM/VLM + render final: varios minutos) y usa un
servidor aislado en un puerto propio, asi que es OPT-IN:
    REELFY_E2E=1 .venv/bin/python -m pytest tests/test_e2e_pro.py -q
Se salta si faltan los 6 IMG_*.MOV de prueba (REELFY_TEST_MEDIA o spike/work/media).
Puerto/datos: REELFY_E2E_PORT (8013) y REELFY_E2E_DATA (spike/work/devdata_servidor).
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import TEST_MEDIA

SPIKE = Path(__file__).resolve().parents[1]
MOVS = sorted(TEST_MEDIA.glob("IMG_*.MOV")) if TEST_MEDIA.exists() else []

pytestmark = [
    pytest.mark.skipif(len(MOVS) < 6, reason="sin los 6 IMG_*.MOV de prueba"),
    pytest.mark.skipif(os.environ.get("REELFY_E2E") != "1", reason="E2E pesado: REELFY_E2E=1"),
]


def test_e2e_pro_por_http():
    out = Path(os.environ.get("REELFY_E2E_OUT") or SPIKE / "work" / "e2e")
    r = subprocess.run(["bash", str(SPIKE / "tests" / "e2e_pro.sh")], cwd=str(SPIKE),
                       capture_output=True, text=True, timeout=5400)
    if r.returncode == 77:
        pytest.skip("e2e_pro.sh: sin medios")
    res = json.loads((out / "e2e_result.json").read_text())
    failed = [k for k, v in (res.get("checks") or {}).items() if not v]
    assert r.returncode == 0 and res.get("ok"), (
        f"fallaron {failed} / error={res.get('error')}\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}")
    assert res["plan"]["shots"] == 6 and res["plan"]["version"] == 2
    assert res["final"]["W"] == 1080 and res["final"]["H"] == 1920
    assert abs(res["final"]["lufs"] + 14) <= 1.0
