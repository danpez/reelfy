"""Config comun de pytest: agrega spike/scripts al path para `import motion.*` y `import paths`."""
import copy
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FIXTURES = Path(__file__).resolve().parents[1] / "work" / "fixtures"
TEST_MEDIA = Path(__import__("os").environ.get("REELFY_TEST_MEDIA") or Path(__file__).resolve().parents[1] / "work" / "media")
PLAN_V1 = FIXTURES / "dentista_plan_v1.json"
_PLAN_CACHE: dict = {}


def load_plan_v1() -> dict:
    """Plan v1 real de la prueba (vive en spike/work/fixtures, ignorado por git).

    En un clon limpio o en CI no existe: la prueba que lo pide se SALTA (no falla)."""
    if not PLAN_V1.exists():
        pytest.skip("sin fixture local work/fixtures/dentista_plan_v1.json (dato privado)")
    if "v1" not in _PLAN_CACHE:
        _PLAN_CACHE["v1"] = json.loads(PLAN_V1.read_text())
    return copy.deepcopy(_PLAN_CACHE["v1"])


@pytest.fixture
def plan_v1() -> dict:
    return load_plan_v1()
