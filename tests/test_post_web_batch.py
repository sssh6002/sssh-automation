# -*- coding: utf-8 -*-
"""post_web_batch.py（校網張貼）的把關測試。

這是整套流程裡**唯一真的對外**的動作，所以這裡釘的是四件事:

  1. 沒有在「張貼」欄打 OK 的，永遠不會被貼（承辦人逐筆勾，跟陳核頁對稱）
  2. 文案裡有「待查核」網址的一律擋下 —— 那可能是來文夾帶的指令
  3. `--go` 一定要帶 `--expect`，而且動手前自己再算一次
  4. 換掉 `_parse_summary` / 版型常數之後**一定要還原**
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import announce_doc as ad  # noqa: E402
import post_web_batch as pwb  # noqa: E402
import review_sheet as rs  # noqa: E402

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
    monkeypatch.setattr(pwb, "_doc_dir",
                        lambda no: str(work / no) if (work / no).is_dir() else None)
    # 判定來源預設都「可以貼」，個別測試再覆蓋回去。
    monkeypatch.setattr(pw, "_parse_summary",
                        lambda d: {"handling": "於官網公告", "title": "主旨",
                                   "body": "1. 甲", "sync_categories": ["研習資訊"]})
    monkeypatch.setattr(pw, "_should_post", lambda s: True)
    monkeypatch.setattr(pw, "_already_announced", lambda d: False)
    import taipeion_login_selenium as tls
    monkeypatch.setattr(tls, "_read_config",
                        lambda k: "系管師群組" if k == "sssh_publish_unit" else None)
    return work, sheet


def _doc(work, no):
    d = work / no
    d.mkdir()
    return d


# ── 逐筆勾選才算數 ─────────────────────────────────────────────────────────

def test_unchecked_is_never_posted(env):
    """沒在「張貼」欄打 OK 的，永遠不會進「可貼」。

    承辦人 2026-08-11 的要求:「張貼要和陳核一樣，是我逐筆勾選，不能直接就跑」。
    """
    work, sheet = env
    for no in ("MWAA0001", "MWAA0002"):
        _doc(work, no)
    rs.upsert([{"文號": "MWAA0001", "公告": TEXT},                  # 沒勾
               {"文號": "MWAA0002", "公告": TEXT, "張貼": "OK"}],   # 勾了
              path=sheet)
    ready, blocked, cand, _ = pwb.plan(sheet)
    assert [i["文號"] for i in ready] == ["MWAA0002"]
    assert [i["文號"] for i in cand] == ["MWAA0001"]
    assert blocked == []


def test_checked_but_broken_goes_to_blocked(env):
    """勾了但貼不出去的要單獨一堆，不能默默混進可貼。"""
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "張貼": "OK"}, path=sheet)   # 公告欄空的
    ready, blocked, _, _ = pwb.plan(sheet)
    assert ready == []
    assert "公告欄是空的" in blocked[0]["擋下原因"]


# ── 待查核網址一律擋下 ─────────────────────────────────────────────────────

def test_stray_link_is_blocked_from_posting(env):
    """文案裡有「待查核」網址 → 擋下，不給貼。

    那種網址可能是 AI 幻覺，也可能是來文 PDF 裡夾帶的指令。公告頁只標紅提醒
    （那時還沒對外），到張貼這一步就是**幫它散布出去**，必須擋。
    """
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "張貼": "OK",
               "公告": TEXT + "\n\n" + ad.STRAY_MARK + "以下網址…"}, path=sheet)
    ready, blocked, _, _ = pwb.plan(sheet)
    assert ready == []
    assert "待查核" in blocked[0]["擋下原因"]


def test_already_announced_is_blocked(env, monkeypatch):
    """已經貼過的不再貼一次（重複公告在校網上很難看）。"""
    from document_closure import document_closure_post_web as pw
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"}, path=sheet)
    monkeypatch.setattr(pw, "_already_announced", lambda d: True)
    ready, blocked, _, _ = pwb.plan(sheet)
    assert ready == []
    assert "已經公告過" in blocked[0]["擋下原因"]


def test_missing_publish_unit_warns(env, monkeypatch):
    """env.env 缺 sssh_publish_unit → 先警告，不要讓人按下去才在發佈那步停住。"""
    import taipeion_login_selenium as tls
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"}, path=sheet)
    monkeypatch.setattr(tls, "_read_config", lambda k: None)
    ready, _, _, warn = pwb.plan(sheet)
    assert warn and "sssh_publish_unit" in warn[0]
    # 警告歸警告，清單照算 —— 但 run() 會擋下來（見 test_run_stops_on_warning）。
    assert [i["文號"] for i in ready] == ["MWAA0001"]


def test_run_stops_on_warning(env, monkeypatch):
    """有警告（例如缺 sssh_publish_unit）就不要開 Chrome ——

    跑下去只會在發佈那步停住，而那時 Chrome 已經開了、校網也登入了。
    """
    import taipeion_login_selenium as tls
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"}, path=sheet)
    monkeypatch.setattr(tls, "_read_config", lambda k: None)
    monkeypatch.setattr(
        "post_web_review._launch_bare_chrome",
        lambda: pytest.fail("有警告就不該開 Chrome"))
    assert pwb.run(["MWAA0001"], sheet) == (0, 0)


# ── 動手前再算一次 ─────────────────────────────────────────────────────────

def test_run_refuses_when_list_changed(env, monkeypatch):
    """畫面看到的跟現在算的不一樣 → 一筆都不貼。"""
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"}, path=sheet)
    monkeypatch.setattr(
        "post_web_review._launch_bare_chrome",
        lambda: pytest.fail("清單對不上就不該開 Chrome"))
    assert pwb.run(["MWAA0002"], sheet) == (0, 0)


def test_run_refuses_when_content_changed(env, monkeypatch):
    """文號一樣、**文案被換掉了** → 一筆都不貼。

    2026-08-12 審出來的洞:原本只比文號。確認框開著的時候有人在 Excel 動了
    「公告」欄（或另一邊剛跑完產文案），貼出去的就不是他看過的字 ——
    而這是唯一對外的動作。
    """
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"}, path=sheet)
    monkeypatch.setattr(
        "post_web_review._launch_bare_chrome",
        lambda: pytest.fail("內容對不上就不該開 Chrome"))
    assert pwb.run(["MWAA0001:0000000000"], sheet) == (0, 0)


def test_fingerprint_follows_the_words(env):
    """指紋要跟著標題／內文／分類／附件動，文號變不變不影響它。"""
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"}, path=sheet)
    before = pwb.fingerprint(pwb.plan(sheet)[0][0])
    rs.upsert({"文號": "MWAA0001", "公告": TEXT + "（改了一句）"},
              path=sheet, overwrite=True)
    after = pwb.fingerprint(pwb.plan(sheet)[0][0])
    assert before and after and before != after
    # UI 拿到的是 _slim 的結果 —— 指紋要在裡面，不然畫面沒東西可帶回來。
    assert pwb._slim(pwb.plan(sheet)[0][0])["指紋"] == after


def test_failed_post_exits_nonzero(env, monkeypatch):
    """沒貼完一定要用非 0 結束碼離開。

    UI 判斷成敗只看結束碼(0 → 寫「張貼結束」、3 秒自動收起面板、報成功)。
    陳核與存查失敗都 SystemExit(1)，張貼原本沒有 —— 於是「一筆都沒貼」跟
    「全部貼完」在畫面上長得一模一樣，而這是唯一對外的動作。
    """
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"}, path=sheet)
    monkeypatch.setattr(sys, "argv",
                        ["post_web_batch.py", "--go", "--expect=MWAA0001",
                         "--path", sheet])
    monkeypatch.setattr(pwb, "run", lambda *a, **k: (0, 1))     # 一筆都沒貼成功
    with pytest.raises(SystemExit) as e:
        pwb.main()
    assert e.value.code == 1

    monkeypatch.setattr(pwb, "run", lambda *a, **k: (0, 0))     # 被關卡擋下
    with pytest.raises(SystemExit) as e:
        pwb.main()
    assert e.value.code == 1


def test_full_success_exits_zero(env, monkeypatch):
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": TEXT, "張貼": "OK"}, path=sheet)
    monkeypatch.setattr(sys, "argv",
                        ["post_web_batch.py", "--go", "--expect=MWAA0001",
                         "--path", sheet])
    monkeypatch.setattr(pwb, "run", lambda *a, **k: (2, 2))
    pwb.main()                                  # 不該拋 SystemExit


def test_parse_expect_allows_bare_doc_no():
    """手動跑 CLI 時只打文號也要能用（那種就只比清單、不比內容）。"""
    assert pwb.parse_expect(["MWAA0001", "MWAA0002:abc123"]) == {
        "MWAA0001": None, "MWAA0002": "abc123"}


def test_go_requires_expect(monkeypatch, capsys):
    """`--go` 不帶 `--expect` 直接拒絕 —— 這是唯一對外的動作。"""
    monkeypatch.setattr(sys, "argv", ["post_web_batch.py", "--go"])
    with pytest.raises(SystemExit):
        pwb.main()
    assert "--expect" in capsys.readouterr().out


# ── 替換完一定要還原 ───────────────────────────────────────────────────────

def test_summary_swap_only_touches_title_and_body():
    """換 `_parse_summary` 時**只換 title/body**，其他欄位原樣保留。

    承辦文字（_should_post 要用）與同步分類（決定貼到哪些類別）都靠它們，
    一起換掉會出鬼故事。
    """
    from document_closure import document_closure_post_web as pw
    real = pw._parse_summary
    base = {"handling": "於官網公告", "title": "舊主旨", "body": "1. 舊條列",
            "category": "研習", "sync_categories": ["研習資訊"]}
    pw._parse_summary = lambda d: dict(base)
    pw._doc_no_of = lambda d: "MWAA0001"
    try:
        with pwb.announcement_as_summary({"MWAA0001": TEXT}):
            got = pw._parse_summary("任何目錄")
        assert got["title"] == "【研習】測試公告"
        assert "📝【公告內容】" in got["body"]
        assert got["handling"] == "於官網公告"           # 沒被動
        assert got["sync_categories"] == ["研習資訊"]     # 沒被動
        assert got["category"] == "研習"                 # 沒被動
    finally:
        pw._parse_summary = real


def test_summary_swap_restores_even_on_error():
    from document_closure import document_closure_post_web as pw
    real = pw._parse_summary
    with pytest.raises(RuntimeError):
        with pwb.announcement_as_summary({}):
            raise RuntimeError("中斷")
    assert pw._parse_summary is real


def test_heading_style_applies_and_restores():
    """版型改靠左、字大一級;用完**一定要還原**。

    `sssh_style.py` 在 document_closure/ 底下（系管師那條路也在用），
    沒還原的話他跑 main.py 3 貼出去的版面會跟著變。
    """
    from document_closure import sssh_style
    old = sssh_style._S["h3"]
    with pwb.sssh_heading_style():
        cur = sssh_style._S["h3"]
        assert "text-align:left" in cur
        assert "font-size:1.3em" in cur
    assert sssh_style._S["h3"] == old


def test_heading_style_restores_even_on_error():
    from document_closure import sssh_style
    old = sssh_style._S["h3"]
    with pytest.raises(RuntimeError):
        with pwb.sssh_heading_style():
            raise RuntimeError("中斷")
    assert sssh_style._S["h3"] == old


# ── 文案拆解 ───────────────────────────────────────────────────────────────

def test_split_takes_first_line_as_title():
    title, body = pwb.split_announcement(TEXT)
    assert title == "【研習】測試公告"
    assert body.startswith("📝【公告內容】")
    assert "【研習】測試公告" not in body        # 標題不重複出現在內文


def test_split_handles_empty():
    assert pwb.split_announcement("") == ("", "")
    assert pwb.split_announcement(None) == ("", "")
