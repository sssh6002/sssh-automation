import textwrap

import yaml

import fill_in_draft


_SAMPLE_CONFIG = {
    "template": "擬:\n<辦理文字>陳閱後文存查。",
    "default": {"辦理文字": "", "動作": "none"},
    # rules 越上方越優先;測試裡刻意把「不參加」放在「資安」之前,
    # 跟現實 yaml 順序一致。
    "rules": [
        {"標記": "不參加", "辦理文字": "不參加，", "動作": "none"},
        {"標記": "資安", "辦理文字": "陳會片段，", "動作": "陳會"},
        {"標記": "汰換", "辦理文字": "汰換片段，", "動作": "備選動作"},
    ],
}


def _write_config(tmp_path):
    p = tmp_path / "fill_in_draft.yaml"
    p.write_text(yaml.safe_dump(_SAMPLE_CONFIG, allow_unicode=True), encoding="utf-8")
    return p


def _write_summary(extract_dir, filename, content):
    p = extract_dir / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_read_marks_parses_second_line(tmp_path):
    _write_summary(tmp_path, "123_456總結.gemini-2.5.md", """\
        #存查分類: 資安
        ## 不參加 研習
        1. 內容
        """)
    assert fill_in_draft._read_marks(tmp_path) == ["不參加", "研習"]


def test_read_marks_no_summary_file_returns_empty(tmp_path):
    assert fill_in_draft._read_marks(tmp_path) == []


def test_read_marks_no_mark_line_returns_empty(tmp_path):
    _write_summary(tmp_path, "123_456總結.gemini.md", """\
        #存查分類: 資安
        1. 只有分類沒有標記行
        """)
    assert fill_in_draft._read_marks(tmp_path) == []


def test_read_marks_single_mark(tmp_path):
    _write_summary(tmp_path, "9_9總結.claude.md", """\
        #存查分類: 設備
        ## 汰換
        """)
    assert fill_in_draft._read_marks(tmp_path) == ["汰換"]


def test_load_rules_returns_rules_default_template(tmp_path):
    rules, default, template = fill_in_draft._load_rules(_write_config(tmp_path))
    assert default == {"辦理文字": "", "動作": "none"}
    assert len(rules) == 3
    assert template == "擬:\n<辦理文字>陳閱後文存查。"


def test_lookup_first_in_yaml_order_wins(tmp_path):
    # yaml 內「不參加」在「資安」前 → 兩個標記同時存在時取上方的「不參加」
    rules, default, _ = fill_in_draft._load_rules(_write_config(tmp_path))
    text, action = fill_in_draft._lookup(["資安", "不參加"], rules, default)
    assert (text, action) == ("不參加，", "none")


def test_lookup_single_mark_hits_its_rule(tmp_path):
    rules, default, _ = fill_in_draft._load_rules(_write_config(tmp_path))
    assert fill_in_draft._lookup(["資安"], rules, default) == ("陳會片段，", "陳會")


def test_lookup_no_match_falls_back_to_default(tmp_path):
    rules, default, _ = fill_in_draft._load_rules(_write_config(tmp_path))
    assert fill_in_draft._lookup(["不存在的標記"], rules, default) == ("", "none")


def test_lookup_empty_marks_falls_back_to_default(tmp_path):
    rules, default, _ = fill_in_draft._load_rules(_write_config(tmp_path))
    assert fill_in_draft._lookup([], rules, default) == ("", "none")


def test_render_substitutes_placeholder():
    assert (fill_in_draft._render("擬:\n<辦理文字>陳閱後文存查。", "不參加，")
            == "擬:\n不參加，陳閱後文存查。")


def test_render_empty_fragment_yields_clean_template():
    assert (fill_in_draft._render("擬:\n<辦理文字>陳閱後文存查。", "")
            == "擬:\n陳閱後文存查。")


def _patch_selenium(monkeypatch, calls, fill_ok=True, save_ok=True, chen_ok=True):
    monkeypatch.setattr(fill_in_draft, "_fill_text",
                        lambda driver, text: calls.append(("fill", text)) or fill_ok)
    monkeypatch.setattr(fill_in_draft, "_save",
                        lambda driver: calls.append(("save",)) or save_ok)
    monkeypatch.setattr(fill_in_draft, "_click_chen_hui",
                        lambda driver: calls.append(("chen_hui",)) or chen_ok)


def test_fill_in_draft_action_none_fills_saves_no_action(tmp_path, monkeypatch):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類: 研習\n## 不參加\n")
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is True
    assert calls == [("fill", "擬:\n不參加，陳閱後文存查。"), ("save",)]


def test_fill_in_draft_action_chen_hui_clicks_after_save(tmp_path, monkeypatch):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類: 資安\n## 資安\n")
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is True
    assert calls == [("fill", "擬:\n陳會片段，陳閱後文存查。"), ("save",), ("chen_hui",)]


def test_fill_in_draft_backup_action_is_noop(tmp_path, monkeypatch):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類: 設備\n## 汰換\n")
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is True
    assert calls == [("fill", "擬:\n汰換片段，陳閱後文存查。"), ("save",)]


def test_fill_in_draft_no_marks_uses_default_template(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is True
    assert calls == [("fill", "擬:\n陳閱後文存查。"), ("save",)]


def test_fill_in_draft_fill_fails_returns_false_no_save(tmp_path, monkeypatch):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類: 資安\n## 資安\n")
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls, fill_ok=False)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is False
    assert calls == [("fill", "擬:\n陳會片段，陳閱後文存查。")]


def test_fill_in_draft_save_fails_returns_false_no_action(tmp_path, monkeypatch):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類: 資安\n## 資安\n")
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls, save_ok=False)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is False
    assert calls == [("fill", "擬:\n陳會片段，陳閱後文存查。"), ("save",)]
