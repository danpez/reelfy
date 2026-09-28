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


def load_factor() -> float:
    """Holgura COMUN de los umbrales de tiempo (la Mac se comparte con otros renders): con load
    > nucleos los tiempos suben en proporcion y el limite se escala igual en vez de fallar en
    falso. Usa el MAYOR de los promedios de 1 y 5 min (el de 1 min tarda en subir cuando la
    carga llega de golpe), 25 % de margen por la contencion de memoria/GPU que el load no ve, y
    tope 6x. Importar en vez de copiar: `from conftest import load_factor`."""
    import os
    try:
        la = max(os.getloadavg()[:2])
    except (OSError, AttributeError):
        return 1.0
    return min(6.0, max(1.0, 1.25 * la / (os.cpu_count() or 8)))


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
