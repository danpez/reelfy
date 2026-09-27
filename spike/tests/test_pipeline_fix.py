"""Regresion del bug de pipeline.render_from_plan: el bucle de cortes pisaba `c` (custom) y
el export ignoraba zoom/air/logo cuando habia cortes habilitados."""
import pipeline


def test_export_respeta_custom_con_cortes(monkeypatch, tmp_path):
    got = {}

    def fake_reframe(video, ass, out, **kw):
        got.update(kw)
        got["out"] = out

    monkeypatch.setattr(pipeline, "reframe_and_burn", fake_reframe)
    monkeypatch.setattr(pipeline, "build_ass", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "_emoji_overlays", lambda *a, **k: None)
    plan = {"stem": "t", "video": str(tmp_path / "v.mp4"), "duration": 10.0, "cmds_path": None,
            "camera": [], "phrases": [], "highlights": [], "beats": [1.0],
            "cuts": [{"start": 2.0, "end": 3.0, "enabled": True},
                     {"start": 6.0, "end": 7.0, "enabled": True}]}
    custom = {"zoom_amt": 0.2, "air": 0.07, "logo": {"size": 0.1, "pos": "tl"}}
    pipeline.render_from_plan(plan, tmp_path, custom=custom)
    assert got["zoom_amt"] == 0.2 and got["air"] == 0.07
    assert got["logo"] == {"size": 0.1, "pos": "tl"}
    assert got["keeps"] == [(0.0, 2.0), (3.0, 6.0), (7.0, 10.0)]
