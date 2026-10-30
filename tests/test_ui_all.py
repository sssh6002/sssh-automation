# -*- coding: utf-8 -*-
"""ui.py「一鍵跑完」的把關測試。

一鍵會送陳核、會歸檔、會貼校網，三樣都收不回來。這裡釘的是:
  1. 順序對、每一步都是借原本那支 Job（判定不另寫一份）
  2. 任何一步沒過，後面全部不跑 —— 尤其是「最後一步貼校網」
  3. 動手的那一刻重算，跟確認表不一樣就停
  4. 串接期間，單獨的按鈕插不進來
  5. 入口:沒帶確認不動、畫面過期不動
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
import post_draft_batch as pdb  # noqa: E402
import post_web_batch as pwb  # noqa: E402
import ui  # noqa: E402


@pytest.fixture
def chain(monkeypatch):
    """一個新的 RunAll，每一支 Job 都換成假的:記下跑了誰、照劇本回結束碼。

    劇本預設一切順利:要送 1 份、待結案 1 份可歸檔、要產文案 1 份、要貼 1 份。
    各測試改 `world` 裡的值來演出事的情況。
    """
    world = {
        "send_now": ["S1"],
        "scan": {"清單": [{"文號": "A1"}], "可跑": True, "不可跑原因": "",
                 "會歸檔": ["A1"]},
        "anc_todo": [{"文號": "N1"}],
        "web_now": {"W1": "fp1"},
        "web_warn": [],
        "chrome_open": True,
        "codes": {},
    }
    ran, closed = [], []

    def fake_run(self, job):
        ran.append(job.name)
        return world["codes"].get(job.name, 0)

    monkeypatch.setattr(ui.RunAll, "_run", fake_run)
    monkeypatch.setattr(ui, "chrome_state", lambda: {"ok": True, "說明": None})
    monkeypatch.setattr(pdb, "plan", lambda *a, **k: (
        [{"文號": n} for n in world["send_now"]], [], []))
    monkeypatch.setattr(ui, "scan_plan", lambda: world["scan"])
    monkeypatch.setattr(ui, "remember_arch_batch", lambda p: None)
    monkeypatch.setattr(ad, "plan", lambda **k: (world["anc_todo"], []))
    monkeypatch.setattr(pwb, "plan", lambda *a, **k: (
        [{"文號": n, "fp": f} for n, f in world["web_now"].items()], [], [],
        world["web_warn"]))
    monkeypatch.setattr(pwb, "fingerprint", lambda it: it["fp"])
    monkeypatch.setattr(pwb, "chrome_in_the_way",
                        lambda: "edoc 開著" if world["chrome_open"] else None)

    import taipeion_login_selenium as tls

    def fake_close():
        closed.append(1)
        world["chrome_open"] = False
    monkeypatch.setattr(tls, "_close_selenium_chrome_only", fake_close)
    monkeypatch.setattr(ui.Job, "_chain", None)

    r = ui.RunAll()

    def go(send=("S1",), web=None):
        ok, err = r.start(list(send), {"W1": "fp1"} if web is None else web)
        assert ok, err
        r.thread.join(5)
        assert not r.running
        return r
    return go, world, ran, closed


def _log(r):
    return "\n".join(r.lines)


def test_happy_path_runs_six_steps_in_order(chain):
    go, world, ran, closed = chain
    r = go()
    assert r.exit == 0
    assert ran == ["收新公文", "送陳核", "讀待結案清單", "結案存查",
                   "產生公告文案", "貼上校網"]
    assert closed == [1]
    # 歸檔帶的是剛讀到的清單，貼校網帶的是文號＋指紋 —— 動手那支還會自己再比一次。
    assert ui.ARCH.argv == ["archive_batch.py", "--go", "--expect=A1"]
    assert ui.WEB.argv == ["post_web_batch.py", "--go", "--expect=W1:fp1"]
    # 只產新的、只產已陳核的，絕不帶 --overwrite。
    assert ui.ANNC.argv == ["announce_doc.py", "--sent-only", "--only", "N1"]
    assert "六步都跑完了" in _log(r)


def test_prep_failure_stops_everything(chain):
    go, world, ran, closed = chain
    world["codes"]["收新公文"] = 1
    r = go()
    assert r.exit == 1
    assert ran == ["收新公文"]
    assert closed == []
    assert "停在第 1/6 步" in _log(r)


def test_send_list_changed_after_prep_sends_nothing(chain):
    go, world, ran, closed = chain
    world["send_now"] = ["S1", "S2"]        # 收文後多了一份打 OK 的
    r = go()
    assert r.exit == 1
    assert "送陳核" not in ran
    assert "貼上校網" not in ran


def test_archive_blocked_stops_before_posting(chain):
    go, world, ran, closed = chain
    world["scan"] = {"清單": [{"文號": "A1"}], "可跑": False,
                     "不可跑原因": "狀態對不上", "會歸檔": []}
    r = go()
    assert r.exit == 1
    assert "結案存查" not in ran
    assert "貼上校網" not in ran
    assert closed == []
    assert "狀態對不上" in _log(r)


def test_empty_archive_list_is_skipped_not_failed(chain):
    go, world, ran, closed = chain
    world["scan"] = {"清單": [], "可跑": False, "不可跑原因": "", "會歸檔": []}
    r = go()
    assert r.exit == 0
    assert "結案存查" not in ran
    assert "貼上校網" in ran


def test_nothing_to_send_or_post_skips_and_keeps_edoc_open(chain):
    go, world, ran, closed = chain
    world["send_now"] = []
    world["web_now"] = {}
    r = go(send=(), web={})
    assert r.exit == 0
    assert "送陳核" not in ran
    assert "貼上校網" not in ran
    assert closed == []                     # 沒東西要貼就不關 edoc


def test_web_text_changed_posts_nothing(chain):
    go, world, ran, closed = chain
    world["web_now"] = {"W1": "fp-changed"}   # 文案在途中被改了
    r = go()
    assert r.exit == 1
    assert "貼上校網" not in ran


def test_announce_failure_stops_before_posting(chain):
    go, world, ran, closed = chain
    world["codes"]["產生公告文案"] = 2
    r = go()
    assert r.exit == 1
    assert "貼上校網" not in ran
    assert closed == []


def test_single_buttons_refused_while_chain_runs(monkeypatch):
    gate = threading.Event()
    r = ui.RunAll()
    monkeypatch.setattr(r, "_main", lambda: gate.wait(5))
    monkeypatch.setattr(ui.Job, "_chain", None)
    ok, _ = r.start([], {})
    assert ok
    try:
        ok, err = ui.Job("假的", ["x.py"]).start()
        assert not ok and "一鍵跑完" in err
    finally:
        gate.set()
        r.thread.join(5)


def test_run_borrows_job_and_copies_output(monkeypatch):
    """真的起一支小程式:結束碼要拿得到、印的字要抄進一鍵的面板。"""
    monkeypatch.setattr(ui.Job, "_chain", None)
    monkeypatch.setattr(ui.Job, "_busy", None)
    r = ui.RunAll()
    job = ui.Job("小程式", ["-c", "print('hello'); raise SystemExit(3)"])
    assert r._run(job) == 3
    assert "hello" in r.lines
    assert job.tee is None                  # 還回去之後不再抄


def test_status_reports_step(chain):
    go, world, ran, closed = chain
    r = go()
    s = r.status(0)
    assert s["總步數"] == 6 and s["結束碼"] == 0


# ── 入口 ───────────────────────────────────────────────────────────────────

@pytest.fixture
def srv(monkeypatch):
    calls = []
    data = {"送陳核": [{"文號": "S1"}], "貼校網": [{"文號": "W1", "指紋": "fp1"}],
            "張貼警告": []}
    monkeypatch.setattr(ui, "all_payload", lambda: (data, None))
    monkeypatch.setattr(ui.RUNALL, "start",
                        lambda s, w: (calls.append((s, w)), (True, None))[1])
    s = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}", calls, data
    s.shutdown()


def _post(base, path, obj):
    req = urllib.request.Request(base + path, method="POST",
                                 data=json.dumps(obj).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def test_start_needs_confirm(srv):
    base, calls, _ = srv
    j = _post(base, "/api/all/start", {"送陳核": ["S1"], "貼校網": {"W1": "fp1"}})
    assert not j["ok"] and calls == []


def test_start_refuses_stale_screen(srv):
    base, calls, _ = srv
    j = _post(base, "/api/all/start",
              {"確認": True, "送陳核": ["S1"], "貼校網": {"W1": "old"}})
    assert not j["ok"] and calls == []


def test_start_refuses_when_posting_has_warning(srv):
    base, calls, data = srv
    data["張貼警告"] = ["缺發布單位"]
    j = _post(base, "/api/all/start",
              {"確認": True, "送陳核": ["S1"], "貼校網": {"W1": "fp1"}})
    assert not j["ok"] and "缺發布單位" in j["錯誤"] and calls == []


def test_start_ok(srv):
    base, calls, _ = srv
    j = _post(base, "/api/all/start",
              {"確認": True, "送陳核": ["S1"], "貼校網": {"W1": "fp1"}})
    assert j["ok"]
    assert calls == [(["S1"], {"W1": "fp1"})]
