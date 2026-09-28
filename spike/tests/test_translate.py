"""scripts/translate.py y POST /translate: titulos del plan v2 traducidos COMPLETOS (sus lineas
son partes de una misma frase), con el MISMO numero de lineas, en el idioma pedido (nunca gallego
por portugues), y sin disfrazar de traduccion el espanol cuando la IA local no responde.

Sin red ni LLM real: un `_chat` falso hace de Ollama (textos inventados, sin datos de clientes).
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import translate as T

SPIKE = Path(__file__).resolve().parents[1]


class FakeLLM:
    """Responde por prompt: `rules` = lista de (condicion(system, user) -> respuesta dict | None)."""

    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def __call__(self, system, user):
        self.calls.append((system, user))
        out = self.fn(system, user, len(self.calls))
        if isinstance(out, Exception):
            raise out
        return json.dumps(out)


def _lines_of(user):
    return json.loads(user.split("línea(s)): ", 1)[1].split("\n\n")[0])["lineas"]


PT = {"Si cuando pasas": "Se quando você passa", "la lengua": "a língua", "por tus dientes": "pelos seus dentes",
      "y no te preocupes": "e não se preocupe", "no vamos a hablar": "não vamos falar",
      "LIMPIEZA": "LIMPEZA", "Agenda tu cita": "Agende sua consulta"}


def test_titulos_conservan_lineas_mayusculas_y_neutros(monkeypatch):
    def fn(system, user, n):
        lines = _lines_of(user)
        assert "português do Brasil" in system and "NÃO galego" in system
        return {"lineas": [PT.get(x, x + " (pt)").lower() if x.isupper() else PT.get(x, x + " (pt)")
                           for x in lines]}
    fake = FakeLLM(fn)
    monkeypatch.setattr(T, "_chat", fake)
    texts = ["Si cuando pasas\nla lengua\npor tus dientes", "LIMPIEZA", "$450", "y no te preocupes\nno vamos a hablar"]
    out, miss, err = T.translate_titles(texts, "pt")
    assert out == ["Se quando você passa\na língua\npelos seus dentes", "LIMPEZA", "$450",
                   "e não se preocupe\nnão vamos falar"]
    assert miss == [] and err is None
    assert len(fake.calls) == 3                              # un titulo por llamada; lo neutro no
    # las lineas del mismo titulo viajan JUNTAS (se traduce la frase completa, no cada fragmento)
    assert _lines_of(fake.calls[0][1]) == ["Si cuando pasas", "la lengua", "por tus dientes"]


def test_contexto_hablado_solo_en_titulos_largos():
    words = "Oye si cuando pasas la lengua por tus dientes ya sientes algo raro necesitamos hablar".split()
    ctx = T.spoken_context("Si cuando pasas\nla lengua\npor tus dientes", words)
    assert ctx == "...Oye [si cuando pasas la lengua por tus dientes]"     # sin lo que sigue
    assert T.spoken_context("$450", words) is None
    assert T.spoken_context("hablar de otra cosa distinta", words) is None


def test_reintenta_lineas_idioma_y_contexto_colado(monkeypatch):
    """Lineas de mas, gallego o una traduccion que tambien tradujo el contexto: se reintenta
    estricto y SIN contexto; el ultimo respaldo reparte la traduccion en las lineas del original."""
    seq = {
        # 1er titulo: gallego -> el reintento estricto da portugues
        1: {"lineas": ["e non te preocupes", "non imos falar"]},
        2: {"lineas": ["e não se preocupe", "não vamos falar"]},
        # 2o titulo: el modelo tradujo tambien el contexto (larguisimo) -> reintento sin contexto
        3: {"lineas": ["Se quando você passa a língua pelos seus dentes você já sente algo estranho e",
                       "precisamos falar sobre isso agora mesmo", "hoje"]},
        4: {"lineas": ["Se quando você passa", "a língua", "pelos seus dentes"]},
        # 3er titulo: nunca respeta las lineas -> se reparte
        5: {"lineas": ["Agende sua consulta hoje mesmo"]},
        6: {"lineas": ["Agende sua consulta hoje mesmo"]},
    }
    fake = FakeLLM(lambda s, u, n: seq[n])
    monkeypatch.setattr(T, "_chat", fake)
    texts = ["y no te preocupes\nno vamos a hablar", "Si cuando pasas\nla lengua\npor tus dientes",
             "Agenda tu cita\nhoy mismo"]
    ctx = [None, "...Oye [si cuando pasas la lengua por tus dientes] ya sientes...", None]
    out, miss, err = T.translate_titles(texts, "pt", ctx)
    assert out[0] == "e não se preocupe\nnão vamos falar"
    assert out[1] == "Se quando você passa\na língua\npelos seus dentes"
    assert out[2].count("\n") == 1 and out[2].replace("\n", " ") == "Agende sua consulta hoje mesmo"
    assert miss == [] and err is None
    assert "Contexto hablado" in fake.calls[2][1] and "Contexto hablado" not in fake.calls[3][1]
    assert "IMPORTANTE" in fake.calls[1][0]


def test_sin_ollama_informa_y_no_insiste(monkeypatch):
    fake = FakeLLM(lambda s, u, n: ConnectionRefusedError("Connection refused"))
    monkeypatch.setattr(T, "_chat", fake)
    out, miss, err = T.translate_titles(["Hola\nmundo", "$450", "Otra cosa"], "de")
    assert out == ["Hola\nmundo", "$450", "Otra cosa"] and miss == [0, 2] and "refused" in err
    assert len(fake.calls) == 1                          # no reintenta titulo por titulo
    out, miss, err = T.translate_texts_ex(["hola mundo", "que tal"], "pt")
    assert out == ["hola mundo", "que tal"] and miss == [0, 1] and err
    info = {}
    ph = [[{"text": "hola", "start": 0.0, "end": 0.5}, {"text": "mundo", "start": 0.5, "end": 1.0}]]
    T.translate_phrases(ph, "fr", info)
    assert info["untranslated"] == [0] and info["error"]


def test_subtitulos_en_portugues_no_gallego(monkeypatch):
    """translate_texts_ex (subtitulos): el idioma se nombra en su lengua y una salida con palabras
    de gallego (o de espanol) se reintenta estricta."""
    def fn(system, user, n):
        assert "português do Brasil" in system
        d = json.loads(user.split("\n\n")[0])
        if n == 1:
            return {k: "non te preocupes" for k in d}
        return {k: "não se preocupe" for k in d}
    monkeypatch.setattr(T, "_chat", FakeLLM(fn))
    out, miss, err = T.translate_texts_ex(["no te preocupes", "tranquilo"], "pt")
    assert out == ["não se preocupe", "não se preocupe"] and miss == [] and err is None


def test_endpoint_translate_con_ia_falsa(tmp_path):
    """POST /translate {texts, lang, spans?}: texts_<lang> con las lineas del original y
    `untranslated`; contexto por `spans` (tiempo original) o buscando el titulo en lo que se
    dice; 503 solo si la IA no tradujo NADA."""
    code = textwrap.dedent('''
        import sys, json
        sys.path.insert(0, "app")
        import server as S, jobstore
        import translate as T
        from fastapi import HTTPException
        seen = []
        def chat(system, user):
            seen.append(user)
            if "rotulo roto" in user:
                return json.dumps({"lineas": ["rotulo roto"]})       # "traduccion" = original
            lines = json.loads(user.split("línea(s)): ", 1)[1].split("\\n\\n")[0])["lineas"]
            return json.dumps({"lineas": ["[de] " + x for x in lines]})
        T._chat = chat
        words = [{"text": w, "start": 0.3 * i, "end": 0.3 * i + 0.25} for i, w in enumerate(
            "hola hoy te cuento como cuidar tu sonrisa todos los dias sin gastar de mas".split())]
        plan = {"version": 2, "phrases": [words[:8], words[8:]], "fx": {}}
        jobstore.create("0123456789ab", kind="analyze", ext=".mp4", plan=plan)
        r = S.translate("0123456789ab", {"texts": ["como cuidar\\ntu sonrisa todos", "$99", "rotulo roto"],
                                         "lang": "de"})
        assert r["texts_de"] == ["[de] como cuidar\\n[de] tu sonrisa todos", "$99", "rotulo roto"], r
        assert r["untranslated"] == [2]
        assert "[como cuidar tu sonrisa todos]" in seen[0]          # contexto del plan guardado
        seen.clear()
        r = S.translate("0123456789ab", {"texts": ["un rotulo que no se dice"], "spans": [[2.4, 3.0]],
                                         "lang": "it"})
        assert "sin gastar" in seen[0] and r["texts_it"] == ["[de] un rotulo que no se dice"]
        try:
            S.translate("0123456789ab", {"texts": ["rotulo roto"], "lang": "fr"})
            raise AssertionError("debio responder 503")
        except HTTPException as e:
            assert e.status_code == 503
        print("OK")
    ''')
    env = dict(os.environ, REELFY_DATA=str(tmp_path), REELFY_GATED="0", REELFY_AUTO_DOWNLOAD="0",
               REELFY_OLLAMA_URL="http://127.0.0.1:9", REELFY_WORKERS="0", REELFY_BG_ANALYSIS="0",
               REELFY_SRC_PROXY="0")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(SPIKE), env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
