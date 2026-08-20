# -*- coding: utf-8 -*-
"""設定頁與 `env_config` 的把關測試。

這一頁不送出任何東西，但它碰的是**密鑰**。所以這裡釘的第一件事不是功能，是:

  **密鑰的值永遠不會出現在 API 回應裡。**

承辦人 2026-08-12 的原話:「KEY 不是應該個人填個人的」—— 這套工具會交給下一個人，
密鑰跟著人不跟著工具。而這個 repo 是 public、畫面會被截圖、log 會被貼進交接檔。

其餘釘的:
  · 寫回去要**逐行保留原檔**（註解、順序、不認識的鍵都不能被吃掉）
  · 只收設定頁自己那張表裡的鍵
  · PIN 只收數字（打錯會鎖卡，要跑戶政事務所）
"""

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import env_config as ec  # noqa: E402
import ui  # noqa: E402

SECRET_PIN = "135790"
SECRET_PW = "s3cr3t-pa55w0rd"
SAMPLE = """\
# 帳密設定:# 開頭整行忽略
#pin是自然人憑證pin code
pin={pin}
sssh_account=abc123
sssh_password={pw}
sssh_publisher=資媒組管理員
sssh_publish_unit=資訊媒體組

# ===== 公文總結 LLM backend =====
summarize_llm_order=antigravity,claude
google_ai_studio_api_key=
anthropic_api_key=
# 這個鍵設定頁沒列到,不可以被吃掉
some_other_key=保留我
""".format(pin=SECRET_PIN, pw=SECRET_PW)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """假 env.env。絕不碰專案裡那份真的。"""
    p = tmp_path / "env.env"
    p.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(ec, "ENV_FILE", str(p))
    monkeypatch.setattr(ec, "EXAMPLE_FILE", str(tmp_path / "沒有這個檔"))
    return p


# ── 密鑰不出畫面（這一頁最重要的一條）───────────────────────────────────────

def test_state_never_leaks_secret_values(env):
    s = ec.state()
    blob = json.dumps(s, ensure_ascii=False)
    assert SECRET_PIN not in blob
    assert SECRET_PW not in blob
    # 但要講得出「有沒有填」，不然使用者不知道自己填過沒有。
    by = {f["鍵"]: f for f in s["欄位"]}
    assert by["pin"]["已填"] is True
    assert by["anthropic_api_key"]["已填"] is False
    assert "值" not in by["pin"]                    # 連欄位都不該有
    # 共用的照樣要看得到值 —— 那些是換人接手照用的東西。
    assert by["sssh_publish_unit"]["值"] == "資訊媒體組"


def test_api_never_leaks_secret_values(env, srv):
    base = srv
    r = json.loads(urllib.request.urlopen(base + "/api/settings/plan",
                                          timeout=10).read().decode())
    blob = json.dumps(r, ensure_ascii=False)
    assert r["ok"] is True
    assert SECRET_PIN not in blob and SECRET_PW not in blob


def test_save_response_only_names_keys(env, srv):
    """存檔的回應只回鍵名，不回值。"""
    base = srv
    r = _post(base, "/api/settings/save", {"值": {"sssh_password": "新密碼xyz"}})
    assert r["ok"] is True and r["改了"] == ["sssh_password"]
    assert "新密碼xyz" not in json.dumps(r, ensure_ascii=False)
    assert ec.read_values()["sssh_password"] == "新密碼xyz"


# ── 寫回去不能吃掉原檔 ─────────────────────────────────────────────────────

def test_write_preserves_comments_and_unknown_keys(env):
    ec.write({"sssh_publish_unit": "圖書館"})
    text = env.read_text(encoding="utf-8")
    assert "#pin是自然人憑證pin code" in text          # 註解留著
    assert "some_other_key=保留我" in text             # 不認識的鍵留著
    assert "sssh_publish_unit=圖書館" in text
    assert text.count("sssh_publish_unit=") == 1       # 沒有變成兩行
    # 其他值一個都沒動
    v = ec.read_values()
    assert v["pin"] == SECRET_PIN and v["sssh_account"] == "abc123"


def test_write_reports_only_real_changes(env):
    assert ec.write({"sssh_publisher": "資媒組管理員"}) == []   # 一樣就不算改
    assert ec.write({"sssh_publisher": "換人了"}) == ["sssh_publisher"]


def test_write_appends_missing_key_with_its_note(env):
    """原檔沒有那一行 → 補在最後，而且**帶著說明一起補**。"""
    ec.write({"summarize_claude_model": "sonnet"})
    text = env.read_text(encoding="utf-8")
    assert "summarize_claude_model=sonnet" in text
    assert "claude 用哪個模型" in text                 # 說明也補上了
    assert ec.read_values()["summarize_claude_model"] == "sonnet"


# ── 只收自己那張表裡的鍵 ───────────────────────────────────────────────────

def test_unknown_key_is_refused(env):
    with pytest.raises(ec.Rejected):
        ec.write({"隨便亂打": "x"})
    assert "隨便亂打" not in env.read_text(encoding="utf-8")


def test_api_refuses_unknown_key(env, srv):
    base = srv
    r = _post(base, "/api/settings/save", {"值": {"隨便亂打": "x"}})
    assert r["ok"] is False


def test_api_refuses_empty_body(env, srv):
    base = srv
    assert _post(base, "/api/settings/save", {"值": {}})["ok"] is False


# ── PIN 打錯會鎖卡，所以嚴一點 ─────────────────────────────────────────────

def test_pin_must_be_digits(env):
    """自然人憑證連續輸錯會鎖卡（要跑戶政事務所解卡）—— 寧可在這裡就擋。"""
    with pytest.raises(ec.Rejected):
        ec.write({"pin": "abc123"})
    assert ec.read_values()["pin"] == SECRET_PIN       # 原本那個沒被動到


def test_value_cannot_contain_newline(env):
    """換行會把一行變兩行，等於偷偷塞一個鍵進去。"""
    with pytest.raises(ec.Rejected):
        ec.write({"sssh_publisher": "甲\nsssh_publish_unit=別的單位"})


# ── 提醒（講一句，但不阻止）─────────────────────────────────────────────────

def test_warns_about_antigravity(env):
    """那個帳號沒資格，每份公文白等 10～13 秒才換棒。講一句，但不擋。"""
    ws = ec.warnings()
    assert any("antigravity" in w for w in ws)
    assert ec.write({"summarize_llm_order": "antigravity,claude"}) == []  # 沒擋


def test_placeholder_values_are_called_out(tmp_path, monkeypatch):
    """從 env_example.env 複製過來的**範例值**要被認出來。

    2026-08-12 實跑發現:安裝流程會複製那份範例檔，而它是**有值的**
    （`pin=000000`）。只看「有沒有填」會回報已填，然後在收文插卡那一刻才爆 ——
    這個 fork 最常見的坑型:有人回報成功，但沒有人回頭確認那是真的。
    """
    ex = tmp_path / "env_example.env"
    ex.write_text("pin=000000\nsssh_account=abcdefg12345\n"
                  "sssh_publish_unit=系管師群組\n", encoding="utf-8")
    p = tmp_path / "env.env"
    p.write_text("pin=000000\nsssh_account=我自己的帳號\n"
                 "sssh_publish_unit=系管師群組\n", encoding="utf-8")
    monkeypatch.setattr(ec, "ENV_FILE", str(p))
    monkeypatch.setattr(ec, "EXAMPLE_FILE", str(ex))
    ph = ec.placeholders()
    assert "pin" in ph                          # 沒改過 → 認出來
    assert "sssh_publish_unit" in ph            # 沒改過 → 認出來
    assert "sssh_account" not in ph             # 改過了 → 不該被誤報
    assert any("範例值" in w for w in ec.warnings())


def test_warns_when_publish_unit_empty(env):
    ec.write({"sssh_publish_unit": ""})
    assert any("發布單位" in w for w in ec.warnings())


def test_order_must_have_one_known_backend(env):
    with pytest.raises(ec.Rejected):
        ec.write({"summarize_llm_order": "喵喵,汪汪"})


def test_known_backends_come_from_summarize_doc():
    """認得的棒次要跟 summarize_doc 同一份 —— 各抄一份就會有
    「設定頁說可以、程式其實不認」這種分岔。"""
    import summarize_doc as sd
    assert ec.known_backends() == list(sd.DEFAULT_LLM_ORDER)


# ── 規格檔入口只認白名單 ───────────────────────────────────────────────────

def test_spec_open_is_whitelisted(env, srv, monkeypatch):
    """不接受任意路徑 —— 不然這個位址就變成「用瀏覽器叫本機開任何檔案」。"""
    base = srv
    opened = []
    monkeypatch.setattr(ui.os, "startfile", lambda p: opened.append(p),
                        raising=False)
    assert _post(base, "/api/settings/open",
                 {"檔名": "../env.env"})["ok"] is False
    assert _post(base, "/api/settings/open",
                 {"檔名": "C:/Windows/System32/calc.exe"})["ok"] is False
    assert opened == []
    r = _post(base, "/api/settings/open", {"檔名": "summarize_doc.md"})
    assert r["ok"] is True and opened and opened[0].endswith("summarize_doc.md")


# ── 起伺服器 ───────────────────────────────────────────────────────────────

@pytest.fixture
def srv():
    s = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


def _post(base, path, obj):
    req = urllib.request.Request(base + path, method="POST",
                                data=json.dumps(obj).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def test_sheet_path_is_editable_from_the_page(env):
    """審核表的位置要能從設定頁改 —— 不然「想把東西放 D 槽」這件事做不到。

    2026-08-14 承辦人問「能不能選安裝槽」時發現的缺口:資料夾本身可以隨便放
    （解壓縮到哪就是哪），但審核表預設在桌面，而設定頁沒有那一格。
    """
    by = {f["鍵"]: f for f in ec.state()["欄位"]}
    assert "review_sheet_path" in by
    assert by["review_sheet_path"]["區"] == "共用"
    assert by["review_sheet_path"]["祕密"] is False      # 路徑不是密鑰,要看得到
    ec.write({"review_sheet_path": r"D:\公文\公告彙整.xlsx"})
    assert ec.read_values()["review_sheet_path"] == r"D:\公文\公告彙整.xlsx"


def test_sheet_path_actually_moves_the_sheet(env, monkeypatch, tmp_path):
    """設定頁寫進去的路徑，`review_sheet` 真的要吃 —— 兩邊對不上就是白改。

    這一條釘的是「設定頁改的東西真的生效」，不是「檔案寫對了」。
    只驗前者的話，設定頁可以很開心地寫進一個沒有人讀的鍵。
    """
    import review_sheet as rs
    import taipeion_login_selenium as tls
    # sheet_path() 是透過 tls._read_config 去讀 env.env 的,所以要讓它讀到假的那份
    monkeypatch.setattr(tls, "ENV_FILE", str(env))
    want = str(tmp_path / "別的地方.xlsx")
    ec.write({"review_sheet_path": want})
    assert rs.sheet_path() == os.path.abspath(want)


def test_sheet_path_blank_means_desktop(env, monkeypatch):
    """留空 = 回到桌面那份預設 —— 不是「沒有審核表」。"""
    import review_sheet as rs
    import taipeion_login_selenium as tls
    monkeypatch.setattr(tls, "ENV_FILE", str(env))
    ec.write({"review_sheet_path": ""})
    assert rs.sheet_path().endswith("公告彙整.xlsx")
    assert "Desktop" in rs.sheet_path()
