# -*- coding: utf-8 -*-
"""ui.py 存查頁的把關測試。

這一頁會觸發歸檔，而歸檔**無 admin 介入無法復原**（比送陳核更難救）。
這裡釘的是:
  1. 清單怎麼來的:一定要先讀過 edoc 的待結案清單，沒讀過不給按
  2. 畫面看到的（含順序）要跟後端算的完全一樣，不一樣就不動
  3. archive_batch 說這批不能跑，介面就不能放行 —— 判定只有一套
  4. 真的放行時，帶下去的 --expect 要是**整份清單、照順序**
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

import archive_batch as ab  # noqa: E402
import prep_hud  # noqa: E402
import ui  # noqa: E402


def _plan(*items, 可跑=True, 原因=None):
    return {"清單": list(items),
            "會歸檔": [it["文號"] for it in items if it["狀態"] == "go"],
            "可跑": 可跑, "不可跑原因": 原因, "警告": []}


def _go(no):
    return {"文號": no, "主旨": "測試", "分類": "研習", "檔號": "03750401",
            "來源": "結案目錄（歸檔會用這份）", "已存查標記": False, "狀態": "go"}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """每個測試都從「沒掃過、Chrome 通、不會真的跑」開始。"""
    monkeypatch.setattr(ui, "chrome_state", lambda: {"ok": True, "說明": None})
    monkeypatch.setattr(ui.SCAN, "lines", [])
    monkeypatch.setattr(ui.ARCH, "argv", ["archive_batch.py", "--go"])
    calls = []
    monkeypatch.setattr(ui.ARCH, "start",
                        lambda: (calls.append(list(ui.ARCH.argv)), (True, None))[1])
    return calls


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


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.load(r)


# ── 計畫是從那支印出來的 JSON 撈的 ─────────────────────────────────────────

def test_scan_plan_picks_the_marked_line(monkeypatch):
    """撈記號那一行，不是「最後一行」—— selenium 隨時會在後面再吐東西。"""
    p = _plan(_go("MWAA0001"))
    monkeypatch.setattr(ui.SCAN, "lines", [
        "[archive_batch] 待結案清單:1 筆",
        f"{ab.PLAN_MARK} {json.dumps(p, ensure_ascii=False)}",
        "DevTools listening on ws://127.0.0.1:9222/devtools/browser/…",
        "── 結束（代碼 0）──",
    ])
    assert ui.scan_plan()["會歸檔"] == ["MWAA0001"]


def test_scan_plan_none_when_never_run():
    assert ui.scan_plan() is None
    assert ui.archive_payload()["掃過"] is False


def test_payload_reports_scanned(monkeypatch):
    monkeypatch.setattr(ui, "scan_plan", lambda: _plan(_go("MWAA0001")))
    d = ui.archive_payload()
    assert d["掃過"] is True and d["計畫"]["會歸檔"] == ["MWAA0001"]


# ── 送出入口的關卡 ─────────────────────────────────────────────────────────

def test_start_needs_confirmation(srv, clean, monkeypatch):
    monkeypatch.setattr(ui, "scan_plan", lambda: _plan(_go("MWAA0001")))
    r = _post(srv, "/api/archive/start", {"文號": ["MWAA0001"]})
    assert r["ok"] is False and "確認" in r["錯誤"]
    assert clean == []


def test_start_refuses_when_never_scanned(srv, clean):
    """沒讀過待結案清單就按 = 沒人看過任何東西。不給。"""
    r = _post(srv, "/api/archive/start", {"確認": True, "文號": ["MWAA0001"]})
    assert r["ok"] is False and "讀待結案清單" in r["錯誤"]
    assert clean == []


def test_start_refuses_when_chrome_down(srv, clean, monkeypatch):
    monkeypatch.setattr(ui, "scan_plan", lambda: _plan(_go("MWAA0001")))
    monkeypatch.setattr(ui, "chrome_state",
                        lambda: {"ok": False, "說明": "自動化用的 Chrome 沒有開著。"})
    r = _post(srv, "/api/archive/start", {"確認": True, "文號": ["MWAA0001"]})
    assert r["ok"] is False and "Chrome" in r["錯誤"]
    assert clean == []


def test_start_refuses_when_list_changed(srv, clean, monkeypatch):
    monkeypatch.setattr(ui, "scan_plan",
                        lambda: _plan(_go("MWAA0001"), _go("MWAA0002")))
    r = _post(srv, "/api/archive/start", {"確認": True, "文號": ["MWAA0001"]})
    assert r["ok"] is False and "清單變了" in r["錯誤"]
    assert clean == []


def test_start_refuses_when_order_changed(srv, clean, monkeypatch):
    """順序不同也算變了 —— 歸檔只做第一筆，順序決定會做到哪裡。"""
    monkeypatch.setattr(ui, "scan_plan",
                        lambda: _plan(_go("MWAA0001"), _go("MWAA0002")))
    r = _post(srv, "/api/archive/start",
              {"確認": True, "文號": ["MWAA0002", "MWAA0001"]})
    assert r["ok"] is False and "清單變了" in r["錯誤"]
    assert clean == []


def test_start_refuses_when_plan_says_no(srv, clean, monkeypatch):
    """判定只有一套:archive_batch 說不能跑，介面就不能放行。"""
    it = dict(_go("MWAA0001"), 狀態="danger", 已存查標記=True,
              擋下原因="磁碟上已經有存查完成標記，卻還在待結案清單裡")
    monkeypatch.setattr(ui, "scan_plan",
                        lambda: _plan(it, 可跑=False, 原因="整批不跑"))
    r = _post(srv, "/api/archive/start", {"確認": True, "文號": ["MWAA0001"]})
    assert r["ok"] is False and r["錯誤"] == "整批不跑"
    assert clean == []


def test_start_refuses_empty(srv, clean):
    r = _post(srv, "/api/archive/start", {"確認": True, "文號": []})
    assert r["ok"] is False
    assert clean == []


def test_start_passes_whole_list_in_order_as_expect(srv, clean, monkeypatch):
    """放行時帶下去的 --expect 要是**整份清單、照順序**，不是只有會歸檔的那幾筆。

    真正動手那支會自己再讀一次 edoc 清單跟這串比 —— 只帶會歸檔的那幾筆，
    等於放過「中間又插進一筆」這種會改變順序的變化。
    """
    stop = dict(_go("MWAA0002"), 狀態="stop", 分類="待確認", 檔號="",
                擋下原因="沒有 8 位檔號")
    monkeypatch.setattr(ui, "scan_plan", lambda: _plan(_go("MWAA0001"), stop))
    r = _post(srv, "/api/archive/start",
              {"確認": True, "文號": ["MWAA0001", "MWAA0002"]})
    assert r["ok"] is True and r["筆數"] == 1        # 只有第一筆會歸檔
    assert clean == [["archive_batch.py", "--go", "--expect=MWAA0001,MWAA0002"]]


def test_start_invalidates_cached_scan(srv, clean, monkeypatch):
    """跑完清單一定不一樣了。舊的留著會讓人拿過期清單再按一次。"""
    ui.SCAN.lines = [f"{ab.PLAN_MARK} "
                     f"{json.dumps(_plan(_go('MWAA0001')), ensure_ascii=False)}"]
    r = _post(srv, "/api/archive/start", {"確認": True, "文號": ["MWAA0001"]})
    assert r["ok"] is True
    assert ui.scan_plan() is None


# ── 讀清單那顆也要擋 Chrome ────────────────────────────────────────────────

def test_scan_blocked_when_chrome_down(srv, monkeypatch):
    """讀清單不歸檔，但會點左側選單 —— 一樣在操作 Chrome。"""
    started = []
    monkeypatch.setattr(ui.SCAN, "start", lambda: (started.append(1), (True, None))[1])
    monkeypatch.setattr(ui, "chrome_state",
                        lambda: {"ok": False, "說明": "自動化用的 Chrome 沒有開著。"})
    r = _post(srv, "/api/scan/start", {})
    assert r["ok"] is False and "Chrome" in r["錯誤"]
    assert started == []


# ── 殘留的公文閱覽器分頁 ───────────────────────────────────────────────────

def _tabs(monkeypatch, *urls):
    import urllib.request as ur

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps([{"type": "page", "url": u} for u in urls]).encode()

    monkeypatch.setattr(ur, "urlopen", lambda *a, **k: _Resp())


HOME = "https://edoc.gov.taipei/tcqb/home/default.jsp?inLine=Y"
CHECK = ("https://edoc.gov.taipei/tcqb/oa/index.html"
         "?app=check&doSno=1156007710&doDeptno=MWAA&dialogType=3")
EDITOR = "https://edoc.gov.taipei/tcqb/oa/index.html?app=editor&doSno=1156007696"
GENPAGES = "https://edoc.gov.taipei/tcqb/oa/index.html?app=genpages&package=Y"


@pytest.mark.parametrize("viewer", [CHECK, EDITOR, GENPAGES])
def test_chrome_state_catches_every_kind_of_viewer_tab(monkeypatch, viewer):
    """三種閱覽器分頁都要擋。

    2026-08-10 存查第一次實跑就栽在只認 `app=editor`:8/6 送陳核留下的
    `app=check` 分頁照樣過關，document_closure 點了 7696 卻切到 7710 那個舊分頁，
    在**別份公文**上判「如擬」、按下載。存查的閱覽器是 check，不是 editor。
    """
    monkeypatch.undo()          # 這幾個測試要驗真的 chrome_state
    _tabs(monkeypatch, HOME, viewer)
    s = ui.chrome_state()
    assert s["ok"] is False
    assert "公文閱覽器" in s["說明"]


def test_chrome_state_passes_with_only_main_window(monkeypatch):
    monkeypatch.undo()
    _tabs(monkeypatch, HOME)
    assert ui.chrome_state()["ok"] is True


# ── 四支工作共用同一組進度／停止路由 ───────────────────────────────────────

def test_status_route_covers_every_job(srv):
    for job in ("prep", "send", "scan", "archive"):
        assert _get(srv, f"/api/{job}/status?since=0")["ok"] is True
    # 沒有這支工作就是 404，不要憑空生一個空面板出來
    with pytest.raises(urllib.error.HTTPError):
        _get(srv, "/api/nosuchjob/status?since=0")


def test_stop_route_covers_every_job(srv):
    for job in ("prep", "send", "scan", "archive"):
        r = _post(srv, f"/api/{job}/stop", {})
        assert r["ok"] is False and "沒有正在執行" in r["錯誤"]


def test_jobs_share_one_lock():
    """讀清單／歸檔跟收文、送陳核搶同一個 Chrome，四支互斥。"""
    assert {ui.PREP, ui.SEND, ui.SCAN, ui.ARCH} == set(ui.JOBS.values())


# ── 浮窗 ───────────────────────────────────────────────────────────────────

def test_hud_knows_archive_job():
    assert prep_hud._parse_args(["--job=archive"])[3] == "archive"
    assert "可以動滑鼠" in prep_hud.DONE_LABEL["archive"]
