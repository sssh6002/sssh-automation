# -*- coding: utf-8 -*-
"""`doctor.py`（環境檢查）的把關測試。

這支的輸出**會被整段複製貼給別人看**（安裝說明就是這樣寫的:「直接複製整段，
它不會印出你的 PIN 與密碼」）。所以第一條測試就是釘這件事。

其餘釘的:
  · 缺了必填設定要算「一定要處理」（❌），不是溫馨提醒
  · 規格檔讀到 0 筆要算 ❌ —— 那代表 YAML 壞了，而那道保護會靜靜失效
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import doctor  # noqa: E402
import env_config as ec  # noqa: E402

SECRET_PIN = "246802"
SECRET_PW = "my-secret-password"


@pytest.fixture(autouse=True)
def clean():
    """每條測試各自算自己的 ❌。"""
    doctor.BAD.clear()
    yield
    doctor.BAD.clear()


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    p = tmp_path / "env.env"
    p.write_text(
        f"pin={SECRET_PIN}\n"
        f"sssh_account=acc123\n"
        f"sssh_password={SECRET_PW}\n"
        f"sssh_publish_unit=資訊媒體組\n"
        f"summarize_llm_order=claude\n",
        encoding="utf-8")
    monkeypatch.setattr(ec, "ENV_FILE", str(p))
    return p


def test_doctor_never_prints_secrets(fake_env, capsys):
    """輸出裡不可以有 PIN 或密碼 —— 這支的輸出是要拿給別人看的。"""
    doctor.check_env()
    out = capsys.readouterr().out
    assert SECRET_PIN not in out
    assert SECRET_PW not in out
    # 但要講得出「有沒有填」，不然檢查就沒意義。
    assert "pin 已填" in out
    assert doctor.BAD == []


def test_doctor_flags_missing_required_key(tmp_path, monkeypatch, capsys):
    """必填的沒填要算 ❌（會直接讓收文或張貼跑不動），不是提醒。"""
    p = tmp_path / "env.env"
    p.write_text("pin=123456\nsummarize_llm_order=claude\n", encoding="utf-8")
    monkeypatch.setattr(ec, "ENV_FILE", str(p))
    doctor.check_env()
    out = capsys.readouterr().out
    assert "sssh_publish_unit 沒填" in out
    assert any("sssh_publish_unit" in b for b in doctor.BAD)


def test_doctor_flags_placeholder_values(tmp_path, monkeypatch, capsys):
    """從範例檔複製過來、還沒改的值要算 ❌，不能報「已填」。

    安裝流程會把 env_example.env 複製成 env.env，而那份是有值的（pin=000000）。
    報「✅ 已填」會讓人以為好了，然後在收文插卡那一刻才發現 PIN 是錯的。
    """
    ex = tmp_path / "env_example.env"
    ex.write_text("pin=000000\nsssh_account=abcdefg12345\n"
                  "sssh_password=abc123\nsssh_publish_unit=系管師群組\n",
                  encoding="utf-8")
    p = tmp_path / "env.env"
    p.write_text("pin=000000\nsssh_account=abcdefg12345\n"
                 "sssh_password=abc123\nsssh_publish_unit=系管師群組\n"
                 "summarize_llm_order=claude\n", encoding="utf-8")
    monkeypatch.setattr(ec, "ENV_FILE", str(p))
    monkeypatch.setattr(ec, "EXAMPLE_FILE", str(ex))
    doctor.check_env()
    out = capsys.readouterr().out
    assert "範例值" in out
    assert any("範例值" in b for b in doctor.BAD)
    assert "pin 已填" not in out                 # 不可以報成已填


def test_doctor_flags_missing_env_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ec, "ENV_FILE", str(tmp_path / "沒有這個檔"))
    doctor.check_env()
    assert any("env.env" in b for b in doctor.BAD)


def test_doctor_flags_broken_routing_yaml(monkeypatch, capsys):
    """規格檔讀到 0 筆 = YAML 壞了 → 一定要處理。

    `routing_flags()` 讀不到就回全空，「他人業務不自動送陳核」那道保護會**靜靜
    失效**，畫面上什麼都不會說。所以這裡必須是 ❌。
    """
    import spec_forms as sf
    monkeypatch.setattr(sf, "routing_state", lambda: {
        "生效中": {"本人字別": 0, "他人字別": 0, "關鍵字": 0, "未知字別": "pass"}})
    monkeypatch.setattr(sf, "table_state", lambda: {"有這張表": True, "列": [{}]})
    doctor.check_specs()
    out = capsys.readouterr().out
    assert "0 筆" in out
    assert any("0 筆" in b for b in doctor.BAD)


def test_doctor_reports_unreadable_routing_yaml(monkeypatch):
    import spec_forms as sf
    monkeypatch.setattr(sf, "routing_state",
                        lambda: {"生效中": {"錯誤": "ScannerError: 壞了"}})
    monkeypatch.setattr(sf, "table_state", lambda: {"有這張表": True, "列": []})
    doctor.check_specs()
    assert any("讀不進去" in b for b in doctor.BAD)


def test_doctor_checks_the_interpreter_that_runs_it(capsys):
    """坑 #4:這台機器有兩個 Python，只有一支有 openpyxl。

    doctor 要檢查**跑它的那一支**（而 啟動.bat 用同一支跑 ui.py）——
    檢查別支等於沒檢查。
    """
    doctor.check_two_pythons()
    out = capsys.readouterr().out
    assert sys.executable in out


# ── 中文安裝路徑（2026-08-14，同事那台踩到）───────────────────────────────

def test_doctor_warns_about_non_ascii_install_path(monkeypatch, capsys):
    """路徑有中文要講出來 —— 公文會被下載到別的資料夾。

    根因:KdApp 的「匯出公文資料」對話框是用模擬實體鍵盤填路徑的，而 VkKeyScanW
    只認得 ASCII，中文字被 `continue` 跳過:
        D:\\D_資訊系統\\自動辦文工具\\document_download
      → D:\\D_\\document_download
    對話框照樣關、程式照樣往下跑 —— 又是「看起來成功」那一類。
    """
    monkeypatch.setattr(doctor, "_BASE_DIR", r"D:\D_資訊系統\自動辦文工具")
    doctor.check_path_ascii()
    out = capsys.readouterr().out
    assert "非英文字元" in out
    assert "剪貼簿" in out            # 講出程式已經有的補救
    assert "sssh-tool" in out         # 也講出零風險的做法
    # 只是警告,不是 ❌ —— 剪貼簿那條路多半會成功,擋下來就沒人收得到文了
    assert doctor.BAD == []


def test_doctor_is_quiet_when_path_is_ascii(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "_BASE_DIR", r"D:\sssh-automation")
    doctor.check_path_ascii()
    out = capsys.readouterr().out
    assert "最安全的狀態" in out
    assert "非英文字元" not in out


# ── claude 那一棒有沒有登入（2026-08-18 加）───────────────────────────────
# 為什麼要有這幾條:8/18 早上收文，公文其實下載好了，但四個 AI backend 全滅，
# 畫面顯示「摘要 0 筆」，看起來像沒收到文。當時 doctor 是綠的 —— 它只檢查
# 「claude 這個命令在不在」，沒檢查有沒有登入。

class _R:
    """假的 subprocess.run 回傳值。"""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _fake_run(monkeypatch, r):
    monkeypatch.setattr(doctor.shutil, "which", lambda n: r"C:\fake\claude.exe")
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: r)


def test_login_probe_catches_not_logged_in(monkeypatch):
    """沒登入的招牌長相:結束碼 1、stderr 空的、原因藏在 stdout 的 JSON 裡。
    只看結束碼與 stderr 就只會得到一句什麼都沒講的錯誤（8/18 的 run.log 就長那樣）。"""
    _fake_run(monkeypatch, _R(
        returncode=1, stderr="",
        stdout='{"is_error":true,"result":"Not logged in \u00b7 Please run /login"}'))
    state, msg = doctor.claude_login_state()
    assert state == "nologin"
    assert "login" in msg.lower()


def test_login_probe_catches_error_with_exit_code_zero(monkeypatch):
    """結束碼 0 也可能是失敗的 —— CLI 會用 is_error 標記。
    這個 fork 最常見的坑就是「回報成功、其實沒做到」，所以不能只信結束碼。"""
    _fake_run(monkeypatch, _R(
        returncode=0,
        stdout='{"is_error":true,"result":"Credit balance is too low"}'))
    state, msg = doctor.claude_login_state()
    assert state == "fail"
    assert "Credit" in msg


def test_login_probe_passes_when_logged_in(monkeypatch):
    _fake_run(monkeypatch, _R(
        returncode=0, stdout='{"is_error":false,"result":"可以用了"}'))
    assert doctor.claude_login_state()[0] == "ok"


def test_login_probe_reports_missing_command(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    assert doctor.claude_login_state()[0] == "missing"


def test_login_probe_survives_garbage_stdout(monkeypatch):
    """CLI 吐非 JSON（例如更新提示混進來）不可以讓整支 doctor 掛掉。"""
    _fake_run(monkeypatch, _R(returncode=1, stdout="<html>不是 JSON</html>",
                              stderr="something broke"))
    state, msg = doctor.claude_login_state()
    assert state == "fail"
    assert "something broke" in msg


def test_not_logged_in_is_a_must_fix(monkeypatch, capsys):
    """沒登入要算 ❌（會讓整批摘要失敗），而且要講「不用重收文」——
    8/18 那天最容易誤判的就是以為公文要重收一次。"""
    _fake_run(monkeypatch, _R(
        returncode=1, stdout='{"is_error":true,"result":"Not logged in"}'))
    doctor.check_llm()
    out = capsys.readouterr().out
    assert doctor.BAD, "沒登入必須算一定要處理"
    assert "/login" in out
    assert "--summary-only" in out
    assert "不用重收" in out


def test_probe_is_skipped_when_claude_not_in_order(monkeypatch, capsys):
    """設定裡沒用 claude 那一棒就不該花時間（也不該花錢）叫它。"""
    import summarize_doc as sd
    monkeypatch.setattr(sd, "_get_backend_order", lambda: ["aistudio"])
    called = []
    monkeypatch.setattr(doctor, "claude_login_state",
                        lambda: called.append(1) or ("ok", ""))
    doctor.check_llm()
    assert called == []
    assert doctor.BAD == []
    assert "跳過" in capsys.readouterr().out
