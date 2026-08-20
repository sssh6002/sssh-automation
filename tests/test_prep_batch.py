# -*- coding: utf-8 -*-
"""prep_batch.py（收新公文拆兩段）的把關測試。

這一支存在的理由是坑 #19:原本邊下載邊叫 AI，edoc 在等 AI 的 1～2.5 分鐘裡
閒置到逾期，後面的公文就被靜靜跳過。所以這裡釘的是:

  1. 下載那一段，AI 真的被關掉了（不然拆了等於沒拆）
  2. 那個覆寫**一定會還原** —— 沒還原的話第二段補摘要會靜靜什麼都不產
  3. `--summary-only` 不准去關 Chrome（那一段根本不用瀏覽器）
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prep_batch as pb  # noqa: E402
import review_sheet as rs  # noqa: E402
import summarize_doc as sd  # noqa: E402


# ── 覆寫與還原 ─────────────────────────────────────────────────────────────

def test_download_only_swaps_and_restores():
    real = sd.summarize_extracted
    with pb.download_only():
        assert sd.summarize_extracted is pb._noop_summarize
    assert sd.summarize_extracted is real


def test_download_only_restores_even_on_error():
    """區塊裡爆掉也要還原。

    不還原的話，同一個行程裡第二段補摘要也會變成不做事 —— 而且是**靜靜地**
    什麼都沒產出，最後印「補摘要完成 0 份」，看起來像「本來就沒事要做」。
    """
    real = sd.summarize_extracted
    with pytest.raises(RuntimeError):
        with pb.download_only():
            raise RuntimeError("下載中斷")
    assert sd.summarize_extracted is real


def test_noop_summarize_really_does_nothing(tmp_path, monkeypatch):
    """假摘要不可以偷偷叫 LLM。"""
    def boom(*a, **k):
        raise AssertionError("第一段不應該叫 LLM")

    monkeypatch.setattr(sd, "summarize_doc", boom)
    assert pb._noop_summarize(str(tmp_path)) is False


# ── 第一段:下載時 AI 必須是關掉的 ─────────────────────────────────────────

class _Driver:
    window_handles = ["w1"]
    current_url = "https://edoc.gov.taipei/tcqb/home/default.jsp"

    def __init__(self):
        self.switch_to = self

    def window(self, h):
        pass


# 真正的等待邏輯 —— fixture 預設會把它換成「主畫面已就緒」，
# 要驗等待本身的測試再從這裡拿回原版。
_REAL_SETTLE = pb.settle_on_main_screen


@pytest.fixture
def stub_edoc(monkeypatch):
    """把登入／進系統／備料三步換成假的，一步都不會碰到真的 edoc。"""
    import click_document as cd
    import document_system as ds
    import ime_utils
    import taipeion_login_selenium as tls

    seen = {}
    drv = _Driver()
    # 預設當作主畫面已經就緒。假 driver 沒有真的 #leftSideBar，
    # 不擋掉的話每個測試都會空等 30 秒 timeout。
    monkeypatch.setattr(pb, "settle_on_main_screen", lambda d, timeout=30: True)

    def fake_prep(driver):
        # 關鍵:備料**正在跑的當下**，摘要必須已經被換成假的。
        seen["ai_off"] = sd.summarize_extracted is pb._noop_summarize
        seen["driver"] = driver
        return True

    monkeypatch.setattr(ime_utils, "ensure_english_ime", lambda: None)
    monkeypatch.setattr(tls, "login_taipeion_selenium", lambda **k: drv)
    monkeypatch.setattr(cd, "click_document_card", lambda d: True)
    monkeypatch.setattr(ds, "process_document_prep", fake_prep)
    return seen, drv


def test_ai_is_off_while_downloading(stub_edoc):
    """下載那一段跑的時候，AI 一定是關掉的 —— 這就是拆兩段的全部意義。"""
    seen, drv = stub_edoc
    real = sd.summarize_extracted
    driver, ok = pb.download_stage()
    assert (driver, ok) == (drv, True)
    assert seen["ai_off"] is True
    assert sd.summarize_extracted is real          # 跑完要還原


def test_login_failure_downloads_nothing(monkeypatch, stub_edoc):
    import taipeion_login_selenium as tls
    monkeypatch.setattr(tls, "login_taipeion_selenium", lambda **k: None)
    assert pb.download_stage() == (None, False)


def test_card_failure_stops_before_prep(monkeypatch, stub_edoc):
    """進不了公文系統就不要往下跑備料。"""
    import click_document as cd
    seen, drv = stub_edoc
    monkeypatch.setattr(cd, "click_document_card", lambda d: False)
    driver, ok = pb.download_stage()
    assert (driver, ok) == (drv, False)
    assert "driver" not in seen                    # 備料根本沒被呼叫


def test_waits_for_the_main_screen_instead_of_snapshotting(monkeypatch, stub_edoc):
    """主畫面慢幾秒才跳出來 —— 要等，不可以拿半路的快照當答案。

    2026-08-11 連踩五次:`auth3.jsp` 到主畫面中間會先經過 `index.jsp`，
    導航後 3 秒讀到中繼站就判定「停在登入頁」放棄 —— 但事後看 Chrome，
    分頁確實停在 `home/default.jsp`，主畫面根本就開起來了。
    """
    import post_draft_batch as pdb
    seen, drv = stub_edoc
    tries = {"n": 0}

    def slow_main(driver):
        tries["n"] += 1
        return tries["n"] >= 3          # 前兩次還在跳轉

    monkeypatch.setattr(pb, "settle_on_main_screen", _REAL_SETTLE)
    monkeypatch.setattr(pdb, "focus_main_window", slow_main)
    monkeypatch.setattr("time.sleep", lambda s: None)
    driver, ok = pb.download_stage()
    assert (driver, ok) == (drv, True)
    assert seen["driver"] is drv
    assert tries["n"] == 3


def test_gives_up_with_a_readable_reason(monkeypatch, stub_edoc, capsys):
    """真的等不到才放棄，而且要講人話、講清楚重跑是安全的。"""
    import post_draft_batch as pdb
    seen, drv = stub_edoc
    monkeypatch.setattr(pdb, "focus_main_window", lambda d: False)
    monkeypatch.setattr(pb, "settle_on_main_screen",
                        lambda d, timeout=30: False)
    driver, ok = pb.download_stage()
    assert (driver, ok) == (drv, False)
    assert "driver" not in seen                    # 備料根本沒被呼叫
    out = capsys.readouterr().out
    # 放棄時要指向真正的原因（edoc 第二關的憑證登入頁），
    # 而且要講明白程式**不會**替他重試 PIN —— 那是鎖卡風險。
    assert "PinCode" in out
    assert "鎖卡" in out
    assert "不會重複" in out


def test_timeout_tab_is_closed_and_focus_restored(monkeypatch):
    """逾期那個分頁要關掉，而且關完焦點要回到還活著的分頁。

    close() 之後目前的 handle 就失效了，沒切回去的話接下來每個操作都會炸。
    """
    class D:
        def __init__(self):
            self.window_handles = ["main", "timeout"]
            self.switch_to = self
            self.cur = "main"
            self.closed = []

        def window(self, h):
            self.cur = h

        @property
        def current_url(self):
            return ("https://edoc.gov.taipei/tcqb/home/sessionTimeout.jsp"
                    if self.cur == "timeout"
                    else "https://edoc.gov.taipei/tcqb/home/default.jsp")

        def close(self):
            self.closed.append(self.cur)
            self.window_handles = [h for h in self.window_handles if h != self.cur]

    d = D()
    assert pb.close_timeout_tabs(d) == 1
    assert d.closed == ["timeout"]
    assert d.cur == "main"                         # 焦點回到活著的分頁


# ── 清掉會被還原的舊分頁 ───────────────────────────────────────────────────

def test_clear_stale_session_removes_restorable_tabs(tmp_path, monkeypatch):
    """把「上次開了哪些分頁」清掉 —— 但不准碰 cookie 與網站設定。

    2026-08-11 回歸測試:Chrome 吃到的旗標是 `--restore-last-session=false`，
    而 Chrome 的 switch 只看有沒有這個旗標、不看值 → 實際效果是**強制還原**。
    於是前一天那個逾期的 edoc 分頁連同過期 session cookie 一起復活，
    新登入一進 edoc 就被踢回登入頁，連跑三次都收不到公文。
    """
    import taipeion_login_selenium as tls
    prof = tmp_path / "Default"
    (prof / "Sessions").mkdir(parents=True)
    (prof / "Sessions" / "Session_123").write_text("舊分頁", encoding="utf-8")
    (prof / "Sessions" / "Tabs_456").write_text("舊分頁", encoding="utf-8")
    (prof / "Current Session").write_text("舊", encoding="utf-8")
    (prof / "Last Tabs").write_text("舊", encoding="utf-8")
    # 這兩個**不可以**被動到
    (prof / "Cookies").write_text("不要碰我", encoding="utf-8")
    (prof / "Preferences").write_text("不要碰我", encoding="utf-8")

    monkeypatch.setattr(tls, "USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tls, "PROFILE_DIR", "Default")
    assert pb.clear_stale_session() == 4

    assert not (prof / "Sessions" / "Session_123").exists()
    assert not (prof / "Current Session").exists()
    assert (prof / "Cookies").read_text(encoding="utf-8") == "不要碰我"
    assert (prof / "Preferences").read_text(encoding="utf-8") == "不要碰我"


def test_only_session_cookies_are_cleared(tmp_path, monkeypatch):
    """只清 edoc 的工作階段 cookie —— 網站偏好（持久 cookie）不准動。

    2026-08-11:系管師說「把瀏覽器關掉就行」，差別在我們是 taskkill、
    他是正常關閉（正常關閉才會丟掉 is_persistent=0 的 cookie）。
    但不能連持久的一起清 —— edoc 那個「不需再顯示環境檢測」的勾選就是持久的，
    清掉會讓對話框每次都跳出來擋路。
    """
    import sqlite3
    import taipeion_login_selenium as tls
    prof = tmp_path / "Default" / "Network"
    prof.mkdir(parents=True)
    db = prof / "Cookies"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, is_persistent INT)")
    con.executemany("INSERT INTO cookies VALUES (?,?,?)", [
        ("edoc.gov.taipei", "JSESSIONID", 0),      # 要清
        ("edoc.gov.taipei", "envCheckHidden", 1),  # 網站偏好,不准動
        ("login.gov.taipei", "SSOSESSION", 0),     # 別的網域,不干我事
    ])
    con.commit()
    con.close()

    monkeypatch.setattr(tls, "USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tls, "PROFILE_DIR", "Default")
    assert pb.clear_edoc_session_cookies() == 1

    con = sqlite3.connect(db)
    left = sorted(r[0] for r in con.execute("SELECT name FROM cookies"))
    con.close()
    assert left == ["SSOSESSION", "envCheckHidden"]


def test_clear_cookies_survives_missing_file(tmp_path, monkeypatch):
    """profile 還沒有 Cookies 檔（第一次跑）也不能炸。"""
    import taipeion_login_selenium as tls
    monkeypatch.setattr(tls, "USER_DATA_DIR", str(tmp_path / "沒有這個"))
    monkeypatch.setattr(tls, "PROFILE_DIR", "Default")
    assert pb.clear_edoc_session_cookies() == 0


def test_clear_stale_session_survives_missing_profile(tmp_path, monkeypatch):
    """profile 還不存在（第一次跑）也不能炸。"""
    import taipeion_login_selenium as tls
    monkeypatch.setattr(tls, "USER_DATA_DIR", str(tmp_path / "沒有這個"))
    monkeypatch.setattr(tls, "PROFILE_DIR", "Default")
    assert pb.clear_stale_session() == 0


# ── edoc 雙因子:第二關的 PinCode ──────────────────────────────────────────

class _PinPage:
    """停在 edoc 憑證登入頁的假 driver。"""

    def __init__(self, with_button=True):
        self.window_handles = ["main"]
        self.switch_to = self
        self.current_url = "https://edoc.gov.taipei/tcqb/index.jsp"
        self.filled = []
        self.clicked = []
        self.with_button = with_button

    def window(self, h):
        pass

    def default_content(self):
        pass

    def find_elements(self, how, what):
        page = self

        class El:
            def __init__(self, kind):
                self.kind = kind

            def is_displayed(self):
                return True

            def click(self):
                page.clicked.append(self.kind)

            def clear(self):
                pass

            def send_keys(self, v):
                page.filled.append(v)

        if how == "css selector" and "pinCode" in what:
            return [El("pin")]
        if how == "xpath" and "登入" in what and page.with_button:
            return [El("登入")]
        return []


def test_pin_is_filled_once_for_the_second_factor(monkeypatch):
    """edoc 第二關要 PinCode 時要自動補填 —— 但**只填一次**。

    2026-08-11:edoc 自 114/6/17 起雙因子，TAIPEION 登入只是第一關。
    程式以為進了 edoc 就有主畫面，一直停在 index.jsp。
    """
    import taipeion_login_selenium as tls
    monkeypatch.setattr(tls, "_read_pin", lambda: "1234")
    d = _PinPage()
    assert pb.needs_pin_login(d) is True
    assert pb.edoc_pin_login(d) is True
    assert d.filled == ["1234"]                    # 剛好一次
    assert d.clicked[-1] == "登入"


def test_pin_never_retried_when_button_missing(monkeypatch):
    """按不到登入鈕就停手 —— PIN 留在框裡讓人自己按，絕不再試一次。

    自然人憑證連續輸錯 PIN 會鎖卡，代價遠大於少收一次文。
    """
    import taipeion_login_selenium as tls
    monkeypatch.setattr(tls, "_read_pin", lambda: "1234")
    d = _PinPage(with_button=False)
    assert pb.edoc_pin_login(d) is False
    assert d.filled == ["1234"]                    # 還是只填一次


def test_no_pin_configured_asks_the_human(monkeypatch, capsys):
    """env.env 沒有 pin= 就明講要人工，不要瞎猜一個去試。"""
    import taipeion_login_selenium as tls
    monkeypatch.setattr(tls, "_read_pin", lambda: None)
    d = _PinPage()
    assert pb.edoc_pin_login(d) is False
    assert d.filled == []
    assert "PinCode" in capsys.readouterr().out


def test_pin_selectors_are_narrow():
    """欄位選擇器必須夠窄 —— 填錯框再送出就是一次 PIN 錯誤。

    尤其**不可以**退回 `input[type=password]` 那種寬鬆條件。
    """
    assert all("pinCode" in s or "PinCode" in s or "PINCODE" in s
               for s in pb._PIN_SELECTORS)
    assert not any("type=password" in s for s in pb._PIN_SELECTORS)


def test_main_screen_skips_pin_entirely(monkeypatch, stub_edoc):
    """已經在主畫面時，完全不該碰 PIN。"""
    import taipeion_login_selenium as tls
    seen, drv = stub_edoc
    monkeypatch.setattr(tls, "_read_pin",
                        lambda: pytest.fail("主畫面就緒時不該讀 PIN"))
    monkeypatch.setattr(pb, "edoc_pin_login",
                        lambda d: pytest.fail("主畫面就緒時不該填 PIN"))
    driver, ok = pb.download_stage()
    assert (driver, ok) == (drv, True)


# ── 逾期偵測 ───────────────────────────────────────────────────────────────

def test_timed_out_uses_the_shared_judgement():
    """判定要跟 ui.chrome_state 同一支（post_draft_batch.looks_timed_out）。"""
    import post_draft_batch as pdb

    class D:
        window_handles = ["w1"]
        current_url = "https://edoc.gov.taipei/tcqb/home/sessionTimeout.jsp"

        def __init__(self):
            self.switch_to = self

        def window(self, h):
            pass

    assert pb.timed_out(D()) is True
    assert pb.timed_out(_Driver()) is False
    assert pdb.looks_timed_out([D.current_url]) is True


# ── 第二段:只補承辦中，不碰結案的 ─────────────────────────────────────────

def test_pending_summaries_skips_finished_docs(tmp_path, monkeypatch):
    """只補 document_download/ —— 結案那個工作區缺總結也不該花 token。"""
    work = tmp_path / "document_download"
    closure = tmp_path / "document_download_closure"
    for base in (work, closure):
        base.mkdir()
    (work / "MWAA0001").mkdir()                    # 缺總結 → 要補
    d2 = work / "MWAA0002"
    d2.mkdir()
    (d2 / "x總結.claude.md").write_text("有了", encoding="utf-8")
    (closure / "MWAA0003").mkdir()                 # 結案的，缺總結也不管

    monkeypatch.setattr(rs, "SCAN_DIRS", [str(work), str(closure)])
    monkeypatch.setattr(pb, "WORK_DIRS", [str(work)])
    got = [os.path.basename(d) for d in pb.pending_summaries()]
    assert got == ["MWAA0001"]


def test_summary_stage_is_offline(monkeypatch, tmp_path):
    """補摘要不可以碰 Chrome/edoc —— 那是它能在逾期後照跑的前提。"""
    work = tmp_path / "document_download"
    work.mkdir()
    (work / "MWAA0001").mkdir()
    monkeypatch.setattr(pb, "WORK_DIRS", [str(work)])
    monkeypatch.setattr(rs, "SCAN_DIRS", [str(work)])

    calls = []
    monkeypatch.setattr(rs, "prepare",
                        lambda dirs, only=None: (calls.append(only), (1, []))[1])

    import taipeion_login_selenium as tls
    monkeypatch.setattr(tls, "login_taipeion_selenium",
                        lambda **k: pytest.fail("補摘要不該登入 edoc"))
    assert pb.summary_stage() == (1, [])
    assert calls == [[str(work / "MWAA0001")]]


# ── 收新公文只補「這次收到的」，不碰歷史公文 ───────────────────────────────

def test_only_this_run_gets_summarised(tmp_path, monkeypatch):
    """按一次「收新公文」不可以默默對工作區裡的舊公文全部叫 LLM。

    2026-08-10 實測:`document_download/` 有 9 個 006xxx 的目錄缺總結（早期跑到
    一半留下的空殼，旁邊還有沒刪的 .zip）。第二段若直接補「所有缺總結的」，
    按一次鈕就是九次 token、十幾分鐘，而且多半是早就辦完的公文。
    """
    import time
    work = tmp_path / "document_download"
    work.mkdir()
    old = work / "MWAA0001"          # 歷史空殼
    old.mkdir()
    os.utime(old, (1000, 1000))      # 很久以前

    t0 = time.time()
    new = work / "MWAA0002"          # 這次收到的
    new.mkdir()

    monkeypatch.setattr(pb, "WORK_DIRS", [str(work)])
    monkeypatch.setattr(rs, "SCAN_DIRS", [str(work)])

    # 兩個都缺總結
    assert len(pb.pending_summaries()) == 2
    # 但只有這次動過的才該進第二段
    assert [os.path.basename(d) for d in pb.touched_since(t0)] == ["MWAA0002"]


def test_same_second_download_is_not_treated_as_history(tmp_path, monkeypatch):
    """mtime 只到秒的檔案系統上，同一秒收到的公文不可以被當成歷史公文。

    漏掉的代價是「公文靜靜沒有摘要」——正是坑 #19 那種要人工發現的錯。
    """
    import time
    work = tmp_path / "document_download"
    work.mkdir()
    d = work / "MWAA0002"
    d.mkdir()
    monkeypatch.setattr(pb, "WORK_DIRS", [str(work)])
    monkeypatch.setattr(rs, "SCAN_DIRS", [str(work)])

    now = time.time()
    os.utime(d, (int(now), int(now)))            # mtime 被截到整秒
    t0 = int(now) + 0.9                          # t0 落在同一秒的後段
    assert [os.path.basename(x) for x in pb.touched_since(t0)] == ["MWAA0002"]


def test_summary_only_still_covers_everything(tmp_path, monkeypatch):
    """`--summary-only` 是明確要求，那條要補全部 —— 包含歷史的。"""
    work = tmp_path / "document_download"
    work.mkdir()
    for n in ("MWAA0001", "MWAA0002"):
        (work / n).mkdir()
    os.utime(work / "MWAA0001", (1000, 1000))
    monkeypatch.setattr(pb, "WORK_DIRS", [str(work)])
    monkeypatch.setattr(rs, "SCAN_DIRS", [str(work)])

    calls = []
    monkeypatch.setattr(rs, "prepare",
                        lambda dirs, only=None: (calls.append(only), (2, []))[1])
    pb.summary_stage()               # targets=None → 全部
    assert sorted(os.path.basename(p) for p in calls[0]) == ["MWAA0001", "MWAA0002"]


# ── --summary-only 不准關 Chrome ───────────────────────────────────────────

def test_summary_only_does_not_kill_chrome(monkeypatch):
    """`--summary-only` 完全不用瀏覽器，不可以把承辦人正在用的 Chrome 收掉。"""
    import taipeion_login_selenium as tls

    def boom():
        raise AssertionError("--summary-only 不該關 Chrome")

    monkeypatch.setattr(tls, "_close_selenium_chrome_only", boom)
    monkeypatch.setattr(tls, "_setup_stdout_logging", boom)
    monkeypatch.setattr(pb, "summary_stage", lambda: (0, []))
    monkeypatch.setattr(pb, "sync_sheet", lambda: None)
    monkeypatch.setattr(sys, "argv", ["prep_batch.py", "--summary-only"])
    pb.main()                                       # 沒炸就是對的


def test_conflicting_flags_refused(monkeypatch):
    for argv in (["prep_batch.py", "--download-only", "--summary-only"],
                 ["prep_batch.py", "--attach", "--summary-only"]):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit):
            pb.main()


# ── --attach:用既有的、已經登入好的 Chrome ────────────────────────────────

def test_attach_never_kills_the_logged_in_chrome(monkeypatch, stub_edoc):
    """`--attach` 的重點就是那個 Chrome 已經登入好了，絕不能關掉它。

    同理也不該清 session 檔（那是為了下次重開乾淨，這條路根本不重開）。
    """
    import taipeion_login_selenium as tls
    seen, drv = stub_edoc

    def boom():
        raise AssertionError("--attach 不該關掉已登入的 Chrome")

    monkeypatch.setattr(tls, "_close_selenium_chrome_only", boom)
    monkeypatch.setattr(tls, "_setup_stdout_logging", lambda: None)
    monkeypatch.setattr(pb, "clear_stale_session",
                        lambda: pytest.fail("--attach 不該清 session"))
    monkeypatch.setattr(pb, "attach_driver", lambda: drv)
    monkeypatch.setattr(pb, "summary_stage", lambda t=None: (0, []))
    monkeypatch.setattr(pb, "sync_sheet", lambda: None)
    monkeypatch.setattr(sys, "argv", ["prep_batch.py", "--attach"])
    pb.main()
    assert seen["driver"] is drv          # 備料照樣跑到了


def test_attach_skips_login(monkeypatch, stub_edoc):
    """`--attach` 不可以再跑一次憑證登入。"""
    import taipeion_login_selenium as tls
    seen, drv = stub_edoc
    monkeypatch.setattr(tls, "login_taipeion_selenium",
                        lambda **k: pytest.fail("--attach 不該重新登入"))
    monkeypatch.setattr(pb, "attach_driver", lambda: drv)
    driver, ok = pb.download_stage(attach=True)
    assert (driver, ok) == (drv, True)


def test_attach_failure_is_explained(monkeypatch, stub_edoc):
    """接不上就明講，不要往下跑。"""
    monkeypatch.setattr(pb, "attach_driver", lambda: None)
    assert pb.download_stage(attach=True) == (None, False)


# ── 中文安裝路徑:改用剪貼簿貼路徑（2026-08-14）─────────────────────────────
#
# KdApp 的「匯出公文資料」對話框用模擬實體鍵盤填路徑，而 VkKeyScanW 只認得
# ASCII —— 中文字被跳過，於是
#     D:\D_資訊系統\自動辦文工具\document_download
#   → D:\D_\document_download
# 公文靜靜掉到別的資料夾，而對話框照樣關掉。

def test_ascii_path_still_uses_the_keyboard(monkeypatch):
    """純英數路徑要走原本那條已經驗過的鍵盤路 —— 行為一個字都不能變。"""
    import pending_doc_handler as pdh
    typed = []
    monkeypatch.setattr(pdh, "_send_text_vk",
                        lambda t, per_char_delay=0.02: typed.append(t))
    with pb.unicode_safe_dialog_path():
        pdh._send_text_vk(r"D:\sssh-automation\document_download")
    assert typed == [r"D:\sssh-automation\document_download"]


def test_chinese_path_goes_through_the_clipboard(monkeypatch):
    """中文路徑改成「複製到剪貼簿 + Ctrl+V」，不再逐字模擬鍵盤。"""
    import pending_doc_handler as pdh
    typed, copied, combos = [], [], []
    monkeypatch.setattr(pdh, "_send_text_vk",
                        lambda t, per_char_delay=0.02: typed.append(t))
    monkeypatch.setattr(pb, "_copy_to_clipboard",
                        lambda t: (copied.append(t), True)[1])
    monkeypatch.setattr(pdh, "_send_ctrl_combo", lambda vk: combos.append(vk))
    monkeypatch.setattr(pb.time, "sleep", lambda *a: None)
    path = r"D:\D_資訊系統\自動辦文工具\document_download"
    with pb.unicode_safe_dialog_path():
        pdh._send_text_vk(path)
    assert copied == [path]
    assert combos == [pb._VK_V]          # 真的按了 Ctrl+V
    assert typed == []                   # 沒有再逐字打


def test_clipboard_failure_types_nothing(monkeypatch):
    """剪貼簿放不進去 → **什麼都不打**。

    打半截路徑的下場是公文靜靜掉到別的資料夾（這次的症狀）;什麼都不打的話
    對話框不會關，原本那道「10s 內對話框沒關閉」會叫出來 —— 失敗要看得見。
    """
    import pending_doc_handler as pdh
    typed, combos = [], []
    monkeypatch.setattr(pdh, "_send_text_vk",
                        lambda t, per_char_delay=0.02: typed.append(t))
    monkeypatch.setattr(pb, "_copy_to_clipboard", lambda t: False)
    monkeypatch.setattr(pdh, "_send_ctrl_combo", lambda vk: combos.append(vk))
    with pb.unicode_safe_dialog_path():
        pdh._send_text_vk(r"D:\中文\document_download")
    assert typed == [] and combos == []


def test_dialog_path_override_is_restored():
    import pending_doc_handler as pdh
    real = pdh._send_text_vk
    with pytest.raises(RuntimeError):
        with pb.unicode_safe_dialog_path():
            raise RuntimeError("中斷")
    assert pdh._send_text_vk is real
