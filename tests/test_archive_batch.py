# -*- coding: utf-8 -*-
"""結案存查（fork 版）的把關測試。

歸檔**無 admin 介入無法復原**，而系管師那條路歸檔完會自動貼校網。
這裡釘的是:
  1. 自動貼校網真的被關掉（這是承辦人選擇拆開的核心）
  2. 檔號讀不到／判成待確認的公文不可以歸檔（不要猜檔號）
  3. sidebar 讀不到時退回 JS 版，不要整批中止
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import archive_batch as ab  # noqa: E402


# ── 自動貼校網必須被關掉 ───────────────────────────────────────────────────

def test_auto_post_is_disabled():
    """跑完 disable_auto_post 之後，maybe_post_announcement 不可以再真的貼。

    document_closure.py 尾端那句是**函式內 import**，所以換模組屬性就會生效。
    2026-07-28 曾因為這條沒有人工確認，把他人業務的內部公文送上校網。
    """
    import document_closure.document_closure_post_web as pw
    original = pw.maybe_post_announcement
    try:
        ab.disable_auto_post()
        assert pw.maybe_post_announcement is not original
        # 換上去的那支要能被安全呼叫，而且回 False（skipped，不是錯誤）
        assert pw.maybe_post_announcement(None, "任何目錄") is False
    finally:
        pw.maybe_post_announcement = original


def test_disable_auto_post_is_idempotent():
    """重複呼叫不可以把「不做事的版本」再包一層當成原版存起來。"""
    import document_closure.document_closure_post_web as pw
    original = pw.maybe_post_announcement
    try:
        ab.disable_auto_post()
        first = pw.maybe_post_announcement
        ab.disable_auto_post()
        assert pw.maybe_post_announcement is first
    finally:
        pw.maybe_post_announcement = original


def test_sidebar_fallback_only_kicks_in_when_original_fails(monkeypatch):
    """原版讀得到就用原版；讀不到（選單收合）才退回 JS 版。"""
    import document_system as ds
    import edoc_sidebar as sb
    original = ds._get_sidebar_paren_count
    try:
        monkeypatch.setattr(ds, "_get_sidebar_paren_count",
                            lambda d, lab, timeout=10: 3)
        monkeypatch.setattr(sb, "sidebar_count",
                            lambda d, lab: pytest.fail("不該用到 fallback"))
        ab.install_sidebar_fallback()
        assert ds._get_sidebar_paren_count(None, "待結案") == 3

        ds._get_sidebar_paren_count = original
        monkeypatch.setattr(ds, "_get_sidebar_paren_count",
                            lambda d, lab, timeout=10: -1)
        monkeypatch.setattr(sb, "sidebar_count", lambda d, lab: 7)
        ab.install_sidebar_fallback()
        assert ds._get_sidebar_paren_count(None, "待結案") == 7
    finally:
        ds._get_sidebar_paren_count = original


# ── 檔號判定 ───────────────────────────────────────────────────────────────

@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "_BASE_DIR", str(tmp_path))
    return tmp_path


def _closure_doc(work, no, head, base="document_download_closure"):
    d = work / base / no
    d.mkdir(parents=True)
    (d / f"{no}總結.claude.md").write_text(head, encoding="utf-8")
    return d


def test_reads_category_and_number(work):
    _closure_doc(work, "MWAA0001", "#存查分類:研習 03750401\n##一、公告。\n")
    it = ab.evaluate("MWAA0001")
    assert (it["分類"], it["檔號"]) == ("研習", "03750401")
    assert it.get("擋下原因") is None
    assert "結案目錄" in it["來源"]


def test_blocks_when_category_is_pending(work):
    """判成「待確認」（沒有 8 位檔號）→ 不可以歸檔。

    書展／藝文展覽的檔號承辦人還沒查到（交接檔待補項）。猜錯就是公文歸錯檔。
    """
    _closure_doc(work, "MWAA0002", "#存查分類:待確認\n##一、公告。\n")
    it = ab.evaluate("MWAA0002")
    assert it["分類"] == "待確認"
    assert "檔號" in it["擋下原因"]


def test_blocks_when_no_summary(work):
    (work / "document_download_closure" / "MWAA0003").mkdir(parents=True)
    assert "補跑摘要" in ab.evaluate("MWAA0003")["擋下原因"]


def test_blocks_when_already_archived(work):
    d = _closure_doc(work, "MWAA0004", "#存查分類:研習 03750401\n")
    (d / "MWAA0004已存查.txt").write_text("x", encoding="utf-8")
    assert "已經存查過" in ab.evaluate("MWAA0004")["擋下原因"]


def test_falls_back_to_pending_dir_and_says_so(work):
    """結案目錄還沒有時退回承辦中目錄，但**要講明來源**。

    兩處總結可能不一致（交接檔記過 MWAA1156007051:研習 vs 競賽），
    不能讓人以為預覽看到的檔號一定是歸檔會用的那個。
    """
    _closure_doc(work, "MWAA0005", "#存查分類:競賽 03750401\n",
                 base="document_download")
    it = ab.evaluate("MWAA0005")
    assert it["檔號"] == "03750401"
    assert "承辦中目錄" in it["來源"]


def test_closure_dir_wins_over_pending_dir(work):
    """兩邊都有總結時，以歸檔實際會用的結案目錄為準。"""
    _closure_doc(work, "MWAA0006", "#存查分類:研習 03750401\n")
    _closure_doc(work, "MWAA0006", "#存查分類:競賽 03750499\n",
                 base="document_download")
    it = ab.evaluate("MWAA0006")
    assert (it["分類"], it["檔號"]) == ("研習", "03750401")


def test_preview_splits_ready_and_blocked(work):
    _closure_doc(work, "MWAA0001", "#存查分類:研習 03750401\n")
    _closure_doc(work, "MWAA0002", "#存查分類:待確認\n")
    ready, blocked = ab.preview(["MWAA0001", "MWAA0002"])
    assert [i["文號"] for i in ready] == ["MWAA0001"]
    assert [i["文號"] for i in blocked] == ["MWAA0002"]


# ── 順序:整批做到哪裡 ──────────────────────────────────────────────────────
#
# `process_document_closure` 每輪只處理待結案清單的第一筆，失敗就整批中止。
# 所以「這一筆過不過得了」跟「這一批做不做得到它」是兩件事 —— 排在被擋那筆
# 後面的公文，判定再乾淨這批也輪不到。

def test_plan_stops_at_first_blocked(work):
    _closure_doc(work, "MWAA0001", "#存查分類:研習 03750401\n")
    _closure_doc(work, "MWAA0002", "#存查分類:待確認\n")          # 卡在這
    _closure_doc(work, "MWAA0003", "#存查分類:資安 03750402\n")   # 自己沒問題
    p = ab.plan(["MWAA0001", "MWAA0002", "MWAA0003"])
    assert [it["狀態"] for it in p["清單"]] == ["go", "stop", "after"]
    assert p["會歸檔"] == ["MWAA0001"]
    assert p["可跑"] is True
    # 輪不到的那筆不可以被講成「它有問題」—— 承辦人會跑去查一份好好的公文。
    assert p["清單"][2].get("擋下原因") is None


def test_plan_order_decides_how_much_gets_done(work):
    """同一批公文，被擋那筆排在最前面就一筆都做不到。"""
    _closure_doc(work, "MWAA0001", "#存查分類:研習 03750401\n")
    _closure_doc(work, "MWAA0002", "#存查分類:待確認\n")
    assert ab.plan(["MWAA0002", "MWAA0001"])["會歸檔"] == []
    assert ab.plan(["MWAA0001", "MWAA0002"])["會歸檔"] == ["MWAA0001"]


def test_plan_refuses_whole_batch_on_stale_archive_marker(work):
    """已存查標記卻還在待結案清單 → 整批不跑。

    `process_document_closure` **不看**那個標記檔，會把它當一般待結案公文再送
    一次「確定存檔」簽章 —— 對同一份公文重複簽章正是 2026-07-16 事故。
    這道閘門長在 fork 這側（系管師那支不改），所以只能整批擋。
    """
    d = _closure_doc(work, "MWAA0001", "#存查分類:研習 03750401\n")
    (d / "MWAA0001已存查.txt").write_text("x", encoding="utf-8")
    _closure_doc(work, "MWAA0002", "#存查分類:資安 03750402\n")
    p = ab.plan(["MWAA0001", "MWAA0002"])
    assert p["清單"][0]["狀態"] == "danger"
    assert p["可跑"] is False
    assert "MWAA0001" in p["不可跑原因"]
    # 危險那筆不擋住後面的判定（它不是「停止點」）—— 但整批照樣不跑。
    assert p["清單"][1]["狀態"] == "go"


def test_plan_carries_subject(work):
    """介面上要看得懂自己在核對哪一份公文，不能只有文號。"""
    d = _closure_doc(work, "MWAA0001", "#存查分類:研習 03750401\n")
    (d / "MWAA0001內容.txt").write_text("主旨：測試用的公文主旨。\n", encoding="utf-8")
    assert ab.plan(["MWAA0001"])["清單"][0]["主旨"] == "測試用的公文主旨。"


# ── --expect:動手前再比一次 ────────────────────────────────────────────────

def test_expect_matches():
    assert ab.expect_mismatch("A,B", ["A", "B"]) is None
    assert ab.expect_mismatch(" A , B ", ["A", "B"]) is None


def test_expect_rejects_different_order():
    """順序不同就是不同 —— 會做到哪裡整個變了。"""
    why = ab.expect_mismatch("A,B", ["B", "A"])
    assert why and "不一樣" in why


def test_expect_rejects_added_or_removed():
    assert ab.expect_mismatch("A,B", ["A", "B", "C"])
    assert ab.expect_mismatch("A,B", ["A"])
    assert ab.expect_mismatch("A", [])
