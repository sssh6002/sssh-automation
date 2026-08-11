# -*- coding: utf-8 -*-
"""ui.py 公告頁的把關測試。

這一頁**不會送出任何東西**（只叫 LLM 產文案、寫審核表「公告」欄），所以這裡釘的
不是「會不會送錯」，而是另外三件事:

  1. 分堆與判定完全來自 announce_doc.plan() —— 介面不另立標準。
     一旦分岔，最壞的方向是「畫面說不覆蓋、實際把手寫的定稿蓋掉」
     （2026-07-28 就是 --overwrite --limit 1 蓋掉三次）。
  2. 重產（overwrite）的護欄從介面這條路也要生效。
  3. 這一頁**不依賴 Chrome** —— Chrome 掛了也要能產文案。
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

import announce_doc as ad  # noqa: E402
import review_sheet as rs  # noqa: E402
import ui  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """假工作區＋假審核表。絕不碰桌面那份真的「公告彙整.xlsx」。"""
    work = tmp_path / "document_download"
    work.mkdir()
    sheet = str(tmp_path / "表.xlsx")
    monkeypatch.setattr(rs, "SCAN_DIRS", [str(work)])
    monkeypatch.setattr(rs, "sheet_path", lambda: sheet)
    monkeypatch.setattr(rs, "sync", lambda *a, **k: (0, 0, 0, 0))
    # 「這份公文該不該公告」是 announce_doc 去問校網張貼那支的事，
    # 這裡驗的是分堆與護欄，一律當「該公告」。
    monkeypatch.setattr(ad, "_should_announce", lambda d: True)
    return work, sheet


def _doc(work, no, marks=(), pii=False):
    d = work / no
    d.mkdir()
    (d / f"{no}內容.txt").write_text("說明：\n一、甲。", encoding="utf-8")
    for m in marks:
        (d / f"{no}{m}").write_text("x", encoding="utf-8")
    if pii:
        (d / f"{no}含個資.txt").write_text("⚠️ 偵測到個資", encoding="utf-8")
    return d


# ── 分堆 ───────────────────────────────────────────────────────────────────

def test_buckets(env):
    work, sheet = env
    for no in ("MWAA0001", "MWAA0002", "MWAA0003"):
        _doc(work, no)
    _doc(work, "MWAA0004", pii=True)
    rs.upsert([
        {"文號": "MWAA0001"},                                   # 還沒有文案
        {"文號": "MWAA0002", "公告": "機器產的草稿"},            # 已有文案
        {"文號": "MWAA0003", "公告": "我手寫的定稿", "張貼": "OK"},
        {"文號": "MWAA0004"},                                   # 含個資
    ], path=sheet)
    data, _ = ui.announce_payload(sent_only=False)
    assert [i["文號"] for i in data["會產"]] == ["MWAA0001"]
    assert [i["文號"] for i in data["已有文案"]] == ["MWAA0002", "MWAA0003"]
    assert [i["文號"] for i in data["不會產"]] == ["MWAA0004"]
    assert "含個資" in data["不會產"][0]["略過"]


def test_page_and_cli_agree(env):
    """畫面說會產的，要跟 CLI（announce_doc.plan）算的完全一樣。"""
    work, sheet = env
    _doc(work, "MWAA0001")
    _doc(work, "MWAA0002")
    rs.upsert([{"文號": "MWAA0001"},
               {"文號": "MWAA0002", "公告": "已經有了"}], path=sheet)
    data, _ = ui.announce_payload(sent_only=False)
    todo, _ = ad.plan(sheet, sent_only=False)
    assert [i["文號"] for i in data["會產"]] == [i["文號"] for i in todo]


def test_finished_docs_never_listed(env):
    """辦完的公文歸「舊文」，不佔這一頁 —— 跟陳核頁同一條規則。"""
    work, sheet = env
    _doc(work, "MWAA0001", marks=("已存查.txt",))
    rs.upsert({"文號": "MWAA0001", "陳會": "已辦", "張貼": "已辦"}, path=sheet)
    data, _ = ui.announce_payload(sent_only=False)
    assert (data["會產"], data["已有文案"], data["不會產"]) == ([], [], [])


def test_locked_draft_cannot_be_regenerated(env):
    """打過 OK 的定稿:重產鈕要是關的，而且要講得出原因。

    2026-07-28 回歸測試 —— 那次是 --overwrite 把承辦人手寫的公告蓋掉三次。
    這裡驗的是**介面這條路**也吃得到 plan() 的同一道護欄。
    """
    work, sheet = env
    _doc(work, "MWAA0001")
    _doc(work, "MWAA0002")
    rs.upsert([{"文號": "MWAA0001", "公告": "定稿", "張貼": "OK"},
               {"文號": "MWAA0002", "公告": "草稿"}], path=sheet)
    have = {i["文號"]: i for i in ui.announce_payload(sent_only=False)[0]["已有文案"]}
    assert have["MWAA0001"]["可重產"] is False
    assert "定稿不覆蓋" in have["MWAA0001"]["不可重產原因"]
    assert have["MWAA0002"]["可重產"] is True


def test_stray_link_marker_surfaces(env):
    """文案裡有「來文找不到的網址」→ 要在清單上標出來,不能只躺在文字裡。"""
    work, sheet = env
    _doc(work, "MWAA0001")
    _doc(work, "MWAA0002")
    rs.upsert([{"文號": "MWAA0001",
                "公告": "【活動】測\n\n" + ad.STRAY_MARK + "以下網址…"},
               {"文號": "MWAA0002", "公告": "【活動】乾淨的"}], path=sheet)
    have = {i["文號"]: i for i in ui.announce_payload(sent_only=False)[0]["已有文案"]}
    assert have["MWAA0001"]["待查核"] is True
    assert have["MWAA0002"]["待查核"] is False


def test_sent_only_filters_but_explains(env):
    """只列已陳核的:被濾掉的要出現在「不會產」並寫明原因，不可以默默消失。"""
    work, sheet = env
    _doc(work, "MWAA0001")                          # 還沒陳核
    _doc(work, "MWAA0002", marks=("已陳核.txt",))
    rs.upsert([{"文號": "MWAA0001"}, {"文號": "MWAA0002"}], path=sheet)

    on, _ = ui.announce_payload(sent_only=True)
    assert [i["文號"] for i in on["會產"]] == ["MWAA0002"]
    assert [i["文號"] for i in on["不會產"]] == ["MWAA0001"]
    assert "還沒陳核" in on["不會產"][0]["略過"]

    off, _ = ui.announce_payload(sent_only=False)
    assert [i["文號"] for i in off["會產"]] == ["MWAA0001", "MWAA0002"]


# ── 產生入口的關卡 ─────────────────────────────────────────────────────────

@pytest.fixture
def srv(env, monkeypatch):
    """把 ui.Handler 起在隨機 port。ANNC.start 換成假的 —— 測試絕不真叫 LLM。"""
    calls = []
    monkeypatch.setattr(ui.ANNC, "start",
                        lambda: (calls.append(1), (True, None))[1])
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


def test_start_needs_confirmation(env, srv):
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001"}, path=sheet)
    base, calls = srv
    r = _post(base, "/api/announce/start", {"文號": ["MWAA0001"]})
    assert r["ok"] is False and "確認" in r["錯誤"]
    assert calls == []                              # 一毛 token 都沒花


def test_start_refuses_empty(env, srv):
    base, calls = srv
    r = _post(base, "/api/announce/start", {"確認": True, "文號": []})
    assert r["ok"] is False
    assert calls == []


def test_start_uses_only_never_limit(env, srv):
    """帶下去的一定是 --only <文號>。

    2026-07-28 那次事故的另一半就是 --limit:它挑的是「清單的前 N 筆」，
    而清單會變 —— 承辦人以為在處理 A，程式動的是 B。
    """
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001"}, path=sheet)
    base, calls = srv
    r = _post(base, "/api/announce/start",
              {"確認": True, "文號": ["MWAA0001"], "只看已陳核": False})
    assert r["ok"] is True and r["筆數"] == 1
    assert calls == [1]
    assert ui.ANNC.argv == ["announce_doc.py", "--only", "MWAA0001"]
    assert "--limit" not in ui.ANNC.argv


def test_start_passes_the_same_flags_it_checked_with(env, srv):
    """重產／只看已陳核這兩個旗標，檢查用的跟真的帶下去的必須是同一組。

    不然會出現「用寬鬆的條件放行、用嚴格的條件執行」（或反過來）。
    """
    work, sheet = env
    _doc(work, "MWAA0001", marks=("已陳核.txt",))
    rs.upsert({"文號": "MWAA0001", "公告": "舊草稿"}, path=sheet)
    base, calls = srv
    r = _post(base, "/api/announce/start",
              {"確認": True, "文號": ["MWAA0001"], "重產": True,
               "只看已陳核": True})
    assert r["ok"] is True
    assert ui.ANNC.argv == ["announce_doc.py", "--overwrite", "--sent-only",
                            "--only", "MWAA0001"]


def test_start_refuses_to_overwrite_a_locked_draft(env, srv):
    """就算前端硬送重產，定稿也不能被蓋 —— 後端自己再問一次 plan()。"""
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": "我手寫的定稿", "張貼": "OK"},
              path=sheet)
    base, calls = srv
    r = _post(base, "/api/announce/start",
              {"確認": True, "文號": ["MWAA0001"], "重產": True,
               "只看已陳核": False})
    assert r["ok"] is False
    assert "定稿不覆蓋" in r["錯誤"]
    assert calls == []


def test_start_refuses_when_one_of_the_batch_went_stale(env, srv):
    """整批裡有一筆現在產不了 → 整批退回，不要默默少產一份。"""
    work, sheet = env
    _doc(work, "MWAA0001")
    _doc(work, "MWAA0002", pii=True)
    rs.upsert([{"文號": "MWAA0001"}, {"文號": "MWAA0002"}], path=sheet)
    base, calls = srv
    r = _post(base, "/api/announce/start",
              {"確認": True, "文號": ["MWAA0001", "MWAA0002"],
               "只看已陳核": False})
    assert r["ok"] is False
    assert "含個資" in r["錯誤"]
    assert calls == []


def test_start_does_not_need_chrome(env, srv, monkeypatch):
    """這一頁不碰 edoc —— Chrome 沒開、chrome_state 整支壞掉都要能產文案。"""
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001"}, path=sheet)

    def boom():
        raise AssertionError("公告頁不應該去問 Chrome 的狀態")

    monkeypatch.setattr(ui, "chrome_state", boom)
    base, calls = srv
    data, _ = ui.announce_payload(sent_only=False)
    assert "chrome" not in data
    r = _post(base, "/api/announce/start",
              {"確認": True, "文號": ["MWAA0001"], "只看已陳核": False})
    assert r["ok"] is True and calls == [1]


# ── 文案改字 ───────────────────────────────────────────────────────────────

def test_save_writes_the_announcement_column(env, srv):
    """文案框存得回審核表「公告」欄，而且是覆寫（承辦人自己在打字）。"""
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": "原本的字"}, path=sheet)
    base, _ = srv
    r = _post(base, "/api/save", {"文號": "MWAA0001", "公告": "我改過的字"})
    assert r["ok"] is True
    row = next(x for x in rs.rows(sheet) if x["文號"] == "MWAA0001")
    assert row["公告"] == "我改過的字"
