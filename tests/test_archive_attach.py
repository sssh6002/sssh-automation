# -*- coding: utf-8 -*-
"""2026-08 edoc 版更:存查前一定要先點【附件歸檔】。

來源是市府那份「附件資訊自動帶入附件編目欄位」操作手冊:
「現修改為需先點選【附件歸檔】，方能存查或發文，否則會跳出提示視窗」，
而且「若公文無附件，也需要先點選【附件歸檔】」。

這條害慘 2026-08-14 那批（坑 #26）:按「確定存檔」被提示視窗擋住 → 簽章根本沒
開始 → 等不到 pinCode → 而那道「文號從待結案可見列消失」的驗證照樣回報成功、
寫下假標記 → 整批被自己的閘門鎖死。

所以這裡釘的是:
  1. 一定先做附件歸檔，才輪到「確定存檔」
  2. 附件歸檔做不到 → **不按確定存檔**（回 False，呼叫端會在寫標記之前收工）
  3. 開了新視窗要關掉，而且要切回存查表單那層 frame
  4. 用完一定還原 document_closure 的那支函式
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import archive_batch as ab  # noqa: E402


class FakeEl:
    def __init__(self, driver, tag="input", shown=True):
        self.driver, self.tag, self.shown = driver, tag, shown

    def is_displayed(self):
        return self.shown


class FakeDriver:
    """夠用就好的假瀏覽器:記下點過什麼、切過哪些視窗。"""

    def __init__(self, has_attach=True, opens_window=True, frame_ok=True):
        self.has_attach = has_attach
        self.opens_window = opens_window
        self.frame_ok = frame_ok
        self.clicked = []
        self.window_handles = ["main"]
        self.current_window_handle = "main"
        self.closed = []
        self.switched = []
        self.frame_calls = 0
        self.switch_to = self
        self._attach_clicked = False

    # --- selenium 介面的最小子集 ---
    def find_elements(self, by, value):
        if "附件歸檔" in value:
            return [FakeEl(self)] if self.has_attach else []
        if "確定存檔" in value:
            # 切過視窗之後要先切回 frame 才看得到（frame_calls > 0）
            if self.switched and self.frame_calls == 0:
                return []
            return [FakeEl(self)] if self.frame_ok else []
        return []

    def find_element(self, by, value):
        return FakeEl(self)

    def execute_script(self, js, *a):
        if "click" in js:
            self.clicked.append("click")
            if not self._attach_clicked:
                self._attach_clicked = True
                if self.opens_window:
                    self.window_handles = ["main", "popup"]

    def window(self, h):
        self.switched.append(h)
        self.current_window_handle = h
        self.frame_calls = 0            # 切視窗會把 frame 重設 —— 就是這個坑

    def close(self):
        self.closed.append(self.current_window_handle)
        self.window_handles = [h for h in self.window_handles
                               if h != self.current_window_handle]

    def default_content(self):
        self.frame_calls += 1

    def frame(self, fr):
        self.frame_calls += 1

    def parent_frame(self):
        pass


@pytest.fixture
def patched(monkeypatch):
    """裝上 attachment_archive_first，回 (被換過的那支, 原本那支被叫幾次)。"""
    from document_closure import document_closure as dc
    calls = []
    monkeypatch.setattr(dc, "_click_confirm_save_button",
                        lambda d, timeout=10: (calls.append(1), True)[1])
    with ab.attachment_archive_first():
        return dc._click_confirm_save_button, calls


def test_attachment_step_runs_before_confirm(patched, monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *a: None)
    sel, calls = patched
    d = FakeDriver()
    assert sel(d) is True
    assert calls == [1]                 # 原本那支有被叫到
    assert d.closed == ["popup"]        # 附件視窗有關掉
    assert d.frame_calls > 0            # 有切回 frame


def test_refuses_to_confirm_when_attach_button_missing(patched, monkeypatch):
    """找不到【附件歸檔】→ **不按確定存檔**。

    按下去會被提示視窗擋住，而程式會誤判成功、寫下假標記（坑 #26）。
    回 False 的位置在呼叫端寫標記之前，所以什麼都不會留下。
    """
    import time
    monkeypatch.setattr(time, "sleep", lambda *a: None)
    sel, calls = patched
    assert sel(FakeDriver(has_attach=False)) is False
    assert calls == []                  # 確定存檔沒有被按


def test_refuses_when_form_lost_after_closing_popup(patched, monkeypatch):
    """關掉附件視窗後切不回存查表單 → 也不按確定存檔（會按到不知道什麼東西）。"""
    import time
    monkeypatch.setattr(time, "sleep", lambda *a: None)
    sel, calls = patched
    assert sel(FakeDriver(frame_ok=False)) is False
    assert calls == []


def test_inline_dialog_variant_still_confirms(patched, monkeypatch):
    """沒開新視窗（頁面內對話框）也要能往下走。"""
    import time
    monkeypatch.setattr(time, "sleep", lambda *a: None)
    sel, calls = patched
    d = FakeDriver(opens_window=False)
    assert sel(d) is True
    assert calls == [1]
    assert d.closed == []


def test_restores_the_original_function():
    from document_closure import document_closure as dc
    real = dc._click_confirm_save_button
    with pytest.raises(RuntimeError):
        with ab.attachment_archive_first():
            raise RuntimeError("中斷")
    assert dc._click_confirm_save_button is real
