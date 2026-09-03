"""summarize_doc 的 LLM backend 設定/派發/清理 純函式與接縫測試。

不打真實 LLM:antigravity 走 _run_agy_pty 接縫、aistudio 走 urllib.urlopen,
皆以 monkeypatch 注入假值。真實 end-to-end 於實機驗證(antigravity/aistudio 已實測)。
"""
import urllib.request

import pytest

import summarize_doc as sd


# ───────────────────────── _parse_order ─────────────────────────

def test_parse_order_full_chain_in_order():
    assert sd._parse_order("antigravity,aistudio,claude,anthropic") == [
        "antigravity", "aistudio", "claude", "anthropic"]


def test_parse_order_strips_and_lowercases():
    assert sd._parse_order("  Claude , Antigravity ") == ["claude", "antigravity"]


def test_parse_order_filters_unknown_names():
    assert sd._parse_order("antigravity,foo,claude,gemini") == ["antigravity", "claude"]


def test_parse_order_dedupes_keeping_first_position():
    assert sd._parse_order("claude,claude,antigravity,claude") == ["claude", "antigravity"]


def test_parse_order_empty_or_none_returns_empty():
    assert sd._parse_order("") == []
    assert sd._parse_order(None) == []
    assert sd._parse_order("   ") == []


# ───────────────────────── _get_backend_order ─────────────────────────

def test_get_backend_order_defaults_when_unset(monkeypatch):
    monkeypatch.setattr(sd, "_read_config", lambda key: None)
    assert sd._get_backend_order() == list(sd.DEFAULT_LLM_ORDER)


def test_get_backend_order_uses_config(monkeypatch):
    monkeypatch.setattr(sd, "_read_config",
                        lambda key: "claude,antigravity" if key == "summarize_llm_order" else None)
    assert sd._get_backend_order() == ["claude", "antigravity"]


def test_get_backend_order_falls_back_to_default_when_all_unknown(monkeypatch):
    monkeypatch.setattr(sd, "_read_config",
                        lambda key: "foo,bar" if key == "summarize_llm_order" else None)
    assert sd._get_backend_order() == list(sd.DEFAULT_LLM_ORDER)


# ───────────────────────── _strip_ansi ─────────────────────────

def test_strip_ansi_removes_agy_conpty_prefix():
    raw = "\x1b[1t\x1b[c\x1b[?1004h\x1b[?9001hPONG_5678\r\n"
    out = sd._strip_ansi(raw)
    assert "\x1b" not in out
    assert "\r" not in out
    assert out.strip() == "PONG_5678"


def test_strip_ansi_removes_color_csi_and_osc():
    raw = "\x1b]0;title\x07\x1b[31mRED\x1b[0m text"
    out = sd._strip_ansi(raw)
    assert out == "RED text"


def test_strip_ansi_keeps_plain_text_untouched():
    assert sd._strip_ansi("純文字\n第二行") == "純文字\n第二行"


# ───────────────────────── _parse_gemini_key_from_text ─────────────────────────

SAMPLE_CRED = """# 共用帳密

## Claude 專用 Google 帳號
- **Email**：`claudejoe0000@gmail.com`

## Google Gemini API Key（Generative Language API）
- **Key**：`AQ.Ab8RN6_TESTKEY_xyz`（新版 `AQ.` 格式,長度 53）
- **環境變數名**：`GEMINI_API_KEY`
"""


def test_parse_gemini_key_extracts_key():
    assert sd._parse_gemini_key_from_text(SAMPLE_CRED) == "AQ.Ab8RN6_TESTKEY_xyz"


def test_parse_gemini_key_returns_none_when_absent():
    assert sd._parse_gemini_key_from_text("# 沒有金鑰的檔案\n- foo: bar") is None


# ───────────────────────── _resolve_gemini_key 優先序 ─────────────────────────

def test_resolve_gemini_key_prefers_env_env(monkeypatch):
    monkeypatch.setattr(sd, "_read_config",
                        lambda key: "ENVENV_KEY" if key == "google_ai_studio_api_key" else None)
    monkeypatch.setenv("GEMINI_API_KEY", "ENVVAR_KEY")
    assert sd._resolve_gemini_key() == "ENVENV_KEY"


def test_resolve_gemini_key_falls_back_to_shared_credentials(monkeypatch):
    monkeypatch.setattr(sd, "_read_config", lambda key: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(sd, "_read_shared_gemini_key", lambda: "SHARED_KEY")
    assert sd._resolve_gemini_key() == "SHARED_KEY"


# ───────────────────────── _llm_summarize_aistudio ─────────────────────────

class _FakeResp:
    def __init__(self, payload, status=200):
        import json
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_aistudio_no_key_returns_none(monkeypatch):
    monkeypatch.setattr(sd, "_resolve_gemini_key", lambda: None)
    assert sd._llm_summarize_aistudio("hi") == (None, None)


def test_aistudio_success_returns_text_and_model(monkeypatch):
    monkeypatch.setattr(sd, "_resolve_gemini_key", lambda: "KEY123")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = req.data
        return _FakeResp({
            "candidates": [{"content": {"parts": [{"text": "摘要結果"}]}, "finishReason": "STOP"}],
            "modelVersion": "gemini-2.5-flash",
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    text, model = sd._llm_summarize_aistudio("請總結")
    assert text == "摘要結果"
    assert model == "gemini-2.5-flash"
    assert "generativelanguage.googleapis.com" in captured["url"]
    assert sd.AISTUDIO_DEFAULT_MODEL in captured["url"]
    assert captured["headers"].get("x-goog-api-key") == "KEY123"
    assert b"\\u8acb\\u7e3d\\u7d50" in captured["body"] or "請總結".encode("utf-8") in captured["body"]


def test_aistudio_non_retryable_http_error_returns_none(monkeypatch):
    monkeypatch.setattr(sd, "_resolve_gemini_key", lambda: "KEY123")
    n = {"c": 0}

    def boom(req, timeout=None):
        n["c"] += 1
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert sd._llm_summarize_aistudio("x") == (None, None)
    assert n["c"] == 1  # 400 非暫時性 → 不重試


def _urlopen_seq(behaviors):
    """依序回傳 behaviors[i] 的假 urlopen;Exception 就 raise,dict 就包成 _FakeResp。
    超出長度沿用最後一個(模擬持續失敗)。"""
    state = {"n": 0}

    def fake(req, timeout=None):
        i = state["n"]
        state["n"] += 1
        b = behaviors[min(i, len(behaviors) - 1)]
        if isinstance(b, Exception):
            raise b
        return _FakeResp(b)

    fake.state = state
    return fake


def test_aistudio_retries_transient_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(sd, "_resolve_gemini_key", lambda: "KEY123")
    monkeypatch.setattr(sd.time, "sleep", lambda s: None)  # 不要真的等
    fake = _urlopen_seq([
        urllib.error.HTTPError("u", 503, "overloaded", {}, None),
        {"candidates": [{"content": {"parts": [{"text": "重試後成功"}]}}],
         "modelVersion": "gemini-2.5-flash"},
    ])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    text, model = sd._llm_summarize_aistudio("x")
    assert text == "重試後成功"
    assert model == "gemini-2.5-flash"
    assert fake.state["n"] == 2  # 第一次 503、第二次成功


def test_aistudio_gives_up_after_persistent_503(monkeypatch):
    monkeypatch.setattr(sd, "_resolve_gemini_key", lambda: "KEY123")
    monkeypatch.setattr(sd.time, "sleep", lambda s: None)
    fake = _urlopen_seq([urllib.error.HTTPError("u", 503, "overloaded", {}, None)])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert sd._llm_summarize_aistudio("x") == (None, None)
    assert fake.state["n"] == sd._AISTUDIO_MAX_TRIES


# ───────────────────────── _llm_summarize_antigravity ─────────────────────────

def test_antigravity_cleans_pty_output_and_labels_default(monkeypatch):
    monkeypatch.setattr(sd, "_read_config", lambda key: None)
    monkeypatch.setattr(sd, "_run_agy_pty",
                        lambda argv, timeout=240: "\x1b[?9001h總結內容\r\n")
    text, model = sd._llm_summarize_antigravity("prompt")
    assert text == "總結內容"
    assert model == sd.AGY_DEFAULT_MODEL_LABEL


def test_antigravity_passes_model_flag_and_label_when_configured(monkeypatch):
    monkeypatch.setattr(sd, "_read_config",
                        lambda key: "Gemini 3.5 Flash (Low)" if key == "summarize_agy_model" else None)
    seen = {}

    def fake_pty(argv, timeout=240):
        seen["argv"] = argv
        return "ok\r\n"

    monkeypatch.setattr(sd, "_run_agy_pty", fake_pty)
    text, model = sd._llm_summarize_antigravity("prompt")
    assert text == "ok"
    assert model == "Gemini 3.5 Flash (Low)"
    assert "--model" in seen["argv"]
    assert "Gemini 3.5 Flash (Low)" in seen["argv"]


def test_antigravity_declines_when_prompt_too_large(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(sd, "_read_config", lambda key: None)
    monkeypatch.setattr(sd, "_run_agy_pty",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "x")
    big = "x" * (sd._AGY_MAX_PROMPT + 1)
    assert sd._llm_summarize_antigravity(big) == (None, None)
    assert called["n"] == 0  # 沒去叫 pty


def test_antigravity_returns_none_when_pty_fails(monkeypatch):
    monkeypatch.setattr(sd, "_read_config", lambda key: None)
    monkeypatch.setattr(sd, "_run_agy_pty", lambda argv, timeout=240: None)
    assert sd._llm_summarize_antigravity("prompt") == (None, None)


# ───────────────────────── claude backend 參數 ─────────────────────────

class _FakeCompleted:
    def __init__(self, stdout):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


_CLAUDE_OK_JSON = ('{"result": "摘要內容", '
                   '"modelUsage": {"claude-sonnet-5": {"inputTokens": 1}}}')


def _run_claude_capturing_cmd(monkeypatch, config):
    """跑 _llm_summarize_claude_code,攔下 subprocess cmd。config=dict 注入 _read_config。"""
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\fake\claude.exe")
    monkeypatch.setattr(sd, "_read_config", lambda key: config.get(key))
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _FakeCompleted(_CLAUDE_OK_JSON)

    monkeypatch.setattr(sd.subprocess, "run", fake_run)
    text, model = sd._llm_summarize_claude_code("prompt")
    assert (text, model) == ("摘要內容", "claude-sonnet-5")
    return seen["cmd"]


def test_claude_default_cmd_has_no_model_or_effort(monkeypatch):
    cmd = _run_claude_capturing_cmd(monkeypatch, {})
    assert "--model" not in cmd
    assert "--effort" not in cmd


def test_claude_passes_model_flag_when_configured(monkeypatch):
    cmd = _run_claude_capturing_cmd(monkeypatch, {"summarize_claude_model": "sonnet"})
    i = cmd.index("--model")
    assert cmd[i + 1] == "sonnet"
    assert "--effort" not in cmd


def test_claude_passes_effort_flag_when_configured(monkeypatch):
    cmd = _run_claude_capturing_cmd(
        monkeypatch,
        {"summarize_claude_model": "sonnet", "summarize_claude_effort": "medium"})
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--effort") + 1] == "medium"


# ───────────────────────── _find_agy 定位 ─────────────────────────

def test_find_agy_uses_path_when_available(monkeypatch):
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\onpath\agy.exe")
    assert sd._find_agy() == r"C:\onpath\agy.exe"


def test_find_agy_falls_back_to_localappdata_when_not_on_path(monkeypatch):
    monkeypatch.setattr(sd.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
    expected = r"C:\Users\test\AppData\Local\agy\bin\agy.exe"
    monkeypatch.setattr(sd.os.path, "isfile", lambda p: p == expected)
    assert sd._find_agy() == expected


def test_find_agy_returns_none_when_nowhere(monkeypatch):
    monkeypatch.setattr(sd.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
    monkeypatch.delenv("AGY_EXE", raising=False)
    monkeypatch.setattr(sd.os.path, "isfile", lambda p: False)
    assert sd._find_agy() is None


# ───────────────────────── _call_backends 派發 ─────────────────────────

def test_call_backends_first_success_wins_and_skips_rest(monkeypatch):
    monkeypatch.setattr(sd, "_get_backend_order", lambda: ["aistudio", "claude"])
    calls = []
    monkeypatch.setattr(sd, "_llm_summarize_aistudio",
                        lambda p: calls.append("aistudio") or ("AI 摘要", "gemini-2.5-flash"))
    monkeypatch.setattr(sd, "_llm_summarize_claude_code",
                        lambda p: calls.append("claude") or ("不該被叫", "claude-x"))
    text, backend, model = sd._call_backends("prompt")
    assert (text, backend, model) == ("AI 摘要", "aistudio", "gemini-2.5-flash")
    assert calls == ["aistudio"]


def test_call_backends_falls_through_to_next_on_failure(monkeypatch):
    monkeypatch.setattr(sd, "_get_backend_order", lambda: ["antigravity", "aistudio"])
    monkeypatch.setattr(sd, "_llm_summarize_antigravity", lambda p: (None, None))
    monkeypatch.setattr(sd, "_llm_summarize_aistudio", lambda p: ("退而求其次", "gemini-2.5-flash"))
    text, backend, model = sd._call_backends("prompt")
    assert (text, backend, model) == ("退而求其次", "aistudio", "gemini-2.5-flash")


def test_call_backends_all_fail_returns_none_triple(monkeypatch):
    monkeypatch.setattr(sd, "_get_backend_order", lambda: ["antigravity", "anthropic"])
    monkeypatch.setattr(sd, "_llm_summarize_antigravity", lambda p: (None, None))
    monkeypatch.setattr(sd, "_llm_summarize_anthropic", lambda p: (None, None))
    assert sd._call_backends("prompt") == (None, None, None)


# ── modelUsage 主模型判定（2026-07-28）────────────────────────────────────

def test_dominant_model_picks_the_heaviest_user():
    """prompt 一長,Claude Code 會另叫小模型跑雜事;不能取第一個 key。"""
    from summarize_doc import _dominant_model
    usage = {
        "claude-haiku-4-5-20251001": {"inputTokens": 12, "outputTokens": 3},
        "claude-sonnet-4-6": {"inputTokens": 9000, "outputTokens": 700},
    }
    assert _dominant_model(usage) == "claude-sonnet-4-6"


def test_dominant_model_counts_cache_tokens():
    from summarize_doc import _dominant_model
    usage = {
        "small": {"inputTokens": 50, "outputTokens": 50},
        "big": {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 9000},
    }
    assert _dominant_model(usage) == "big"


def test_dominant_model_handles_empty_and_odd_shapes():
    from summarize_doc import _dominant_model
    assert _dominant_model({}) is None
    assert _dominant_model(None) is None
    assert _dominant_model({"only": None}) == "only"


# ───────────────── 紙本轉線上(掃描影像、主檔無文字層) ─────────────────
# 文書組收到實體公文 → 掃成 PDF → 承辦人在 edoc 按「轉線上」。轉完之後它是一般
# 電子公文,但主檔是掃描影像、pypdf 抽不到字。這種公文的附件是實體海報,只能人工
# 辦 —— 不可以送 LLM(零星雜訊會被瞎編成一份像模像樣的假摘要)。

def _make_doc_dir(tmp_path, name="MWAA1156008767", main="29391312_11530938061.pdf"):
    d = tmp_path / name
    d.mkdir()
    (d / main).write_bytes(b"%PDF-1.4 fake")
    return d


def test_scanned_main_pdf_writes_manual_marker_and_skips_llm(tmp_path, monkeypatch):
    d = _make_doc_dir(tmp_path)
    monkeypatch.setattr(sd, "_pdf_to_text", lambda p: "")
    called = []
    monkeypatch.setattr(sd, "_call_backends", lambda prompt: called.append(prompt) or (None, None, None))

    assert sd.summarize_doc(d) is None
    assert called == [], "掃描件不可以送 LLM"
    markers = list(d.glob("*紙本轉線上需手動處理.txt"))
    assert len(markers) == 1
    body = markers[0].read_text(encoding="utf-8")
    assert "紙本轉線上" in body and "海報" in body


def test_ocr_scraps_below_threshold_still_marked_manual(tmp_path, monkeypatch):
    """掃描器附帶 OCR 撈到零星幾個字 —— 一樣不夠做總結,照樣交回人工。"""
    d = _make_doc_dir(tmp_path)
    monkeypatch.setattr(sd, "_pdf_to_text", lambda p: "臺北市 松山 高中\n海報\n")
    called = []
    monkeypatch.setattr(sd, "_call_backends", lambda prompt: called.append(prompt) or (None, None, None))

    assert sd.summarize_doc(d) is None
    assert called == []
    assert len(list(d.glob("*紙本轉線上需手動處理.txt"))) == 1


def test_normal_text_layer_is_not_marked_as_paper(tmp_path, monkeypatch):
    """一般電子公文(主檔有文字層)不可以被誤標成紙本轉線上。"""
    d = _make_doc_dir(tmp_path)
    monkeypatch.setattr(sd, "_pdf_to_text", lambda p: "檢送本會辦理研習資訊一案,請查照。" * 20)
    monkeypatch.setattr(sd, "_call_backends", lambda prompt: (None, None, None))

    sd.summarize_doc(d)
    assert list(d.glob("*紙本轉線上需手動處理.txt")) == []


def test_no_main_pdf_at_all_is_not_marked_as_paper(tmp_path, monkeypatch):
    """目錄裡根本沒有主檔 → 是「找不到主檔」,不是掃描件,不可以寫紙本標記。"""
    d = tmp_path / "MWAA1156000000"
    d.mkdir()
    (d / "隨便.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(sd, "_pdf_to_text", lambda p: "")

    assert sd.summarize_doc(d) is None
    assert list(d.glob("*紙本轉線上需手動處理.txt")) == []
