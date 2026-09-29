#!/usr/bin/env python3
"""
Forced alignment (the caption-sync wedge).

whisper.cpp gives accurate TEXT but its word timestamps drift +/- vs the audio.
Here we re-time those words with a wav2vec2 CTC forced aligner (MMS_FA, multilingual
incl. Spanish), which aligns the whole transcript to the audio globally -> tight,
non-drifting word timing. We keep whisper's original text (accents/punctuation) and
only replace start/end.

Corre con **onnxruntime** (modelo int8), NO torch: el mismo modelo pesa ~315 MB en
vez de arrastrar el framework completo (~1.5 GB), funciona igual en Win/Mac/Linux
y no depende de paquetes que no compilan en Windows.

Sin el modelo (no descargado, o falla) NO se sigue en silencio con el timing crudo de
whisper (palabras de duración 0 que el render perdía, palabras estiradas sobre pausas):
  1) tiempos DTW de whisper.cpp (tokens `t_dtw` de la salida -ojf), corregido su retraso;
  2) reparación por ENERGÍA del audio: inicios/finales pegados a donde arranca y termina
     la voz en cada pausa, palabras estiradas encogidas a su voz;
  3) duración mínima para todas (ninguna palabra dicha se pierde).
`LAST` dice qué método se usó y trae el aviso en español para el plan/estado del job.
"""
import bisect
import re
import unicodedata

import numpy as np

import paths
from motion.timeline import WORD_MIN, est_word_dur, fix_word_times

# Vocabulario de MMS_FA: blank + 26 letras + apóstrofe (sin dígitos ni separador).
VOCAB = {c: i for i, c in enumerate("-aienoutsrmkldghybpwcvjzf'qx")}
BLANK = 0
# Trozo de alineación: acota memoria y tiempo del DP (que crece ~O(duración²) si
# se alinea todo de una). A 60s la desviación vs alinear completo es mediana 6ms /
# p90 22ms — imperceptible (un frame a 30fps = 33ms) — y es 2.2x más rápido.
CHUNK_SECS = 60.0
_session = None
_session_err = ""

# --- plan B sin el modelo (medido contra MMS_FA en clips reales, dos locutores). El DTW de whisper.cpp marca TARDE (mediana +145 ms al inicio de palabra,
# más en palabras largas) y su "fin" es el inicio del token siguiente (se traga la pausa
# que siga). Corrección lineal ajustada contra MMS_FA en clips de prueba (validada en otro
# locutor): inicio = t0 - (A + B*letras); fin = t0 + C + D*letras + E*(t1 - t0).
DTW_START_A = 0.094
DTW_START_B = 0.015
DTW_END_C = -0.101
DTW_END_D = 0.027
DTW_END_E = 0.242
PAUSE_MIN = 0.20        # s: silencio que cuenta como pausa entre palabras (energía)
SNAP_S = 0.20           # s: distancia máxima para pegar un inicio/fin al borde de la voz
INNER_PAUSE = 0.25      # s: pausa DENTRO de una palabra = palabra estirada sobre ella
LOOSE_MAX_S = 40.0      # s: racha sin timing confiable que se reparte sobre la voz
VAD_BELOW_TOP = 22.0    # dB: voz = más de (nivel alto de la voz - 22 dB)...
VAD_ABOVE_FLOOR = 6.0   # dB: ...y al menos 6 dB sobre el piso de ruido
MMS_DTW_MAX = 1.0       # s: MMS y DTW no discrepan tanto salvo que uno ponga la palabra en otro lado
NOTE_DTW = ("Sincronía fina de subtítulos no disponible; usé los tiempos de la "
            "transcripción ajustados con el audio")
NOTE_APPROX = "Sincronía fina de subtítulos no disponible; usé tiempos aproximados"
# Último align_words: {"method": "mms"|"dtw"|"aprox", "note": str|None, "level": None|
# "info"|"warn", "reason": str, "aligned": int, "words": int}. Lo lee el análisis para
# avisarle al usuario ('info': DTW, casi tan fino como MMS; 'warn': tiempos aproximados).
LAST: dict = {}
_SPECIAL = re.compile(r"^\[_[^\]]*\]$")      # tokens especiales de whisper.cpp ([_BEG_], [_TT_n])


def _norm(s):
    """Texto -> caracteres del vocabulario (sin acentos, minúsculas)."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z']", "", s.lower())


def _get_session():
    """Sesión de onnxruntime cacheada; None si no está el modelo (motivo en _session_err)."""
    global _session, _session_err
    if _session is not None:
        return _session
    p = paths.aligner_model_path()
    if not p or not p.exists():
        _session_err = "sin modelo"
        return None
    try:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        _session = ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"])
        _session_err = ""
        return _session
    except Exception as e:  # noqa: modelo truncado/corrupto -> plan B
        _session_err = f"modelo inválido ({e})"
        return None


def preload():
    """Carga el modelo para que el primer análisis no pague el costo.
    True si quedó listo, o si no hay modelo ni de dónde bajarlo (la sincronía usa el plan
    B: no hay nada que esperar); False si el modelo falta o no carga (analyze funciona
    igual con el plan B)."""
    if _get_session() is not None:
        return True
    return not paths.aligner_model_ready() and not paths.aligner_downloadable()


def _log_softmax(x):
    x = x - x.max(axis=-1, keepdims=True)
    return x - np.log(np.exp(x).sum(axis=-1, keepdims=True))


def _forced_align(log_probs, targets):
    """CTC forced alignment (Viterbi) sobre la secuencia EXACTA `targets`.

    log_probs: [T, V]; targets: [N] ids (sin blanks).
    Devuelve [(start_frame, end_frame)] por cada target, o (-1,-1) si no aparece.
    """
    T = log_probs.shape[0]
    N = len(targets)
    if N == 0 or T == 0:
        return [(-1, -1)] * N
    S = 2 * N + 1
    ext = np.full(S, BLANK, dtype=np.int64)
    ext[1::2] = targets
    NEG = np.float32(-1e30)

    idx = np.arange(S)
    nonblank = (idx % 2) == 1
    # Se puede saltar el blank intermedio solo entre tokens DISTINTOS.
    can_skip = np.zeros(S, dtype=bool)
    can_skip[2:] = nonblank[2:] & (ext[2:] != ext[:-2])

    prev = np.full(S, NEG, dtype=np.float32)
    prev[0] = log_probs[0, ext[0]]
    if S > 1:
        prev[1] = log_probs[0, ext[1]]
    bp = np.zeros((T, S), dtype=np.int8)   # 0=quedarse, 1=venir de s-1, 2=de s-2

    for t in range(1, T):
        one = np.full(S, NEG, dtype=np.float32)
        one[1:] = prev[:-1]
        two = np.full(S, NEG, dtype=np.float32)
        two[2:] = np.where(can_skip[2:], prev[:-2], NEG)
        cands = np.stack([prev, one, two])          # [3, S]
        best = np.argmax(cands, axis=0).astype(np.int8)
        prev = cands[best, idx] + log_probs[t, ext]
        bp[t] = best

    # backtrack desde el mejor final (último token o su blank posterior)
    s = int(S - 1 if prev[S - 1] >= prev[S - 2] else S - 2)
    path = np.zeros(T, dtype=np.int64)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= int(bp[t, s])          # backpointer del estado actual (no la fila)

    spans = []
    for n in range(N):
        fr = np.nonzero(path == (2 * n + 1))[0]
        spans.append((int(fr[0]), int(fr[-1]) + 1) if len(fr) else (-1, -1))
    return spans


def _align_chunk(sess, audio, words_tok):
    """Alinea un trozo. words_tok: [(indice_global, [ids])]. Devuelve {i: (ini,fin) seg}."""
    if not words_tok or audio.size == 0:
        return {}
    emissions = sess.run(None, {"waveform": audio[None, :].astype(np.float32)})[0][0]
    log_probs = _log_softmax(emissions.astype(np.float32))
    sec_per_frame = (audio.shape[0] / max(log_probs.shape[0], 1)) / 16000.0

    targets, owner = [], []
    for gi, ids in words_tok:
        for tid in ids:
            targets.append(tid); owner.append(gi)
    spans = _forced_align(log_probs, np.array(targets, dtype=np.int64))

    out = {}
    for (s, e), gi in zip(spans, owner):
        if s < 0:
            continue
        st, en = s * sec_per_frame, e * sec_per_frame
        if gi in out:
            out[gi] = (min(out[gi][0], st), max(out[gi][1], en))
        else:
            out[gi] = (st, en)
    return out


def _align_mms(sess, audio, words):
    """MMS_FA sobre `audio` (16k mono). -> (palabras re-timeadas, n alineadas). Conserva el
    timing de entrada de las palabras que no aportan caracteres (cifras, signos)."""
    # tokens por palabra (las que no aportan caracteres se quedan con su timing)
    toks = [(i, [VOCAB[c] for c in _norm(w["text"]) if c in VOCAB]) for i, w in enumerate(words)]
    toks = [(i, t) for i, t in toks if t]
    if not toks:
        return [dict(w) for w in words], 0

    # Trocear acota la memoria del DP (O(T·S)) y es ~2.6x más rápido, pero cortar
    # en seco parte el habla y descuadra las palabras del borde. Por eso cortamos
    # en el SILENCIO más grande cerca del objetivo: cada trozo queda acústicamente
    # completo y el resultado iguala a alinear todo de una.
    chunks, cur, c_start = [], [], None
    k = 0
    while k < len(toks):
        i, ids = toks[k]
        if c_start is None:
            c_start = max(0.0, float(words[i].get("start", 0.0)) - 0.20)
        cur.append((i, ids))
        if float(words[i].get("end", 0.0)) - c_start >= CHUNK_SECS and k + 1 < len(toks):
            # buscar el mayor hueco entre palabras en la ventana siguiente
            best_gap, best_k = -1.0, k
            for j in range(k, min(k + 12, len(toks) - 1)):
                a_end = float(words[toks[j][0]].get("end", 0.0))
                b_start = float(words[toks[j + 1][0]].get("start", a_end))
                gap = b_start - a_end
                if gap > best_gap:
                    best_gap, best_k = gap, j
                if gap >= 0.35:            # silencio claro: cortar aquí
                    break
            for j in range(k + 1, best_k + 1):     # completar hasta el corte
                cur.append(toks[j])
            a_end = float(words[toks[best_k][0]].get("end", 0.0))
            b_start = float(words[toks[best_k + 1][0]].get("start", a_end))
            cut = (a_end + b_start) / 2.0
            chunks.append((c_start, cut, cur))
            cur, c_start, k = [], cut, best_k + 1
            continue
        k += 1
    if cur:
        chunks.append((c_start or 0.0, None, cur))

    timings = {}
    for c_ini, c_fin, items in chunks:
        a = int(c_ini * 16000)
        b = int(c_fin * 16000) if c_fin is not None else len(audio)
        for gi, (s, e) in _align_chunk(sess, audio[a:b], items).items():
            timings[gi] = (s + c_ini, e + c_ini)

    out = []
    for i, w in enumerate(words):
        if i in timings:
            s, e = timings[i]
            out.append({**w, "start": round(s, 3), "end": round(e, 3)})
        else:
            out.append(dict(w))
    return out, len(timings)


# ------------------------------------------------------------------ plan B: DTW + energía
def whisper_words(data):
    """JSON de whisper.cpp (-ml 1 -sow; con -ojf trae tokens) -> [{'text','start','end'}] (s).

    Si los tokens traen `t_dtw` (--dtw sin flash-attn), usa esos tiempos corregidos
    (DTW_START_*/DTW_END_*): mucho más finos que los de whisper (mediana de error de
    inicio ~45 ms vs ~340 ms en clips cortos). El DTW falla a veces en una ventana entera
    (todas sus palabras en el mismo instante, lejos de donde whisper las oyó): esas palabras
    se quedan con el tiempo de whisper y, si falla en más del 30 %, se descarta el DTW. Cada
    palabra lleva 'tsrc' ('dtw'|'whisper'); align_words lo usa para el aviso y lo quita.
    """
    words, toks = [], []
    for seg in (data or {}).get("transcription") or []:
        txt = (seg.get("text") or "").strip()
        if not txt:
            continue
        off = seg.get("offsets") or {}
        wi = len(words)
        words.append({"text": txt, "start": float(off.get("from", 0)) / 1000.0,
                      "end": float(off.get("to", 0)) / 1000.0})
        for tk in seg.get("tokens") or []:
            tt = str(tk.get("text") or "")
            td = tk.get("t_dtw")
            if _SPECIAL.match(tt.strip()) or not isinstance(td, (int, float)) or td < 0:
                continue
            toks.append((wi, tt, float(td) / 100.0))
    n = len(words)
    dtw = [None] * n
    raw0 = [None] * n                         # t_dtw crudo del inicio (detecta colapsos)
    by_word: dict = {}
    for k, (wi, _, _) in enumerate(toks):
        by_word.setdefault(wi, []).append(k)
    for wi, ks in by_word.items():
        let = [k for k in ks if any(c.isalnum() for c in toks[k][1])] or ks
        st = toks[let[0]][2]
        nxt = let[-1] + 1                     # token siguiente (signo o palabra siguiente)
        en = toks[nxt][2] if nxt < len(toks) else max(words[wi]["end"], st)
        nl = sum(1 for c in words[wi]["text"] if c.isalpha())
        s0 = st - (DTW_START_A + DTW_START_B * nl)
        e0 = st + DTW_END_C + DTW_END_D * nl + DTW_END_E * max(0.0, en - st)
        dtw[wi] = [s0, max(e0, s0)]
        raw0[wi] = st
    # validez: rachas de >= 3 palabras en el mismo instante = ventana colapsada; o lejos de
    # donde whisper la oyó
    ok = [d is not None for d in dtw]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and raw0[i] is not None and raw0[j + 1] is not None and \
                abs(raw0[j + 1] - raw0[i]) < 1e-6:
            j += 1
        if j - i >= 2:
            for k in range(i, j + 1):
                ok[k] = False
        i = j + 1
    for k in range(n):
        if ok[k] and not (words[k]["start"] - 1.5 <= dtw[k][0] <= words[k]["end"] + 1.5):
            ok[k] = False
    use = n > 0 and sum(ok) >= 0.7 * n
    for k, w in enumerate(words):
        if use and ok[k]:
            w["start"], w["end"] = round(max(0.0, dtw[k][0]), 3), round(max(0.0, dtw[k][1]), 3)
            w["tsrc"] = "dtw"
        else:
            w["tsrc"] = "whisper"
    return words


def _pauses(audio, sr=16000):
    """Pausas de la voz [(inicio, fin)] (s, >= PAUSE_MIN) por energía RMS (cuadros de 25 ms
    cada 10 ms, umbral adaptado al piso de ruido). None si la voz no se distingue del fondo
    (música/ruido fuerte): ahí la energía no sirve y no se toca nada."""
    hop, win = int(sr * 0.01), int(sr * 0.025)
    a = np.asarray(audio, dtype=np.float64)
    if a.ndim > 1:
        a = a.mean(axis=1)
    if len(a) < win * 10:
        return None
    nf = 1 + (len(a) - win) // hop
    c = np.concatenate([[0.0], np.cumsum(a * a)])
    st = np.arange(nf) * hop
    db = 10.0 * np.log10((c[st + win] - c[st]) / win + 1e-10)
    dig = db <= -80.0                     # silencio digital (cámaras con compuerta de ruido)
    live = db[~dig]
    if live.size < 10:
        return None
    floor, top = np.percentile(live, 10), np.percentile(db, 95)
    if dig.mean() >= 0.05:                # hay silencio digital de verdad: ése es el piso
        floor = min(floor, -80.0)
    if top - floor < 12.0:
        return None
    v = db > max(floor + VAD_ABOVE_FLOOR, top - VAD_BELOW_TOP)
    # huecos de < 50 ms dentro de la voz (oclusivas) -> voz; chasquidos de < 30 ms -> silencio
    for val, mx in ((False, 5), (True, 3)):
        k = 0
        while k < nf:
            if v[k] != val:
                k += 1
                continue
            m = k
            while m < nf and v[m] == val:
                m += 1
            if m - k < mx and k > 0 and m < nf:
                v[k:m] = not val
            k = m
    t0 = win / 2.0 / sr                   # centro del cuadro
    out, k = [], 0
    while k < nf:
        if v[k]:
            k += 1
            continue
        m = k
        while m < nf and not v[m]:
            m += 1
        ps, pe = k * 0.01 + t0, m * 0.01 + t0
        if k == 0:
            ps = 0.0
        if m >= nf:
            pe = len(a) / sr
        if pe - ps >= PAUSE_MIN:
            out.append((ps, pe))
        k = m
    return out


def _snap_to_voice(words, pauses, keep="end"):
    """Pega inicios y finales a los bordes de la voz alrededor de cada pausa y encoge las
    palabras que se tragan una pausa. Solo toca bordes junto a una pausa: dentro del habla
    continua la energía no separa palabras."""
    n = len(words)
    if not n or not pauses:
        return words
    S = [float(w["start"]) for w in words]
    E = [float(w["end"]) for w in words]
    P0 = [p[0] for p in pauses]

    def around(t, before, after):
        """Pausa con inicio-before <= t <= fin+after (la más cercana)."""
        k = bisect.bisect_right(P0, t + before)
        for j in (k - 1, k - 2, k):
            if 0 <= j < len(pauses):
                ps, pe = pauses[j]
                if ps - before <= t <= pe + after:
                    return ps, pe
        return None

    # 1) finales: dentro de una pausa -> donde calla la voz; poco antes -> se extiende
    for i in range(n):
        p = around(E[i], SNAP_S, 0.0)
        if not p:
            continue
        ps, pe = p
        nxt = S[i + 1] if i + 1 < n else float("inf")
        if ps > S[i] + WORD_MIN / 2 and nxt >= ps - 0.02 and E[i] <= pe:
            E[i] = ps
    # 2) inicios: dentro de una pausa -> donde arranca la voz; poco después -> se adelanta
    for i in range(n):
        p = around(S[i], 0.0, SNAP_S)
        if not p:
            continue
        ps, pe = p
        prv = E[i - 1] if i else float("-inf")
        if S[i] >= ps - 0.03 and pe < E[i] - WORD_MIN / 2 and prv <= pe + 0.02:
            S[i] = pe
    # 3) pausa larga DENTRO de la palabra (o que la palabra se traga desde su borde): la
    #    palabra está de un lado (whisper crudo mete la pausa antes de la palabra; el DTW,
    #    después)
    for i in range(n):
        k = max(0, bisect.bisect_right(P0, S[i]) - 1)
        inner = []
        while k < len(pauses) and pauses[k][0] < E[i]:
            ps, pe = pauses[k]
            if min(pe, E[i]) - max(ps, S[i]) >= INNER_PAUSE:
                inner.append((ps, pe))
            k += 1
        if keep == "start":
            cut = [ps for ps, _ in inner if ps - S[i] >= WORD_MIN]
            if cut:
                E[i] = cut[0]
        else:
            cut = [pe for _, pe in inner if E[i] - pe >= WORD_MIN]
            if cut:
                S[i] = cut[-1]
    out = []
    for w, s, e in zip(words, S, E):
        out.append({**w, "start": s, "end": max(e, s)})
    return out


def _place_on_voice(ws, i, j, a, b, pauses):
    """Reparte las palabras i..j (timing no confiable) sobre el tiempo CON VOZ de [a, b], en
    orden y en proporción a su duración estimada. False si ahí casi no hay voz."""
    segs, t = [], a
    for ps, pe in pauses:
        if pe <= a or ps >= b:
            continue
        if ps > t:
            segs.append((t, min(ps, b)))
        t = max(t, pe)
    if t < b:
        segs.append((t, b))
    segs = [(x, y) for x, y in segs if y - x >= 0.05]     # astillas junto a las anclas
    voiced = sum(y - x for x, y in segs)
    est = [est_word_dur(ws[k].get("text")) for k in range(i, j + 1)]
    if not segs or voiced < 0.3 * sum(est):
        return False

    def at(v):
        """Tiempo real del instante v de voz acumulada."""
        for x, y in segs:
            if v <= y - x + 1e-9:
                return x + v, y
            v -= y - x
        return segs[-1][1], segs[-1][1]

    acc, tot = 0.0, sum(est)
    for k, d in zip(range(i, j + 1), est):
        s, seg_end = at(voiced * acc / tot)
        acc += d
        e, _ = at(voiced * acc / tot)
        ws[k]["start"], ws[k]["end"] = s, min(e, seg_end)   # no cruza una pausa
    return True


def repair_timings(words, audio=None, sr=16000, keep="end", energy=True, cap=True, loose=None):
    """Tiempos utilizables para subtítulos sin perder ninguna palabra (mismas palabras, mismo
    orden). Con `audio` (y `energy`): las rachas `loose` (índices con timing no confiable,
    p. ej. una ventana donde falló el DTW) se reparten sobre la voz entre sus vecinas
    confiables (si abarcan <= LOOSE_MAX_S), y los bordes se pegan a la voz en cada pausa;
    luego fix_word_times: orden, sin traslapes, estiradas encogidas (`cap`) y duración
    mínima. `keep`: de qué lado se queda una palabra estirada ('end' whisper crudo, 'start'
    DTW)."""
    ws = [dict(w) for w in words or []]
    hi = pauses = None
    if audio is not None:
        hi = len(audio) / float(sr)
        if energy:
            pauses = _pauses(audio, sr)
    if pauses and loose:
        # antes de ordenar: un tiempo basura de la racha no debe recortar a sus vecinas
        lz = sorted(set(k for k in loose if 0 <= k < len(ws)))
        r = 0
        while r < len(lz):
            i = j = lz[r]
            while r + 1 < len(lz) and lz[r + 1] == j + 1:
                r += 1
                j = lz[r]
            r += 1
            try:
                a = float(ws[i - 1]["end"]) if i else 0.0
                b = float(ws[j + 1]["start"]) if j + 1 < len(ws) else hi
            except (KeyError, TypeError, ValueError):
                continue
            if 0 < b - a <= LOOSE_MAX_S:
                _place_on_voice(ws, i, j, a, b, pauses)
    ws = fix_word_times(ws, keep=keep, min_dur=0.0, cap=False)     # orden, sin traslapes
    if pauses:
        ws = _snap_to_voice(ws, pauses, keep)
    ws = fix_word_times(ws, keep=keep, hi=hi, cap=cap)
    for w in ws:
        w["start"], w["end"] = round(w["start"], 3), round(w["end"], 3)
    return ws


def _shrink_stretched(words, pauses):
    """La alineación forzada a veces estira una palabra sobre la pausa que la sigue o la
    precede ("nada." de 2.3 s, "hacer" de 6 s): la palabra se tacha en el Studio y sale de los
    subtítulos porque su centro cae en el silencio cortado. Una palabra de más de STRETCH_MIN_S
    y 2.5x su duración típica con una pausa adentro se queda del lado donde HAY voz (medido, no
    supuesto: la alineación la puede estirar hacia cualquiera de los dos lados)."""
    if not pauses:
        return words, 0
    from motion.timeline import STRETCH_FACTOR, STRETCH_MIN_S
    out, n = [], 0
    for w in words:
        s, e = float(w["start"]), float(w["end"])
        d = est_word_dur(w.get("text"))
        if e - s <= max(STRETCH_MIN_S, STRETCH_FACTOR * d):
            out.append(w)
            continue
        inner = [(max(ps, s), min(pe, e)) for ps, pe in pauses
                 if min(pe, e) - max(ps, s) >= INNER_PAUSE]
        if not inner:
            out.append(w)
            continue
        if _voiced(s, s + d, pauses) >= _voiced(e - d, e, pauses):
            ns, ne = s, inner[0][0]                 # la voz está al inicio: acaba donde calla
        else:
            ns, ne = inner[-1][1], e                # la voz está al final: empieza donde arranca
        if ne - ns >= WORD_MIN:
            out.append({**w, "start": round(ns, 3), "end": round(ne, 3)})
            n += 1
        else:
            out.append(w)
    return out, n


def _voiced(a, b, pauses):
    """Fraccion de [a, b] (s) con voz: fuera de las pausas."""
    L = max(1e-3, b - a)
    sil = sum(max(0.0, min(b, pe) - max(a, ps)) for ps, pe in pauses if pe > a and ps < b)
    return max(0.0, 1.0 - sil / L)


def _prefer_voice(out, ref, audio):
    """La alineacion forzada a veces arrastra palabras a un SILENCIO lejos de donde se dijeron (la
    Viterbi reparte el texto a su modo: una frase entera puede quedar 5 s antes, en plena
    pausa, y los subtitulos lo perdian con el corte del silencio). Si el tiempo del alineador
    discrepa del DTW de whisper en > MMS_DTW_MAX s, cae en silencio y el del DTW cae en voz,
    manda el DTW. Donde ambos caen en voz (p. ej. dos tomas iguales y el texto de una) no se toca.
    -> (palabras, cuantas volvieron al DTW)."""
    pauses = _pauses(audio) if audio is not None else None
    if not pauses:
        return out, 0
    res, back = list(out), 0
    for i, (o, r) in enumerate(zip(out, ref or [])):
        if r.get("tsrc") != "dtw":
            continue
        try:
            os_, oe, rs, re_ = float(o["start"]), float(o["end"]), float(r["start"]), float(r["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(os_ - rs) <= MMS_DTW_MAX:
            continue
        if _voiced(os_, max(oe, os_ + 0.05), pauses) < 0.3 and _voiced(rs, max(re_, rs + 0.05), pauses) >= 0.5:
            res[i] = {**o, "start": round(rs, 3), "end": round(max(re_, rs), 3)}
            back += 1
    return res, back


def _read_audio(wav_path):
    import soundfile as sf
    audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:                      # el wav del pipeline ya viene a 16k
        import librosa
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
    return audio


def align_words(wav_path, words):
    """Devuelve `words` con start/end re-timeados. NUNCA pierde palabras ni lanza por el
    alineador: con MMS_FA, alineación forzada (y duración mínima para las que no se
    alinean); sin el modelo o si falla, plan B (DTW de whisper y/o energía del audio).
    Deja en LAST el método usado y el aviso para el usuario."""
    global _session
    n = len(words or [])
    LAST.clear()
    if not n:
        LAST.update(method="none", note=None, level=None, reason="", aligned=0, words=0)
        return []
    src = "dtw" if sum(1 for w in words if w.get("tsrc") == "dtw") >= 0.7 * n else "whisper"
    clean = [{k: v for k, v in w.items() if k != "tsrc"} for w in words]
    audio = None
    try:
        audio = _read_audio(wav_path)
    except Exception as e:  # noqa: sin audio legible solo queda la reparación sin energía
        print(f"   align: no se pudo leer el audio ({e})")
    sess = _get_session()
    reason = (_session_err or "sin modelo") if sess is None else "audio ilegible"
    if sess is not None and audio is not None:
        try:
            out, got = _align_mms(sess, audio, clean)
            out, back = _prefer_voice(out, words, audio)
            out = repair_timings(out, audio, keep="end", energy=False, cap=False)
            out, _ = _shrink_stretched(out, _pauses(audio))
            LAST.update(method="mms", note=None, level=None, reason="", aligned=got, words=n)
            print(f"   forced-aligned {got}/{n} words (onnx)"
                  + (f"; {back} devueltas al DTW (el alineador las puso en un silencio)" if back else ""))
            return out
        except Exception as e:  # noqa: sesión rota (memoria, modelo corrupto) -> plan B
            reason = f"falló ({e})"
            _session = None
    keep = "start" if src == "dtw" else "end"
    # sin timing confiable: las palabras de una ventana donde falló el DTW, entre vecinas
    # con DTW (sin DTW en todo el video no hay anclas: repartir todo sobre la voz empeora)
    loose = [i for i, w in enumerate(words) if w.get("tsrc") != "dtw"] if src == "dtw" else []
    out = repair_timings(clean, audio, keep=keep, loose=loose)
    method = "dtw" if src == "dtw" else "aprox"
    LAST.update(method=method, note=NOTE_DTW if method == "dtw" else NOTE_APPROX,
                level="info" if method == "dtw" else "warn", reason=reason, aligned=0, words=n)
    print(f"   align: afinador no disponible ({reason}); tiempos {method} reparados "
          f"({'con' if audio is not None else 'sin'} energía)")
    return out


def last_note():
    """Aviso en español del último align_words (None si hubo sincronía fina)."""
    return LAST.get("note")


if __name__ == "__main__":
    import json, sys
    d = json.load(open(sys.argv[2]))
    for w in align_words(sys.argv[1], whisper_words(d))[:10]:
        print(f'  {w["start"]:.2f}-{w["end"]:.2f}  {w["text"]!r}')
    print(LAST)
