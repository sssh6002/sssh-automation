# -*- coding: utf-8 -*-
"""清單分頁（第 2 頁以後）的測試。

2026-08-20 同事回報:公文超過 10 筆、清單分頁之後，第 2 頁的公文完全不處理。
備料只讀第 1 頁就收工，畫面還印「共處理 10/10 筆」—— 漏收跟正常結束長一樣。

這裡釘四件事:
  1. 有第 2 頁就要翻過去，每一頁的公文號都要收進來（順序不能亂）
  2. 收到的筆數跟左側「承辦中(N)」對不上 → **一定要出警告**（這次的病灶）
  3. 翻頁鈕按了畫面沒動 → 不可以無限迴圈，也不可以假裝成功
  4. 翻頁只點翻頁鈕 —— 送出／刪除／簽收那些字樣一律不碰
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import list_pager as lp  # noqa: E402


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """測試不要真的等 8 秒。"""
    monkeypatch.setattr(lp, "_CHANGE_TIMEOUT", 0.3)
    monkeypatch.setattr(lp.time, "sleep", lambda s: None)


class FakeList:
    """假的清單頁:pages 是每一頁的公文號，點「下一頁」就換下一頁。

    next_returns_none=True → 畫面上沒有翻頁鈕（最後一頁的樣子）。
    dead_button=True       → 有鈕，但點了畫面不會變（翻頁鈕壞掉）。
    page_size              → 有「每頁筆數」下拉時，改大之後全部併成一頁。
    """

    def __init__(self, pages, next_returns_none=False, dead_button=False,
                 page_size=None):
        self.pages = [list(p) for p in pages]
        self.idx = 0
        self.next_returns_none = next_returns_none
        self.dead_button = dead_button
        self.page_size = page_size
        self.clicks = []
        self.switch_to = self

    # -- selenium 介面 --------------------------------------------------
    def default_content(self):
        pass

    def execute_script(self, script, *args):
        if "querySelectorAll('th')" in script:
            return list(self.pages[self.idx])
        if "selectedIndex" in script:
            if self.page_size is None:
                return None
            self.pages = [[no for p in self.pages for no in p]]
            self.idx = 0
            return {"value": self.page_size, "id": "pageSize", "name": "ps"}
        if "NEXT = [" in script:
            self.clicks.append(args[0])
            if self.next_returns_none or self.idx >= len(self.pages) - 1:
                return None
            if not self.dead_button:
                self.idx += 1
            return {"how": "下一頁鈕(下一頁)", "text": "下一頁",
                    "tag": "A", "html": "<a>下一頁</a>"}
        raise AssertionError(f"沒預期到的 script: {script[:60]}")


P1 = ["MWAA1156000001", "MWAA1156000002"]
P2 = ["MWAA1156000003", "MWAA1156000004"]
P3 = ["MWAA1156000005"]


# ── 讀這一頁 ───────────────────────────────────────────────────────────────

def test_page_doc_nos_reads_current_page():
    assert lp.page_doc_nos(FakeList([P1, P2])) == P1


def test_page_doc_nos_swallows_error():
    class Boom:
        switch_to = None

        def execute_script(self, *a):
            raise RuntimeError("boom")

    assert lp.page_doc_nos(Boom()) == []


# ── 翻頁:每一頁都要收到 ────────────────────────────────────────────────────

def test_walk_collects_every_page_in_order():
    d = FakeList([P1, P2, P3])
    pages, warn = lp.walk_pages(d, expect=5)
    assert pages == [P1, P2, P3]
    assert warn == []


def test_walk_single_page_does_not_warn():
    d = FakeList([P1], next_returns_none=True)
    pages, warn = lp.walk_pages(d, expect=2)
    assert pages == [P1]
    assert warn == []


def test_walk_stops_once_expected_count_reached():
    """收齊 expect 筆就別再翻 —— 多點一下都是多一次出錯機會。"""
    d = FakeList([P1, P2, P3])
    lp.walk_pages(d, expect=4)
    assert d.clicks == [2]          # 只翻到第 2 頁就夠 4 筆了


def test_page_size_dropdown_merges_into_one_page():
    d = FakeList([P1, P2], page_size=50)
    pages, warn = lp.walk_pages(d, expect=4)
    assert pages == [P1 + P2]
    assert warn == []
    assert d.clicks == []           # 併成一頁就不必按翻頁鈕


# ── 漏了就要看得見（這次的病灶）────────────────────────────────────────────

def test_missing_docs_are_reported_loudly():
    """左側寫 5 筆、只讀到 2 筆又沒有翻頁鈕 → 一定要喊，不可以安靜收工。"""
    d = FakeList([P1], next_returns_none=True)
    pages, warn = lp.walk_pages(d, expect=5)
    assert pages == [P1]
    assert warn and "少了 3 筆" in warn[0]


def test_dead_next_button_does_not_loop_forever():
    d = FakeList([P1, P2], dead_button=True)
    pages, warn = lp.walk_pages(d, expect=4)
    assert pages == [P1]                      # 沒翻過去就不要假裝有
    assert warn and "少了 2 筆" in warn[0]
    assert len(d.clicks) == 1                 # 按一次沒反應就停，不無限重試


def test_unknown_expect_still_notes_early_stop():
    """不知道應該有幾筆（sidebar 讀不到）時，翻頁提早停下只能提醒、不能斷定。"""
    d = FakeList([P1, P2], dead_button=True)
    pages, warn = lp.walk_pages(d, expect=-1)
    assert pages == [P1]
    assert warn and "後面幾頁這次沒處理到" in warn[0]


def test_walk_respects_max_pages():
    class Endless(FakeList):
        def execute_script(self, script, *args):
            if "NEXT = [" in script:
                self.clicks.append(args[0])
                self.pages.append([f"MWAA1156007{len(self.pages):03d}"])
                self.idx += 1
                return {"how": "x", "text": ">", "tag": "A", "html": "<a>"}
            return super().execute_script(script, *args)

    d = Endless([P1])
    pages, warn = lp.walk_pages(d, expect=-1, max_pages=4)
    assert len(pages) == 4
    assert warn and "翻超過 4 頁" in warn[0]


# ── 找某一筆公文在第幾頁 ───────────────────────────────────────────────────

def test_find_doc_on_later_page():
    d = FakeList([P1, P2, P3])
    assert lp.find_doc_across_pages(d, P3[0]) is True
    assert lp.page_doc_nos(d) == P3           # 停在它所在的那一頁


def test_find_doc_not_anywhere():
    d = FakeList([P1, P2])
    assert lp.find_doc_across_pages(d, "MWAA1156009999") is False


def test_ensure_page_has_does_not_page_when_already_visible():
    d = FakeList([P1, P2])
    assert lp.ensure_page_has(d, P1[0]) is True
    assert d.clicks == []


def test_ensure_page_has_refocus_first():
    """畫面停在第 3 頁、要找第 1 頁的公文 —— 得先把清單叫回第 1 頁。"""
    d = FakeList([P1, P2, P3])
    d.idx = 2

    def refocus():
        d.idx = 0
        return True

    assert lp.ensure_page_has(d, P1[0], refocus=refocus) is True


def test_ensure_page_has_gives_up_when_refocus_fails():
    d = FakeList([P1, P2])
    d.idx = 1
    assert lp.ensure_page_has(d, P1[0], refocus=lambda: False) is False


# ── 安全:不可以亂點清單上的按鈕 ────────────────────────────────────────────

@pytest.mark.parametrize("word", ["送出", "刪除", "簽收", "結案", "陳核", "退回"])
def test_dangerous_buttons_are_blacklisted(word):
    """清單頁上這些按鈕按下去不可復原 —— 翻頁的 JS 必須把它們排除。"""
    bad_block = lp._NEXT_JS.split("var PAGER")[0]
    assert word in bad_block


# ───────── 清單「簽核」欄:線上簽核 / 紙本轉線上 ─────────
# 紙本轉線上的公文來文本文還沒上傳,備料一定收不到。讀清單時記下這一欄,
# 失敗時才講得出真正的原因(2026-09-03 MWAA1156008767 實測)。

class FakeSignList:
    """execute_script 回 [{no, sign}, …] —— 有讀到「簽核」欄的清單。"""

    def __init__(self, rows):
        self.rows = rows

    def execute_script(self, script, *args):
        if "idxSign" in script:
            return [dict(r) for r in self.rows]
        return [r["no"] for r in self.rows]


def test_page_doc_nos_records_sign_column():
    lp.reset_sign_kinds()
    d = FakeSignList([{"no": "MWAA1156008753", "sign": "線"},
                      {"no": "MWAA1156008767", "sign": "紙"}])
    assert lp.page_doc_nos(d) == ["MWAA1156008753", "MWAA1156008767"]
    assert lp.sign_kind("MWAA1156008767") == "紙"
    assert lp.is_paper("MWAA1156008767") is True
    assert lp.is_paper("MWAA1156008753") is False


def test_sign_kind_unknown_doc_is_blank_not_paper():
    lp.reset_sign_kinds()
    assert lp.sign_kind("MWAA0000000000") == ""
    assert lp.is_paper("MWAA0000000000") is False


def test_page_doc_nos_still_works_without_sign_column():
    """欄位改名/別張清單讀不到「簽核」時,公文號照樣要讀得到。"""
    lp.reset_sign_kinds()
    d = FakeSignList([{"no": "MWAA1156008753", "sign": ""}])
    assert lp.page_doc_nos(d) == ["MWAA1156008753"]
    assert lp.sign_kind("MWAA1156008753") == ""


def test_walk_pages_clears_previous_sign_kinds():
    """上一輪的殘留會讓失敗原因報到別筆去 —— 重走清單要先清空。"""
    lp.reset_sign_kinds()
    lp.SIGN_KINDS["MWAA_舊的"] = "紙"
    lp.walk_pages(FakeList([["MWAA1156008753"]], next_returns_none=True))
    assert "MWAA_舊的" not in lp.SIGN_KINDS


# ── click_doc_by_no ────────────────────────────────────────────────────────
#
# 這支的真正判斷式（元素文字「含有」還是「完全等於」文號）在 JS 裡，Python
# 測試釘不住那段；這裡只釘外層合約——回布林值、例外要吞掉，不能讓整批
# 備料／送陳核因為這一步炸掉。

class FakeClickDriver:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append(args)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_click_doc_by_no_returns_true_on_success():
    d = FakeClickDriver(True)
    assert lp.click_doc_by_no(d, "MWAA1156009654") is True
    assert d.calls == [("MWAA1156009654",)]


def test_click_doc_by_no_returns_false_when_not_found():
    d = FakeClickDriver(False)
    assert lp.click_doc_by_no(d, "MWAA1156009654") is False


def test_click_doc_by_no_swallows_exceptions():
    d = FakeClickDriver(RuntimeError("壞了"))
    assert lp.click_doc_by_no(d, "MWAA1156009654") is False


# ── click_first_document_in_pending ──────────────────────────────────────────
#
# 這支的真正判斷式（三策略比對、抽出乾淨文號）在 JS 裡，同樣用 Node 手動驗證過
# （regex 對「?MWAA1156009654」/正常列/日期/中文標籤都對）；這裡只釘外層合約：
# 找到就回抽出來的文號並點下去、找不到回 None、例外要吞掉不能讓整批歸檔炸掉。

class FakeFirstDocDriver:
    """第一次 execute_script（無參數）= 找列；第二次（帶參數）= 點擊。"""

    def __init__(self, script_result=None, raise_on=None, click_raises=None):
        self.script_result = script_result
        self.raise_on = raise_on
        self.click_raises = click_raises
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append(args)
        if not args:
            if self.raise_on is not None:
                raise self.raise_on
            return self.script_result
        if self.click_raises is not None:
            raise self.click_raises
        return None


def test_click_first_document_in_pending_returns_doc_no_on_success():
    d = FakeFirstDocDriver(script_result={"el": "EL", "docNo": "MWAA1156009654"})
    assert lp.click_first_document_in_pending(d) == "MWAA1156009654"
    assert len(d.calls) == 2   # 找列一次、點擊一次


def test_click_first_document_in_pending_returns_none_when_not_found():
    d = FakeFirstDocDriver(script_result=None)
    assert lp.click_first_document_in_pending(d, timeout=0.02) is None


def test_click_first_document_in_pending_swallows_find_exception():
    d = FakeFirstDocDriver(raise_on=RuntimeError("壞了"))
    assert lp.click_first_document_in_pending(d, timeout=0.02) is None


def test_click_first_document_in_pending_returns_none_when_click_fails():
    d = FakeFirstDocDriver(script_result={"el": "EL", "docNo": "MWAA1156009654"},
                            click_raises=RuntimeError("click 失敗"))
    assert lp.click_first_document_in_pending(d, timeout=0.02) is None
