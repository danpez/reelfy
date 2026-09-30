"""Armar con IA SIN guion (scripts/montage.py): el "Guion detectado" de una sesion.

Alineacion multiple de todos los intentos en orden de grabacion; gana la redaccion de la ultima
toma limpia y fluida; lo dicho una vez junto a material repetido pasa por la regla de
enriquecimiento; charla, bloopers y comentarios meta quedan fuera. Solo sesiones SINTETICAS
inventadas (repo publico)."""
import montage as M
from test_montage import chosen, mk, norm_words, tl_words


def lines(r):
    return [x["text"] for x in r["script"]["lines"]]


def flat(r):
    return " ".join(lines(r)).lower()


def test_guion_detectado_gana_la_ultima_toma_limpia_sin_charla_ni_bloopers(tmp_path):
    clips = [
        mk(tmp_path, "n1", "Hace tres años abrí una panadería en el centro.", 0, setup=0),
        mk(tmp_path, "n2", "¿Ya está grabando? |0.4| ¿Le doy? |0.4| ¿Así está bien?", 1, setup=0),
        mk(tmp_path, "n3", "Hace tres años abrí una panadería pequeña en el centro. |0.8| Al principio "
                           "vendíamos apenas veinte panes al día.", 2, setup=0),
        mk(tmp_path, "n4", "Todo cambió cuando empezamos a publicar videos del horno.", 5, setup=1),
        mk(tmp_path, "n5", "Todo cambió cuando empezamos a subir videos del horno, |0.3| puta madre.", 6, setup=1),
        mk(tmp_path, "n6", "Si quieres probar nuestro pan de elote, visítanos este sábado.", 9, setup=2),
    ]
    r = M.build(clips, None)
    assert r["script"]["source"] == "detected"
    txt = flat(r)
    assert "pequeña" in txt and "apenas veinte panes" in txt and "publicar" in txt
    for junk in ("grabando", "le doy", "puta", "subir"):
        assert junk not in txt, junk
    # orden de grabacion y secciones (una por lugar: nadie dice dos de ellas seguidas)
    assert txt.index("hace tres años") < txt.index("al principio") < txt.index("todo cambió") < txt.index("si quieres")
    assert len(r["script"]["sections"]) == 3
    # se arma igual que con guion: tomas limpias mas recientes, cobertura completa, sin basura
    assert set(x["sid"] for x in r["timeline"]) == {"n3", "n4", "n6"}
    said = " ".join(tl_words(r, clips)).lower()
    assert "grabando" not in said and "puta" not in said and "subir" not in said
    assert r["uncovered"] == [] and r["stats"]["coverage"] == 1.0
    why = {(d["sid"], d["why"]) for d in r["discarded"]}
    assert ("n2", "fuera_de_guion") in why and ("n5", "blooper") in why
    assert "n5" in {x.replace(".MOV", "") for x in r["stats"]["detected"]["suspects"]}


def test_ruta_nueva_con_titubeo_no_reemplaza_a_una_limpia(tmp_path):
    clips = [
        mk(tmp_path, "t1", "Nuestro pan de elote se hornea todos los días en horno de leña.", 0),
        mk(tmp_path, "t2", "Nuestro pan de elote se |1.1| cocina todos los días en horno de leña.", 1),
    ]
    r = M.build(clips, None)
    assert "hornea" in flat(r) and "cocina" not in flat(r)
    assert chosen(r, "L01") == ["t1"]
    # sin titubeo la ultima si gana
    clips[1] = mk(tmp_path, "t2", "Nuestro pan de elote se cocina todos los días en horno de leña.", 1)
    r = M.build(clips, None)
    assert "cocina" in flat(r) and "hornea" not in flat(r)
    assert chosen(r, "L01") == ["t2"]


def test_retoma_corta_de_una_linea_no_la_duplica(tmp_path):
    clips = [
        mk(tmp_path, "s1", "Pusimos un letrero nuevo en la puerta de la panadería. |0.6| ¿Clientes nuevos? "
                           "|0.6| Ninguno.", 0),
        mk(tmp_path, "s2", "La gente pasaba por la banqueta, pero nadie entraba a probar.", 3),
        mk(tmp_path, "s3", "¿Clientes nuevos? |0.6| Ninguno.", 4),
    ]
    r = M.build(clips, None)
    assert flat(r).count("ninguno") == 1 and flat(r).count("clientes nuevos") == 1
    said = norm_words(tl_words(r, clips))
    assert said.count("ninguno") == 1 and said.count("clientes") == 1
    assert said.index("pusimos") < said.index("ninguno") < said.index("gente")


def test_toma_parcial_retoma_de_cola_y_linea_nueva_sin_duplicar(tmp_path):
    clips = [
        mk(tmp_path, "q1", "Probamos muchas recetas antes de abrir...", 0, setup=1),
        mk(tmp_path, "q2", "Probamos muchas recetas antes de abrir, |0.7| y la de elote fue la favorita de "
                           "todos.", 1, setup=2),
        mk(tmp_path, "q3", "La de elote fue la favorita de todos. |0.9| Hoy vendemos cien piezas diarias en "
                           "el barrio.", 2, setup=3),
    ]
    r = M.build(clips, None)
    said = norm_words(tl_words(r, clips))
    expect = norm_words("Probamos muchas recetas antes de abrir y la de elote fue la favorita de todos "
                        "Hoy vendemos cien piezas diarias en el barrio".split())
    assert said == expect, said
    assert r["uncovered"] == [] and r["stats"]["coverage"] == 1.0


def test_comentario_meta_tras_la_toma_no_entra(tmp_path):
    clips = [
        mk(tmp_path, "k1", "Todo cambió cuando empezamos a publicar videos del horno.", 0),
        mk(tmp_path, "k2", "Todo cambió cuando empezamos a publicar videos del horno. |0.9| Ay no, me equivoqué "
                           "otra vez.", 1),
        mk(tmp_path, "k3", "En un mes llegamos a cinco mil seguidores.", 3),
    ]
    r = M.build(clips, None)
    assert "equivoqué" not in flat(r) and "otra vez" not in flat(r)
    assert "equivoqu" not in " ".join(tl_words(r, clips)).lower()
    assert chosen(r, "L01") == ["k1"]         # la toma con el comentario es sospechosa: gana la limpia


def test_charla_fuera_de_microfono_no_entra(tmp_path):
    clips = [
        mk(tmp_path, "o1", "Hace tres años abrí una panadería pequeña en el centro.", 0),
        mk(tmp_path, "o2", "Oye pásame el agua que está en la mesa por favor.", 1, level_db=-44),
        mk(tmp_path, "o3", "Si quieres probar nuestro pan de elote, visítanos este sábado.", 2),
    ]
    r = M.build(clips, None)
    assert "agua" not in flat(r)
    assert set(x["sid"] for x in r["timeline"]) == {"o1", "o3"}


def test_frase_nueva_junto_a_material_repetido_entra_si_aporta(tmp_path):
    base = [
        mk(tmp_path, "x1", "Nuestro pan de elote se hornea todos los días en horno de leña.", 0),
        mk(tmp_path, "x2", "Nuestro pan de elote se hornea todos los días en horno de leña. |0.9| Además los "
                           "domingos regalamos café de olla a los vecinos.", 1),
    ]
    r = M.build(base, None)
    assert "domingos regalamos café" in flat(r)
    assert "domingos" in " ".join(tl_words(r, base)).lower()
    # el juez LLM solo puede vetar (dos "no")
    r = M.build(base, None, llm=lambda p: "No")
    assert "domingos" not in flat(r)
    r = M.build(base, None, llm=lambda p: "Sí")
    assert "domingos" in flat(r)


def test_guion_detectado_es_editable_y_rearmable(tmp_path):
    clips = [
        mk(tmp_path, "e1", "Soy Ana y tengo una panadería en Puebla.", 0),
        mk(tmp_path, "e2", "Soy Ana y tengo una panadería pequeña en Puebla. |0.8| Hacemos pan de elote "
                           "todos los días.", 1),
        mk(tmp_path, "e3", "Si quieres probarlo, visítanos este sábado.", 3),
    ]
    r = M.build(clips, None)
    txt = r["script"]["text"]
    assert "Ana" in txt and "Puebla" in txt              # nombres propios con mayuscula
    assert all(x[:1].isupper() for x in lines(r))        # cada linea empieza con mayuscula
    r2 = M.build(clips, txt)                             # el usuario lo edita (o no) y se rearma
    assert r2["script"]["source"] == "user"
    assert [x["sid"] for x in r2["timeline"]] == [x["sid"] for x in r["timeline"]]
    assert r2["script"]["lines"] == r["script"]["lines"]


def test_sesion_sin_repeticiones_es_todo_guion_en_orden(tmp_path):
    clips = [mk(tmp_path, f"u{k}", t, k) for k, t in enumerate([
        "Hace tres años abrí una panadería pequeña en el centro.",
        "Al principio vendíamos apenas veinte panes al día.",
        "Todo cambió cuando empezamos a publicar videos del horno.",
    ])]
    r = M.build(list(reversed(clips)), None)             # el orden lo da la fecha, no la subida
    assert [x["sid"] for x in r["timeline"]] == ["u0", "u1", "u2"]
    assert len(lines(r)) == 3 and r["uncovered"] == []
