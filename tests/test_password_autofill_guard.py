"""密碼自動填入防護測試（2026-07-14 公告失敗根因修正）。

三層防護中可純函式測試的部分：
  1. _build_chrome_options 的 prefs 必須關閉密碼管理員
  2. _purge_saved_passwords 啟動前刪除 profile 內已存密碼資料庫
  3. _force_field_value 送出前驗證欄位最終值、被蓋掉時 JS 改回
登入頁實際 autofill 行為為 live Selenium，於實機驗證。
"""
import os

import taipeion_login_selenium as tls
from document_closure.document_closure_post_web import _force_field_value


# ── 1. prefs 關閉密碼管理員 ─────────────────────────────────────────────────

def test_chrome_options_disable_password_manager():
    prefs = tls._build_chrome_options().experimental_options["prefs"]
    assert prefs["credentials_enable_service"] is False
    assert prefs["credentials_enable_autosignin"] is False
    assert prefs["profile.password_manager_enabled"] is False
    assert prefs["profile.password_manager_leak_detection"] is False
    # 原有的剪貼簿允許設定不可被弄丟
    assert prefs["profile.default_content_setting_values.clipboard"] == 1


# ── 2. _purge_saved_passwords ───────────────────────────────────────────────

_LOGIN_DB_FILES = ("Login Data", "Login Data-journal",
                   "Login Data For Account", "Login Data For Account-journal")


def test_purge_deletes_login_databases(tmp_path, monkeypatch):
    profile = tmp_path / "User Data" / "Default"
    profile.mkdir(parents=True)
    for name in _LOGIN_DB_FILES:
        (profile / name).write_bytes(b"x")
    (profile / "Preferences").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(tls, "USER_DATA_DIR", str(tmp_path / "User Data"))
    monkeypatch.setattr(tls, "PROFILE_DIR", "Default")
    tls._purge_saved_passwords()

    for name in _LOGIN_DB_FILES:
        assert not (profile / name).exists(), f"{name} 應被刪除"
    assert (profile / "Preferences").exists(), "不相干檔案不可被刪"


def test_purge_tolerates_missing_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, "USER_DATA_DIR", str(tmp_path / "不存在" / "User Data"))
    monkeypatch.setattr(tls, "PROFILE_DIR", "Default")
    tls._purge_saved_passwords()  # 不應丟例外


# ── 3. _force_field_value ───────────────────────────────────────────────────

class _FakeFieldDriver:
    """模擬單一欄位的 driver：read JS 回目前值、write JS 設值。

    frozen=True 模擬「怎麼改都被 autofill 蓋回去」的極端情況（寫入無效）。
    """

    def __init__(self, initial, frozen=False):
        self.value = initial
        self.frozen = frozen
        self.writes = 0

    def execute_script(self, script, *args):
        if "return el ? el.value" in script:
            return self.value
        self.writes += 1
        if not self.frozen:
            self.value = args[1]


def test_force_field_value_already_correct():
    d = _FakeFieldDriver("acc123")
    assert _force_field_value(d, "login-user-name", "acc123", settle=0) is True
    assert d.writes == 0  # 值已正確就不動欄位


def test_force_field_value_overwritten_by_autofill_gets_fixed():
    d = _FakeFieldDriver("robot")  # 被 autofill 填成別組帳號
    assert _force_field_value(d, "login-user-name", "acc123", settle=0) is True
    assert d.value == "acc123"
    assert d.writes == 1


def test_force_field_value_gives_up_when_field_stuck():
    d = _FakeFieldDriver("robot", frozen=True)
    assert _force_field_value(d, "login-user-name", "acc123",
                              attempts=3, settle=0) is False
    assert d.writes == 3
