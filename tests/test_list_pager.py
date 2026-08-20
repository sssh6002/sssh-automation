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
