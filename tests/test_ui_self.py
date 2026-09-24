# -*- coding: utf-8 -*-
"""ui.py 自訂公告頁的把關測試。

承辦人 2026-09-05:「我要貼一個公告，但這並不是因為公文而產生的公告」。
整條張貼路徑本來每一關都綁著公文，這一頁是另一個入口。

跟張貼頁一樣**真的對外**，所以這裡釘的是同一組關卡:

  1. 沒帶確認一律不動
  2. 畫面說會貼的，要跟 `custom_notice.plan()` 算的完全一樣
  3. **內容指紋要對** —— 確認框開著的時候字被改掉，一則都不准貼（坑 #24）
  4. 收文那個 Chrome 還開著不放行（跟陳核／存查條件相反，坑 #22）
  5. 已經貼出去的不准刪 —— 資料夾就是「貼過了」的痕跡，刪掉會重貼
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

import custom_notice as cn  # noqa: E402
import post_web_batch as pwb  # noqa: E402
import ui  # noqa: E402

BODY = "【領取期間】\n9月7日～9月24日\n【領取地點】\n圖書館 2 樓　資訊媒體組"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """假的 custom_notices/。絕不碰真的工作區,也絕不真的開 Chrome。"""
    monkeypatch.setattr(cn, "NOTICE_DIR", str(tmp_path / "custom_notices"))
    # Chrome 檢查預設當「沒有擋路的」。真的去連 :9222 會拿到這台機器當下的
    # 狀態（承辦人的 edoc Chrome 開著就整批測試變色），測試不可以看那個。
    monkeypatch.setattr(pwb, "chrome_in_the_way", lambda: None)


@pytest.fixture
def srv(monkeypatch):
    """把 ui.Handler 起在隨機 port。SELFN.start 換成假的 —— 絕不真的貼校網。"""
    calls = []
    monkeypatch.setattr(ui.SELFN, "start",
                        lambda: (calls.append(list(ui.SELFN.argv)), (True, None))[1])
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


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.load(r)


def _ready():
    return cn.save("【載具領取公告】9/7-9/24 至資媒組領取", BODY, ["處室公告"])


# ── 清單 ───────────────────────────────────────────────────────────────────

def test_payload_splits_ready_blocked_posted():
    ok = _ready()
    bad = cn.save("沒選分類的", BODY, [])
    done = cn.save("已經貼過的", BODY, ["處室公告"])
    open(os.path.join(cn.notice_path(done), f"{done}已公告.txt"),
         "w", encoding="utf-8").write("x")

    data = ui.self_payload()
    assert [i["編號"] for i in data["可貼"]] == [ok]
    assert [i["編號"] for i in data["擋下"]] == [bad]
    assert [i["編號"] for i in data["已貼"]] == [done]
    # 畫面要核對的是全校會讀到的那幾百字 —— 內文一定要帶上來
    assert "領取期間" in data["可貼"][0]["內文"]
    assert data["可貼"][0]["指紋"]
    assert "處室公告" in data["分類選項"]


def test_payload_matches_what_the_cli_computes():
    """畫面說會貼的，要跟真正動手那支算的完全一樣。"""
    _ready()
    ready, _ = cn.plan()
    data = ui.self_payload()
    assert [i["編號"] for i in data["可貼"]] == [i["編號"] for i in ready]
    assert [i["指紋"] for i in data["可貼"]] == [i["指紋"] for i in ready]


# ── 整理格式:算給你看，不偷偷改 ───────────────────────────────────────────

def test_normalize_route_returns_fixed_text_and_preview(srv):
    base, _ = srv
    j = _post(base, "/api/self/normalize",
              {"標題": "標題", "內文": "■ 領取期間\n   ‧ 身分證"})
    assert j["ok"] and j["改了"] == 2
    assert "【領取期間】" in j["內文"] and "- 身分證" in j["內文"]
    assert "領取期間" in j["預覽"]


def test_normalize_route_does_not_save_anything(srv):
    """按「整理格式」只是算給你看,不可以偷偷存一則出來。"""
    base, _ = srv
    _post(base, "/api/self/normalize", {"標題": "標題", "內文": "■ 小標"})
    assert cn.list_ids() == []


# ── 存 ─────────────────────────────────────────────────────────────────────

def test_save_creates_then_updates_in_place(srv):
    base, _ = srv
    j = _post(base, "/api/self/save",
              {"標題": "標題", "內文": BODY, "分類": ["處室公告"]})
    assert j["ok"] and j["擋下原因"] is None
    nid = j["編號"]
    j2 = _post(base, "/api/self/save",
               {"編號": nid, "標題": "改過的標題", "內文": BODY, "分類": ["處室公告"]})
    assert j2["編號"] == nid, "帶編號存就是改同一則,不可以又生一則"
    assert cn.load(nid)["標題"] == "改過的標題"


def test_save_reports_why_it_still_cannot_post(srv):
    base, _ = srv
    j = _post(base, "/api/self/save", {"標題": "標題", "內文": BODY, "分類": []})
    assert j["ok"] and "沒有選分類" in j["擋下原因"]


def test_save_refuses_to_edit_something_already_posted(srv):
    """貼出去的字不能再改 —— 校網上那則不會跟著變,兩邊會對不起來。"""
    base, _ = srv
    nid = _ready()
    open(os.path.join(cn.notice_path(nid), f"{nid}已公告.txt"),
         "w", encoding="utf-8").write("x")
    j = _post(base, "/api/self/save",
              {"編號": nid, "標題": "偷改", "內文": BODY, "分類": ["處室公告"]})
    assert not j["ok"] and "已經貼出去" in j["錯誤"]
    assert cn.load(nid)["標題"].startswith("【載具領取公告】")


# ── 刪 ─────────────────────────────────────────────────────────────────────

def test_delete_removes_a_draft(srv):
    base, _ = srv
    nid = _ready()
    assert _post(base, "/api/self/delete", {"編號": nid})["ok"]
    assert cn.list_ids() == []


def test_delete_refuses_when_already_posted(srv):
    """資料夾就是「貼過了」的痕跡,刪掉下次會重貼一次。"""
    base, _ = srv
    nid = _ready()
    open(os.path.join(cn.notice_path(nid), f"{nid}已公告.txt"),
         "w", encoding="utf-8").write("x")
    j = _post(base, "/api/self/delete", {"編號": nid})
    assert not j["ok"] and "不能刪" in j["錯誤"]
    assert nid in cn.list_ids()


# ── 送出:唯一真的對外的入口 ───────────────────────────────────────────────

def test_start_requires_confirmation(srv):
    base, calls = srv
    nid = _ready()
    j = _post(base, "/api/self/start", {"編號": [nid]})
    assert not j["ok"] and calls == []


def test_start_passes_id_and_fingerprint_to_the_worker(srv):
    base, calls = srv
    nid = _ready()
    it = cn.evaluate(nid)
    j = _post(base, "/api/self/start",
              {"確認": True, "編號": [nid],
               "指紋": {nid: cn.fingerprint(it)}})
    assert j["ok"] and j["筆數"] == 1
    assert calls and calls[0][-1] == f"{nid}:{cn.fingerprint(it)}"


def test_start_refuses_when_text_changed_after_confirm(srv):
    """確認框開著的時候內文被改掉 → 一則都不准貼（坑 #24 同型）。"""
    base, calls = srv
    nid = _ready()
    stale = cn.fingerprint(cn.evaluate(nid))
    cn.save("【載具領取公告】9/7-9/24 至資媒組領取", "被換掉的內文",
            ["處室公告"], notice_id=nid)
    j = _post(base, "/api/self/start",
              {"確認": True, "編號": [nid], "指紋": {nid: stale}})
    assert not j["ok"] and "不一樣" in j["錯誤"]
    assert calls == []


def test_start_refuses_when_notice_is_gone(srv):
    base, calls = srv
    j = _post(base, "/api/self/start",
              {"確認": True, "編號": ["SELF-19000101-01"],
               "指紋": {"SELF-19000101-01": "x"}})
    assert not j["ok"] and "貼不了" in j["錯誤"]
    assert calls == []


def test_start_refuses_when_edoc_chrome_is_in_the_way(srv, monkeypatch):
    """跟陳核／存查**條件相反**:那兩頁要那個 Chrome 開著,這一頁要它關掉。

    漏了這道，最壞的下場是把收文那個已經登入好的 edoc Chrome 開去校網，
    然後收尾把它關掉（坑 #22）。
    """
    base, calls = srv
    monkeypatch.setattr(pwb, "chrome_in_the_way", lambda: "收文那個 Chrome 還開著")
    nid = _ready()
    it = cn.evaluate(nid)
    j = _post(base, "/api/self/start",
              {"確認": True, "編號": [nid], "指紋": {nid: cn.fingerprint(it)}})
    assert not j["ok"] and "Chrome" in j["錯誤"]
    assert calls == []


def test_plan_route_serves_the_same_thing_as_payload(srv):
    base, _ = srv
    nid = _ready()
    j = _get(base, "/api/self/plan")
    assert j["ok"] and [i["編號"] for i in j["可貼"]] == [nid]
