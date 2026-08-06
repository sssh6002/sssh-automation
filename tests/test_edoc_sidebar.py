# -*- coding: utf-8 -*-
"""sidebar fallback 的測試。

2026-08-05 的故障:簽收催辦通知後 edoc 把「待辦公文」選單收起來（height:0 +
overflow:hidden），Selenium 判定那些 <a> 不可見、`.text` 回空字串，於是
`_get_sidebar_paren_count` 回 -1 → 備料流程什麼都沒下載就結束。

這裡釘兩件事:
  1. JS 版讀數／點擊的契約（讀不到一律 -1／False，不可以亂猜）
  2. **選單收合時不可以用原版點擊** —— `_click_sidebar_item` 最後一個 XPath 會
     退到第一個可見元素（收合時是 <html>），點下去等於沒點卻回報成功。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import document_system as ds  # noqa: E402
import edoc_sidebar as sb  # noqa: E402


class FakeDriver:
    """只夠這幾支函式用的假 driver。script 回傳值由 `result` 決定。"""

    def __init__(self, result=None, boom=False):
        self.result = result
        self.boom = boom
        self.scripts = []
        self.current_url = "https://edoc.gov.taipei/tcqb/home/default.jsp"
        self.switch_to = self

    def default_content(self):
        pass

    def execute_script(self, script, *args):
        self.scripts.append((script, args))
        if self.boom:
            raise RuntimeError("boom")
        return self.result(script, args) if callable(self.result) else self.result


# ── JS 版讀數 ──────────────────────────────────────────────────────────────

def test_count_reads_paren_number():
    d = FakeDriver({"text": "承辦中(4)", "id": "menu76", "onclick": ""})
    assert sb.sidebar_count(d, "承辦中") == 4


def test_count_handles_thousands_separator():
    d = FakeDriver({"text": "承辦中(1,234)", "id": "menu76", "onclick": ""})
    assert sb.sidebar_count(d, "承辦中") == 1234


def test_count_zero_is_not_treated_as_missing():
    """受會案件(0) 要回 0，不能回 -1 —— 兩者對呼叫端意義不同。"""
    d = FakeDriver({"text": "受會案件(0)", "id": "menu88", "onclick": ""})
    assert sb.sidebar_count(d, "受會案件") == 0


def test_count_missing_element_returns_minus_one():
    assert sb.sidebar_count(FakeDriver(None), "承辦中") == -1


def test_count_without_number_returns_minus_one():
    """「催辦訊息4」這種沒括號的格式不歸這支管，不可以硬猜成 4。"""
    d = FakeDriver({"text": "承辦中", "id": "menu76", "onclick": ""})
    assert sb.sidebar_count(d, "承辦中") == -1


def test_count_swallows_script_error():
    assert sb.sidebar_count(FakeDriver(boom=True), "承辦中") == -1


# ── JS 版點擊 ──────────────────────────────────────────────────────────────

def test_click_reports_result():
    assert sb.click_sidebar(FakeDriver(True), "承辦中") is True
    assert sb.click_sidebar(FakeDriver(False), "承辦中") is False
    assert sb.click_sidebar(FakeDriver(boom=True), "承辦中") is False


# ── process_document_prep 的分支 ───────────────────────────────────────────

@pytest.fixture
def prep(monkeypatch):
    """把 process_document_prep 的依賴全換成假的，記錄誰被呼叫。"""
    log = []

    def stub(name, ret=None):
        def f(*a, **k):
            log.append((name, a[1:] if len(a) > 1 else ()))
            return ret
        return f

    monkeypatch.setattr(ds, "_get_urgent_message_count", lambda d: 0)
    monkeypatch.setattr(ds, "_get_pending_signoff_count", lambda d: -1)
    monkeypatch.setattr(ds, "_click_pending_signoff", stub("原版點待簽收", True))
    monkeypatch.setattr(ds, "_select_all_and_signoff", stub("簽收", True))
    monkeypatch.setattr(ds, "_click_sidebar_item", stub("原版點", True))
    monkeypatch.setattr(ds, "pending_doc_prep", stub("備料", True))
    monkeypatch.setattr(sb, "click_sidebar", stub("JS點", True))
    return log


def test_collapsed_menu_uses_js_path(prep, monkeypatch):
    """原版讀不到（選單收合）→ 讀與點都要走 JS 版，而且真的有進到備料。"""
    monkeypatch.setattr(ds, "_get_sidebar_paren_count", lambda d, lab: -1)
    monkeypatch.setattr(sb, "sidebar_count",
                        lambda d, lab: {"待簽收": 2, "承辦中": 4}.get(lab, 0))
    assert ds.process_document_prep(FakeDriver()) is True
    names = [n for n, _ in prep]
    assert "JS點" in names
    assert "原版點" not in names          # 收合時原版會點到 <html> 卻回報成功
    assert "原版點待簽收" not in names
    assert ("備料", ()) in [(n, ()) for n, _ in prep]
    assert "簽收" in names                # 待簽收 2 筆這次才簽得到


def test_normal_menu_keeps_original_path(prep, monkeypatch):
    """選單正常展開時，行為與修改前完全一樣 —— 一步都不改。"""
    monkeypatch.setattr(ds, "_get_pending_signoff_count", lambda d: 0)
    monkeypatch.setattr(ds, "_get_sidebar_paren_count",
                        lambda d, lab: 4 if lab == "承辦中" else 0)

    def boom(*a, **k):
        raise AssertionError("展開狀態下不該用到 JS fallback")

    monkeypatch.setattr(sb, "sidebar_count", boom)
    monkeypatch.setattr(sb, "click_sidebar", boom)
    assert ds.process_document_prep(FakeDriver()) is True
    assert "原版點" in [n for n, _ in prep]


def test_zero_count_downloads_nothing(prep, monkeypatch):
    """沒公文就別點進去 —— JS 讀到 0 也一樣。"""
    monkeypatch.setattr(ds, "_get_sidebar_paren_count", lambda d, lab: -1)
    monkeypatch.setattr(sb, "sidebar_count", lambda d, lab: 0)
    ds.process_document_prep(FakeDriver())
    names = [n for n, _ in prep]
    assert "備料" not in names
    assert "JS點" not in names
