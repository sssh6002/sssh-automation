# -*- coding: utf-8 -*-
"""ui.py 張貼頁的把關測試。

這一頁是介面上**唯一真的對外**的地方 —— 貼出去全校師生家長都看得到
（可事後刪，但已經被看到就是被看到了）。所以這裡釘的是:

  1. 清單分堆正確，尤其**沒勾的永遠不會進「會貼」**（承辦人逐筆勾，跟陳核頁對稱）
  2. 畫面說會貼的，要跟 CLI（post_web_batch.plan）算的完全一樣
  3. 送出入口的四道關卡:沒帶確認不動、清單變了不貼、有警告不貼、
     **收文那個 Chrome 還開著不貼**
  4. 第 3 點最後那道跟陳核／存查**條件相反**，兩盞燈不可以共用
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

import post_web_batch as pwb  # noqa: E402
import prep_hud  # noqa: E402
import review_sheet as rs  # noqa: E402
import ui  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")

TEXT = "【研習】測試公告\n\n📝【公告內容】\n一、內容甲。"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """假工作區＋假審核表。絕不碰桌面那份真的「公告彙整.xlsx」。"""
    from document_closure import document_closure_post_web as pw
    work = tmp_path / "document_download"
    work.mkdir()
    sheet = str(tmp_path / "表.xlsx")
    monkeypatch.setattr(rs, "SCAN_DIRS", [str(work)])
    monkeypatch.setattr(rs, "sheet_path", lambda: sheet)
    monkeypatch.setattr(rs, "sync", lambda *a, **k: (0, 0, 0, 0))
    # 判定來源預設都「可以貼」，個別測試再覆蓋回去。
    monkeypatch.setattr(pw, "_parse_summary",
                        lambda d: {"handling": "於官網公告", "title": "主旨",
                                   "body": "1. 甲", "sync_categories": ["研習資訊"]})
    monkeypatch.setattr(pw, "_should_post", lambda s: True)
    monkeypatch.setattr(pw, "_already_announced", lambda d: False)
    import taipeion_login_selenium as tls
    monkeypatch.setattr(tls, "_read_config",
                        lambda k: "系管師群組" if k == "sssh_publish_unit" else None)
    # Chrome 檢查預設當「沒有擋路的 Chrome」。要驗它的測試自己覆蓋回去 ——
    # 真的去連 :9222 會拿到**這台機器當下的狀態**（承辦人的 edoc Chrome 開著
    # 就整批測試變色），測試不可以看那個。
    monkeypatch.setattr(pwb, "chrome_in_the_way", lambda: None)
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
        {"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"},   # 可貼
        {"文號": "MWAA0002", "張貼": "OK"},                 # 勾了但公告欄空的
        {"文號": "MWAA0003", "公告": TEXT, "張貼": ""},     # 還沒勾
    ], path=sheet)
    data, _ = ui.web_payload()
    assert [i["文號"] for i in data["可貼"]] == ["MWAA0001"]
    assert [i["文號"] for i in data["擋下"]] == ["MWAA0002"]
    assert [i["文號"] for i in data["候選"]] == ["MWAA0003"]
    # 畫面要核對的是「全校會讀到的那幾百字」,不是一行主旨 —— 內文一定要帶上來。
    assert data["可貼"][0]["標題"] == "【研習】測試公告"
    assert "📝【公告內容】" in data["可貼"][0]["內文"]
    assert data["可貼"][0]["分類"] == ["研習資訊"]
    assert "公告欄是空的" in data["擋下"][0]["擋下原因"]
    # 還沒勾的也要先算好能不能貼 —— 免得勾完才發現貼不了。
    assert data["候選"][0].get("擋下原因") is None


def test_unchecked_never_reaches_go(env):
    """沒在「張貼」欄打 OK 的，畫面上永遠不會出現在「會貼上校網」。

    承辦人 2026-08-11 的原話:「張貼要和陳核一樣，是我逐筆勾選，不能直接就跑」。
    """
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": TEXT}, path=sheet)
    data, _ = ui.web_payload()
    assert data["可貼"] == []
    assert [i["文號"] for i in data["候選"]] == ["MWAA0001"]


def test_finished_docs_never_listed(env):
    """辦完的公文不可以出現在張貼頁 —— 不管「張貼」欄留著什麼。

    兩種辦完:磁碟有 *已存查.txt ＋ *已公告.txt（程式辦的）、
    兩關都標「已辦」（自己宣告的）。
    """
    work, sheet = env
    _doc(work, "MWAA0001", marks=("已存查.txt", "已公告.txt"))
    _doc(work, "MWAA0002")
    rs.upsert([{"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"},
               {"文號": "MWAA0002", "公告": TEXT, "陳會": "已辦", "張貼": "已辦"}],
              path=sheet)
    data, _ = ui.web_payload()
    assert (data["可貼"], data["擋下"], data["候選"]) == ([], [], [])


def test_page_and_cli_agree(env):
    """畫面說會貼的，要跟 CLI（post_web_batch.plan）算的完全一樣。

    這兩份清單一旦分岔，最壞的方向是「畫面說不貼、實際貼出去了」——
    而這一頁貼出去是對外的。
    """
    work, sheet = env
    for no in ("MWAA0001", "MWAA0002", "MWAA0003"):
        _doc(work, no)
    rs.upsert([{"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"},
               {"文號": "MWAA0002", "張貼": "OK"},
               {"文號": "MWAA0003", "公告": TEXT}], path=sheet)
    data, _ = ui.web_payload()
    ready, blocked, cand, _ = pwb.plan(sheet)
    assert [i["文號"] for i in data["可貼"]] == [i["文號"] for i in ready]
    assert [i["文號"] for i in data["擋下"]] == [i["文號"] for i in blocked]
    assert [i["文號"] for i in data["候選"]] == [i["文號"] for i in cand]


# ── Chrome 的要求跟陳核／存查相反 ──────────────────────────────────────────

def test_chrome_light_is_the_opposite_of_send_page(env, monkeypatch):
    """張貼要收文那個 Chrome **關掉**，陳核／存查要它**開著**。

    兩頁的燈不可以共用:張貼不碰 edoc，是自己開一個新的 Chrome，而兩邊用同一個
    Selenium 設定檔（`--user-data-dir=…Chrome-Selenium` ＋ `--remote-debugging-port
    =9222`）。同一個設定檔被兩個 Chrome 佔住時，最壞的下場是 chromedriver 接到
    收文那個 Chrome，把 edoc 的視窗開去校網，收尾再把它整個關掉 —— 而重建那個
    session 要插卡、輸 PIN、還要過雙因子（坑 #20 卡了整個上午）。
    """
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"}, path=sheet)
    monkeypatch.setattr(pwb, "chrome_in_the_way", lambda: "那個 Chrome 還開著")
    data, _ = ui.web_payload()
    assert data["chrome"] == {"ok": False, "說明": "那個 Chrome 還開著"}
    # 清單照算 —— 關掉 Chrome 就能貼，不必連看都看不到。
    assert [i["文號"] for i in data["可貼"]] == ["MWAA0001"]


def test_chrome_in_the_way_reads_devtools(monkeypatch):
    """:9222 連得上 = 那個 Chrome 開著 → 要擋，而且訊息要講中文的下一步。"""
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    why = pwb.chrome_in_the_way()
    assert why and "關掉" in why

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert pwb.chrome_in_the_way() is None


# ── 送出入口的關卡 ─────────────────────────────────────────────────────────

@pytest.fixture
def srv(env, monkeypatch):
    """把 ui.Handler 起在隨機 port。WEB.start 換成假的 —— 測試絕不真的貼校網。"""
    calls = []
    monkeypatch.setattr(ui.WEB, "start", lambda: (calls.append(1), (True, None))[1])
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
    rs.upsert({"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"}, path=sheet)


def _mark(sheet, no="MWAA0001"):
    """現在算出來的指紋 —— 畫面按確定時帶回來的就是這個。"""
    return next(pwb.fingerprint(i) for i in pwb.plan(sheet)[0]
                if i["文號"] == no)


def _body(sheet, nos=("MWAA0001",)):
    """一份「畫面剛剛才算過」的送出請求。要驗後面幾道關卡的測試都得先過前面幾道。"""
    return {"確認": True, "文號": list(nos),
            "指紋": {n: _mark(sheet, n) for n in nos}}


def test_start_needs_confirmation(env, srv):
    work, sheet = env
    _one_ready(work, sheet)
    base, calls = srv
    r = _post(base, "/api/web/start", {"文號": ["MWAA0001"]})
    assert r["ok"] is False and "確認" in r["錯誤"]
    assert calls == []                      # 什麼都沒跑


def test_start_refuses_empty(env, srv):
    base, calls = srv
    r = _post(base, "/api/web/start", {"確認": True, "文號": []})
    assert r["ok"] is False
    assert calls == []


def test_start_refuses_when_list_changed(env, srv):
    """畫面看到的跟現在算出來的不一樣 → 一筆都不貼。

    中間有人在 Excel 裡多打了一個 OK、或公告文案被改過，清單就變了;
    那不是承辦人按確定時看到的東西。
    """
    work, sheet = env
    _one_ready(work, sheet)
    _doc(work, "MWAA0002")
    rs.upsert({"文號": "MWAA0002", "公告": TEXT, "張貼": "OK"}, path=sheet)
    base, calls = srv
    r = _post(base, "/api/web/start", {"確認": True, "文號": ["MWAA0001"]})
    assert r["ok"] is False and "清單變了" in r["錯誤"]
    assert calls == []


def test_start_blocked_when_edoc_chrome_open(env, srv, monkeypatch):
    """收文那個 Chrome 還開著就不要起 subprocess。

    跑下去最壞是把 edoc 那個視窗開去校網、收尾再關掉它。
    """
    work, sheet = env
    _one_ready(work, sheet)
    base, calls = srv
    body = _body(sheet)                 # 指紋要先算好（那道關卡排在前面）
    monkeypatch.setattr(pwb, "chrome_in_the_way", lambda: "那個 Chrome 還開著，請先關掉")
    r = _post(base, "/api/web/start", body)
    assert r["ok"] is False and "關掉" in r["錯誤"]
    assert calls == []


def test_start_blocked_when_plan_warns(env, srv, monkeypatch):
    """plan() 有警告（例如 env.env 缺 sssh_publish_unit）就不放行。

    跑下去會在「發布單位」那步停住，而那時 Chrome 已經開了、校網也登入了。
    """
    import taipeion_login_selenium as tls
    work, sheet = env
    _one_ready(work, sheet)
    base, calls = srv
    body = _body(sheet)                 # 指紋要先算好（那道關卡排在前面）
    monkeypatch.setattr(tls, "_read_config", lambda k: None)
    r = _post(base, "/api/web/start", body)
    assert r["ok"] is False and "sssh_publish_unit" in r["錯誤"]
    assert calls == []


def test_start_refuses_when_content_changed(env, srv):
    """文號一樣、**要貼的字被換掉了** → 不貼。

    2026-08-12 審出來的洞:原本只比文號。承辦人確認的是那幾百字，不是那 14 個
    字元;確認框開著時有人動了 Excel 的「公告」欄，貼出去的就不是他看過的東西。
    """
    work, sheet = env
    _one_ready(work, sheet)
    base, calls = srv
    r = _post(base, "/api/web/start",
              {"確認": True, "文號": ["MWAA0001"],
               "指紋": {"MWAA0001": "0000000000"}})
    assert r["ok"] is False and "不一樣" in r["錯誤"]
    assert calls == []


def test_start_refuses_without_fingerprint(env, srv):
    """沒帶指紋一律不動 —— 那代表畫面是舊版本，寧可要人重看一次。"""
    work, sheet = env
    _one_ready(work, sheet)
    base, calls = srv
    r = _post(base, "/api/web/start", {"確認": True, "文號": ["MWAA0001"]})
    assert r["ok"] is False
    assert calls == []


def test_start_runs_when_list_and_content_match(env, srv):
    """關卡都過了才真的跑，而且 `--expect` **要帶指紋** ——

    那支動手前會自己再算一次跟它比對:介面這幾道只擋得住畫面過期。
    """
    work, sheet = env
    _one_ready(work, sheet)
    base, calls = srv
    mark = _mark(sheet)
    r = _post(base, "/api/web/start",
              {"確認": True, "文號": ["MWAA0001"], "指紋": {"MWAA0001": mark}})
    assert r["ok"] is True and r["筆數"] == 1
    assert calls == [1]
    assert ui.WEB.argv == ["post_web_batch.py", "--go",
                           f"--expect=MWAA0001:{mark}"]


# ── 進度浮窗要認得這支工作 ─────────────────────────────────────────────────

def test_hud_knows_the_web_job():
    """`--job=web` 一定要在 prep_hud 的表裡。

    不在的話 `_parse_args` 會**退回 prep**（不要因為打錯字就開不起來），
    於是浮窗去看收文那支的進度 —— 顯示上一輪的舊訊息，還會以為早就跑完了，
    而那正是坑 #13「浮窗被當成可以動滑鼠的信號」。
    """
    assert prep_hud._parse_args(["--job=web"])[3] == "web"
    assert "web" in prep_hud.RUN_LABEL and "web" in prep_hud.DONE_LABEL
    # 結束時最重要的一句話是「可以動滑鼠了」,不是「完成」。
    assert "可以動滑鼠" in prep_hud.DONE_LABEL["web"]
    assert ui.WEB.hud == "web"
