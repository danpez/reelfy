"""Plan de prueba del nucleo: plan v1 real (dentista) + fx minimos que usan los stubs.

Solo construye el dict en memoria (la ruta del video apunta a la media local); nada de
datos del cliente queda versionado: el plan v1 vive en spike/work/fixtures (ignorado).
"""
from conftest import TEST_MEDIA, load_plan_v1

STUBS = {n: f"motion_stubs.{n}" for n in ("camera", "grade", "matte", "text", "graphics",
                                           "cutaways", "transitions")}
STUBS.update(audio=None, music=None)


def stub_plan(video=None) -> dict:
    p = load_plan_v1()                    # se salta la prueba si el fixture privado no esta
    p["video"] = str(video or (TEST_MEDIA / "crudo_completo.mp4"))
    p["version"] = 2
    # limites reales de los 6 clips en crudo_completo (compose): solo tiempos
    b = [0.0, 8.0, 13.867, 17.267, 24.267, 28.867, p["duration"]]
    p["shots"] = [{"start": a, "end": e} for a, e in zip(b, b[1:])]
    p["fx"] = {
        "captions": {"enabled": True, "preset": "clean_bold", "y": 0.72, "size": 0.05,
                     "font": "Poppins-ExtraBold", "hide_during_titles": True},
        "text": [{"id": "tx_1", "preset": "title_pop", "text": "Titulo de prueba", "start": 1.1,
                  "end": 3.2, "y": 0.2, "size": 0.075},
                 {"id": "tx_2", "preset": "stack", "text": "DETRAS", "start": 8.5, "end": 11.0,
                  "y": 0.3, "size": 0.16, "behind": True}],
        "graphics": [{"id": "gr_1", "kind": "doodle", "start": 18.0, "end": 20.5, "x": 0.72,
                      "y": 0.3, "size": 0.25, "color": "#FFD23F"},
                     {"id": "gr_2", "kind": "progress", "start": 0.0, "end": p["duration"]}],
        "cutaways": [{"id": "cw_1", "kind": "title_card", "start": 30.2, "end": 31.6,
                      "text": {"preset": "title_pop", "text": "ESCENA"}}],
        "effects": [{"id": "fx_1", "kind": "zoom", "start": 6.5, "end": 12.9, "from": 1.0, "to": 1.18},
                    {"id": "fx_2", "kind": "punch", "start": 14.6, "end": 15.6, "amount": 0.12},
                    {"id": "fx_3", "kind": "bw", "start": 24.3, "end": 26.0},
                    {"id": "fx_4", "kind": "shake", "start": 26.5, "end": 27.3}],
        "transitions": [{"id": "tr_1", "kind": "flash", "at": 3.8, "dur": 0.3},
                        {"id": "tr_2", "kind": "slide", "at": 13.8, "dur": 0.4},
                        {"id": "tr_3", "kind": "slide", "at": 29.4, "dur": 0.4}],
        "sfx": [{"id": "sf_1", "name": "pop", "at": 1.1}],
        "music": {"enabled": False, "source": "none"},
        "grade": {"enabled": True, "look": "warm_clean"},
    }
    return p
