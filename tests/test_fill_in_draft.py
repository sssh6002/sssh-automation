import textwrap

import yaml

import fill_in_draft


_SAMPLE_CONFIG = {
    "template": "擬:\n<承辦文字>陳閱後文存查。",
    "default": {"承辦文字": "遵照辦理", "動作": "none"},
}


def _write_config(tmp_path):
    p = tmp_path / "fill_in_draft.yaml"
    p.write_text(yaml.safe_dump(_SAMPLE_CONFIG, allow_unicode=True), encoding="utf-8")
    return p


def _write_summary(extract_dir, filename, content):
    p = extract_dir / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ── _read_action:從 *總結*.md 抽承辦文字 + 動作 ───────────────────────────

def test_read_action_parses_second_and_third_lines(tmp_path):
    _write_summary(tmp_path, "123_456總結.gemini.md", """\
        #存查分類:研習 03750401
        ##參加線上研習
        ###陳會

        1. 內容
        """)
    assert fill_in_draft._read_action(tmp_path) == ("參加線上研習", "陳會")


def test_read_action_no_summary_file(tmp_path):
    assert fill_in_draft._read_action(tmp_path) == (None, None)


def test_read_action_only_fragment_no_action(tmp_path):
    # 第二行有 ##,但第三行沒(直接內容) → action 應該 None
    _write_summary(tmp_path, "9_9總結.x.md", """\
        #存查分類:資安
        ##本案為宣導，
        1. 條列內容
        """)
    assert fill_in_draft._read_action(tmp_path) == ("本案為宣導，", None)


def test_read_action_no_hash_lines(tmp_path):
    _write_summary(tmp_path, "1_1總結.x.md", """\
        #存查分類:研習
        1. 純內文沒任何 ##
        """)
    assert fill_in_draft._read_action(tmp_path) == (None, None)


def test_read_action_empty_hash_lines_treated_as_none(tmp_path):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類:資安\n##\n###\n")
    assert fill_in_draft._read_action(tmp_path) == (None, None)


def test_read_action_extra_spaces_stripped(tmp_path):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類:研習\n##   參加  \n###   陳會  \n")
    assert fill_in_draft._read_action(tmp_path) == ("參加", "陳會")


# ── _load_config:讀 yaml(只剩 default + template) ────────────────────────

def test_load_config_returns_default_and_template(tmp_path):
    default, template = fill_in_draft._load_config(_write_config(tmp_path))
    assert default == {"承辦文字": "遵照辦理", "動作": "none"}
    assert template == "擬:\n<承辦文字>陳閱後文存查。"


def test_load_config_missing_keys_fall_back_to_builtin_defaults(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    default, template = fill_in_draft._load_config(p)
    assert default == {"承辦文字": "", "動作": "none"}
    assert "<承辦文字>" in template


# ── _render ────────────────────────────────────────────────────────────────

def test_render_substitutes_placeholder():
    assert (fill_in_draft._render("擬:\n<承辦文字>陳閱後文存查。", "不參加，")
            == "擬:\n不參加，陳閱後文存查。")


def test_render_empty_fragment_yields_clean_template():
    assert (fill_in_draft._render("擬:\n<承辦文字>陳閱後文存查。", "")
            == "擬:\n陳閱後文存查。")


# ── fill_in_draft 主流程 ──────────────────────────────────────────────────

def _patch_selenium(monkeypatch, calls, fill_ok=True, save_ok=True, chen_ok=True):
    monkeypatch.setattr(fill_in_draft, "_fill_text",
                        lambda driver, text: calls.append(("fill", text)) or fill_ok)
    monkeypatch.setattr(fill_in_draft, "_save",
                        lambda driver: calls.append(("save",)) or save_ok)
    monkeypatch.setattr(fill_in_draft, "_click_chen_hui",
                        lambda driver: calls.append(("chen_hui",)) or chen_ok)


def test_fill_in_draft_action_none_from_summary(tmp_path, monkeypatch):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類:研習\n##不參加，\n###none\n")
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is True
    assert calls == [("fill", "擬:\n不參加，陳閱後文存查。"), ("save",)]


def test_fill_in_draft_action_chen_hui_from_summary(tmp_path, monkeypatch):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類:資安\n##本案為宣導，\n###陳會\n")
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is True
    assert calls == [("fill", "擬:\n本案為宣導，陳閱後文存查。"), ("save",), ("chen_hui",)]


def test_fill_in_draft_backup_action_is_noop(tmp_path, monkeypatch):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類:設備\n##汰換，\n###備選動作\n")
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is True
    assert calls == [("fill", "擬:\n汰換，陳閱後文存查。"), ("save",)]


def test_fill_in_draft_no_summary_uses_full_default(tmp_path, monkeypatch):
    # 沒總結檔 → 承辦文字 + 動作 都套 default
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is True
    assert calls == [("fill", "擬:\n遵照辦理陳閱後文存查。"), ("save",)]


def test_fill_in_draft_summary_missing_action_uses_default_action(tmp_path, monkeypatch):
    # 第二行有,第三行沒 → 承辦文字用 summary,動作套 default("none")
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類:研習\n##本案為宣導，\n1. 內容\n")
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is True
    assert calls == [("fill", "擬:\n本案為宣導，陳閱後文存查。"), ("save",)]


def test_fill_in_draft_fill_fails_returns_false_no_save(tmp_path, monkeypatch):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類:資安\n##本案為宣導，\n###陳會\n")
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls, fill_ok=False)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is False
    assert calls == [("fill", "擬:\n本案為宣導，陳閱後文存查。")]


def test_fill_in_draft_save_fails_returns_false_no_action(tmp_path, monkeypatch):
    _write_summary(tmp_path, "1_1總結.x.md", "#存查分類:資安\n##本案為宣導，\n###陳會\n")
    cfg = _write_config(tmp_path)
    calls = []
    _patch_selenium(monkeypatch, calls, save_ok=False)
    ok = fill_in_draft.fill_in_draft(driver=None, extract_dir=tmp_path, config_path=cfg)
    assert ok is False
    assert calls == [("fill", "擬:\n本案為宣導，陳閱後文存查。"), ("save",)]
