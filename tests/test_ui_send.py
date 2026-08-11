# -*- coding: utf-8 -*-
"""ui.py 陳核頁的把關測試。

這一頁是介面上**唯一會真的送出公文**的地方，而送陳核收不回來。所以這裡釘的是:
  1. 清單分堆正確（尤其「已經辦完的不可以再出現」）
  2. 送出入口的兩道關卡:沒帶確認不動、畫面看到的跟現算的不一樣就不送
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

import post_draft_batch as pdb  # noqa: E402
import prep_hud  # noqa: E402
import review_sheet as rs  # noqa: E402
import ui  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")

DEFAULT = "一、於學校公告欄公佈周知。二、文存備查。"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """假工作區＋假審核表。絕不碰桌面那份真的「公告彙整.xlsx」。"""
    work = tmp_path / "document_download"
    work.mkdir()
    sheet = str(tmp_path / "表.xlsx")
    monkeypatch.setattr(rs, "SCAN_DIRS", [str(work)])
    monkeypatch.setattr(rs, "sheet_path", lambda: sheet)
    monkeypatch.setattr(rs, "sync", lambda *a, **k: (0, 0, 0, 0))
    # Chrome 連線檢查預設當通過 —— 大多數測試要驗的不是那件事，
    # 而且真的去連 :9222 每次要等 timeout。要驗它的測試自己覆蓋回去。
    monkeypatch.setattr(ui, "chrome_state", lambda: {"ok": True, "說明": None})
    return work, sheet


def _doc(work, no, marks=()):
    d = work / no
    d.mkdir()
    for m in marks:
        (d / f"{no}{m}").write_text("x", encoding="utf-8")
    return d


# ── 清單分堆 ───────────────────────────────────────────────────────────────

def test_buckets(env):
    work, sheet = env
    for no in ("MWAA0001", "MWAA0002", "MWAA0003"):
        _doc(work, no)
    rs.upsert([
        {"文號": "MWAA0001", "擬辦": DEFAULT, "陳會": "OK"},      # 可送
        {"文號": "MWAA0002", "陳會": "OK"},                       # 勾了但擬辦空白
        {"文號": "MWAA0003", "擬辦": DEFAULT, "陳會": ""},        # 還沒勾
    ], path=sheet)
    data, _ = ui.send_payload()
    assert [i["文號"] for i in data["可送"]] == ["MWAA0001"]
    assert [i["文號"] for i in data["擋下"]] == ["MWAA0002"]
    assert [i["文號"] for i in data["候選"]] == ["MWAA0003"]
    assert data["可送"][0]["送出文字"] == DEFAULT
    # 還沒勾的也要先算好能不能送 —— 免得勾完才發現送不了。
    assert data["候選"][0].get("擋下原因") is None


def test_finished_docs_never_listed(env):
    """辦完的公文不可以出現在陳核頁 —— 不管「陳會」欄留著什麼。

    兩種辦完:磁碟有 *已存查.txt（程式辦的）、兩關都標「已辦」（自己宣告的）。
    """
    work, sheet = env
    _doc(work, "MWAA0001", marks=("已存查.txt",))
    _doc(work, "MWAA0002")
    rs.upsert([{"文號": "MWAA0001", "擬辦": DEFAULT, "陳會": "OK", "張貼": "OK"},
               {"文號": "MWAA0002", "擬辦": DEFAULT, "陳會": "已辦", "張貼": "已辦"}],
              path=sheet)
    data, _ = ui.send_payload()
    assert (data["可送"], data["擋下"], data["候選"]) == ([], [], [])


def test_already_sent_docs_are_not_listed(env):
    """已有 *已陳核.txt 的公文不可以再出現在陳核頁 —— 那一關做完了。

    「陳會」欄這時可能還停在 OK（upsert 只填空白格，做完不會被改寫，
    交接檔坑 #1）。2026-08-06 承辦人畫面上就卡了 5 筆這種，理由全是
    「先前送過」，還得一筆一筆取消勾選。以磁碟痕跡為準直接跳過。
    """
    work, sheet = env
    _doc(work, "MWAA0001", marks=("已陳核.txt",))
    _doc(work, "MWAA0002")
    rs.upsert([{"文號": "MWAA0001", "擬辦": DEFAULT, "陳會": "OK"},
               {"文號": "MWAA0002", "擬辦": DEFAULT, "陳會": "OK"}], path=sheet)
    data, _ = ui.send_payload()
    assert [i["文號"] for i in data["可送"]] == ["MWAA0002"]
    assert data["擋下"] == []
    assert data["已送過"] == ["MWAA0001"]


def test_page_and_cli_agree(env):
    """畫面說會送的，要跟 CLI（post_draft_batch.plan）算的完全一樣。

    這兩份清單一旦分岔，最壞的方向是「畫面說不送、實際送出去了」。
    """
    work, sheet = env
    _doc(work, "MWAA0001")
    _doc(work, "MWAA0002", marks=("已存查.txt",))
    rs.upsert([{"文號": "MWAA0001", "擬辦": DEFAULT, "陳會": "OK"},
               {"文號": "MWAA0002", "擬辦": DEFAULT, "陳會": "OK", "張貼": "OK"}],
              path=sheet)
    data, _ = ui.send_payload()
    ready, _ = pdb.plan(sheet)
    assert [i["文號"] for i in data["可送"]] == [i["文號"] for i in ready]


# ── 送出入口的關卡 ─────────────────────────────────────────────────────────

@pytest.fixture
def srv(env, monkeypatch):
    """把 ui.Handler 起在隨機 port。SEND.start 換成假的 —— 測試絕不真跑 edoc。"""
    calls = []
    monkeypatch.setattr(ui.SEND, "start", lambda: (calls.append(1), (True, None))[1])
    s = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}", calls
    s.shutdown()


def _post(base, path, obj):
    req = urllib.request.Request(base + path, method="POST",
                                data=json.dumps(obj).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def _one_ready(work, sheet):
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "擬辦": DEFAULT, "陳會": "OK"}, path=sheet)


def test_chrome_state_reports_not_running(monkeypatch):
    """連不上 :9222 = 那個 Chrome 沒開。訊息要講中文的下一步，不是英文堆疊。"""
    import urllib.request as ur

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(ur, "urlopen", boom)
    s = ui.chrome_state()
    assert s["ok"] is False
    assert "收新公文" in s["說明"]


def test_chrome_state_detects_logout(monkeypatch):
    """只剩登入頁時要說「已經登出」，不是含糊的「找不到主畫面」。

    2026-08-06:閒置一陣子後 Chrome 只剩 index.jsp?logout=Y 一個分頁。
    """
    import urllib.request as ur

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps([
                {"type": "page",
                 "url": "https://edoc.gov.taipei/tcqb/index.jsp?logout=Y"}]
            ).encode()

    monkeypatch.setattr(ur, "urlopen", lambda *a, **k: _Resp())
    s = ui.chrome_state()
    assert s["ok"] is False
    assert "登出" in s["說明"]


# ── 「操作時間逾期」的警告視窗（2026-08-10）─────────────────────────────────

HOME_URL = "https://edoc.gov.taipei/tcqb/home/default.jsp?inLine=Y"
TIMEOUT_URL = "https://edoc.gov.taipei/tcqb/home/sessionTimeout.jsp"


def _fake_tabs(monkeypatch, urls):
    """假造 DevTools 回的分頁清單。"""
    import urllib.request as ur

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                [{"type": "page", "url": u} for u in urls]).encode()

    monkeypatch.setattr(ur, "urlopen", lambda *a, **k: _Resp())


def test_chrome_state_detects_session_timeout(monkeypatch):
    """逾期警告視窗跳出來就要擋 —— **就算主畫面看起來還在**。

    2026-08-10 回歸測試。那個視窗的網址是 /tcqb/home/sessionTimeout.jsp，
    **含 /tcqb/home/**;原本這裡用 any("/tcqb/home/" in u) 認主畫面，被它冒充
    過去 → chrome_state 對一個已經被踢出去的 Chrome 回 ok=True。
    陳核頁與存查頁共用這盞燈，等於兩頁都亮綠燈讓人按下送出。
    實測承辦人當時的 Chrome:主視窗已被踢回 index.jsp，警告視窗還開著。
    """
    _fake_tabs(monkeypatch, [TIMEOUT_URL, HOME_URL])
    s = ui.chrome_state()
    assert s["ok"] is False
    assert "逾期" in s["說明"]
    assert "關掉" in s["說明"]          # 下一步要講:那個小視窗得先關掉


def test_timeout_page_is_not_a_home_page():
    """sessionTimeout.jsp 住在 /tcqb/home/ 底下，但它不是主畫面。

    「共用同一個特徵字串前，先確認另一條路的長相真的一樣」—— 坑 #17。
    """
    assert pdb.has_home([HOME_URL]) is True
    assert pdb.has_home([TIMEOUT_URL]) is False
    assert pdb.looks_timed_out([TIMEOUT_URL]) is True
    assert pdb.looks_timed_out([HOME_URL]) is False
    # 逾期也算「要重新登入」—— 存查那條路是靠 looks_logged_out 給訊息的。
    assert pdb.looks_logged_out([TIMEOUT_URL]) is True


def test_start_blocked_when_chrome_down(env, srv, monkeypatch):
    """Chrome 沒開就不要起 subprocess —— 跑下去只會吐英文堆疊，
    而且 fill_in_draft 會建議「跑 main.py 3」（結案存查，會貼校網）。"""
    work, sheet = env
    _one_ready(work, sheet)
    base, calls = srv
    monkeypatch.setattr(ui, "chrome_state",
                        lambda: {"ok": False, "說明": "自動化用的 Chrome 沒有開著。"})
    r = _post(base, "/api/send/start", {"確認": True, "文號": ["MWAA0001"]})
    assert r["ok"] is False and "Chrome" in r["錯誤"]
    assert calls == []


def test_start_needs_confirmation(env, srv):
    work, sheet = env
    _one_ready(work, sheet)
    base, calls = srv
    r = _post(base, "/api/send/start", {"文號": ["MWAA0001"]})
    assert r["ok"] is False and "確認" in r["錯誤"]
    assert calls == []                      # 什麼都沒跑


def test_start_refuses_when_list_changed(env, srv):
    """畫面看到的跟現在算出來的不一樣 → 不送。

    中間有人動了 Excel、或又收了幾份公文，清單就變了；那不是承辦人按確定時
    看到的東西。
    """
    work, sheet = env
    _one_ready(work, sheet)
    _doc(work, "MWAA0002")
    rs.upsert({"文號": "MWAA0002", "擬辦": DEFAULT, "陳會": "OK"}, path=sheet)
    base, calls = srv
    r = _post(base, "/api/send/start", {"確認": True, "文號": ["MWAA0001"]})
    assert r["ok"] is False and "清單變了" in r["錯誤"]
    assert calls == []


def test_start_refuses_empty(env, srv):
    base, calls = srv
    r = _post(base, "/api/send/start", {"確認": True, "文號": []})
    assert r["ok"] is False
    assert calls == []


def test_start_runs_when_list_matches(env, srv):
    work, sheet = env
    _one_ready(work, sheet)
    base, calls = srv
    r = _post(base, "/api/send/start", {"確認": True, "文號": ["MWAA0001"]})
    assert r["ok"] is True and r["筆數"] == 1
    assert calls == [1]


# ── 兩件事不可以同時跑（會搶同一個 Chrome）───────────────────────────────

def test_jobs_are_mutually_exclusive(monkeypatch):
    a = ui.Job("甲", ["--x"])
    b = ui.Job("乙", ["--y"])

    class FakeProc:
        def poll(self):
            return None                     # 一直在跑

    monkeypatch.setattr(ui.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(ui.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda s: None})())
    monkeypatch.setattr(ui.Job, "_busy", None)
    assert a.start() == (True, None)
    ok, err = b.start()
    assert ok is False and "甲" in err
    ok, err = a.start()
    assert ok is False and err == "已經在跑了"


# ── 進度浮窗的參數 ─────────────────────────────────────────────────────────

def test_hud_args():
    assert prep_hud._parse_args([]) == (prep_hud.DEFAULT_BASE, 0, False, "prep", None)
    base, since, demo, job, label = prep_hud._parse_args(
        ["http://127.0.0.1:8760/", "12", "--job=send", "--label=送陳核中"])
    assert (base, since, job, label) == ("http://127.0.0.1:8760", 12, "send", "送陳核中")
    assert demo is False
    # 不認識的 job 當收文處理,不要因為打錯字就開不起來
    assert prep_hud._parse_args(["--job=亂打"])[3] == "prep"
