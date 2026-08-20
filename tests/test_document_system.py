"""document_system cascade 串接 / handler 委派測試(不需瀏覽器)。"""
import document_system


class _SwitchTo:
    def default_content(self):
        pass


class _Driver:
    switch_to = _SwitchTo()
    current_url = "https://edoc.gov.taipei/"
    title = "t"


def _patch_cascade(monkeypatch, counts, calls):
    monkeypatch.setattr(document_system, "_get_sidebar_paren_count",
                        lambda drv, label, **k: counts[label])
    monkeypatch.setattr(document_system, "_click_sidebar_item",
                        lambda drv, label, **k: True)
    monkeypatch.setattr(document_system, "pending_doc",
                        lambda drv, label="承辦中": calls.append(("pending", label)) or True)
    monkeypatch.setattr(document_system, "circulate_doc",
                        lambda drv: calls.append(("circulate",)) or True)
    monkeypatch.setattr(document_system, "pending_closeout_doc",
                        lambda drv: calls.append(("closeout",)) or True)


def test_cascade_processes_all_three_in_order(monkeypatch):
    """三類都有待辦 → 依序把承辦中、受會案件、待結案都跑過(不再做完第一類就停)。"""
    calls = []
    _patch_cascade(monkeypatch, {"承辦中": 1, "受會案件": 1, "待結案": 1}, calls)
    ok = document_system._run_sidebar_cascade(_Driver())
    assert ok is True
    assert calls == [("pending", "承辦中"), ("circulate",), ("closeout",)]


def test_cascade_skips_zero_but_continues_to_rest(monkeypatch):
    """承辦中=0 跳過,但仍繼續做受會案件、待結案(每類獨立)。"""
    calls = []
    _patch_cascade(monkeypatch, {"承辦中": 0, "受會案件": 2, "待結案": 1}, calls)
    ok = document_system._run_sidebar_cascade(_Driver())
    assert ok is True
    assert calls == [("circulate",), ("closeout",)]


def test_cascade_all_zero_returns_false(monkeypatch):
    calls = []
    _patch_cascade(monkeypatch, {"承辦中": 0, "受會案件": 0, "待結案": 0}, calls)
    ok = document_system._run_sidebar_cascade(_Driver())
    assert ok is False
    assert calls == []


def test_cascade_click_fail_skips_then_continues(monkeypatch):
    """某類點入失敗 → 跳過該類但繼續下一類(不再 return)。"""
    calls = []
    _patch_cascade(monkeypatch, {"承辦中": 1, "受會案件": 1, "待結案": 1}, calls)
    # 承辦中點入失敗,其餘成功
    monkeypatch.setattr(document_system, "_click_sidebar_item",
                        lambda drv, label, **k: label != "承辦中")
    ok = document_system._run_sidebar_cascade(_Driver())
    assert ok is True
    assert calls == [("circulate",), ("closeout",)]  # 承辦中點失敗被跳過


def test_circulate_doc_delegates_to_pending_doc_with_label(monkeypatch):
    """受會案件 = 承辦中:circulate_doc 直接呼叫 pending_doc(label='受會案件')。"""
    got = {}
    monkeypatch.setattr(document_system, "pending_doc",
                        lambda drv, label="承辦中": got.update(label=label) or True)
    assert document_system.circulate_doc(object()) is True
    assert got["label"] == "受會案件"


# ── 備料:清單分頁（2026-08-20 同事回報）───────────────────────────────────
#
# 公文超過 10 筆時 edoc 會分頁，而備料不陳會、公文會一直留在承辦中，
# 所以筆數只會越積越多。原本只讀第 1 頁 → 第 11 筆之後從沒被下載，
# 畫面卻印「共處理 10/10 筆」，跟全部收完長得一模一樣。

class _PrepDriver:
    """假 driver:只需要 window/frame 這幾個動作，公文號由 list_pager 那邊餵。"""

    switch_to = _SwitchTo()
    current_url = "https://edoc.gov.taipei/"
    title = "t"
    current_window_handle = "main"
    window_handles = ["main"]

    def __init__(self):
        _PrepDriver.switch_to = self

    def default_content(self):
        pass

    def window(self, handle):
        pass


def _patch_prep(monkeypatch, pages, warns=(), expect=-1):
    """把 pending_doc_prep 的外部依賴全換掉，回傳「實際點開了哪幾筆」。"""
    import list_pager
    import pending_doc_handler

    opened = []
    monkeypatch.setattr(document_system, "_switch_to_frame_with_xpath",
                        lambda *a, **k: True)
    monkeypatch.setattr(document_system, "_prep_sidebar_count",
                        lambda drv, label: expect)
    monkeypatch.setattr(document_system, "_prep_already_done", lambda no: False)
    monkeypatch.setattr(document_system, "_click_doc_by_no",
                        lambda drv, no: opened.append(no) or True)
    monkeypatch.setattr(document_system.time, "sleep", lambda s: None)
    monkeypatch.setattr(list_pager, "walk_pages",
                        lambda drv, expect=-1, **k: ([list(p) for p in pages],
                                                     list(warns)))
    monkeypatch.setattr(list_pager, "ensure_page_has",
                        lambda drv, no, **k: True)
    monkeypatch.setattr(pending_doc_handler, "handle_opened_document",
                        lambda drv, do_fill_draft=True: True)
    return opened


def test_prep_processes_docs_on_second_page(monkeypatch):
    """第 2 頁的公文也要被點開下載 —— 這次的病灶。"""
    opened = _patch_prep(monkeypatch,
                         [["MWAA0001", "MWAA0002"], ["MWAA0003"]], expect=3)
    assert document_system.pending_doc_prep(_PrepDriver()) is True
    assert opened == ["MWAA0001", "MWAA0002", "MWAA0003"]


def test_prep_skips_doc_it_cannot_find_on_any_page(monkeypatch):
    """翻遍清單都找不到某一筆 → 跳過它，但**不影響**其他筆繼續做。"""
    import list_pager
    opened = _patch_prep(monkeypatch, [["MWAA0001", "MWAA0002"]], expect=2)
    monkeypatch.setattr(list_pager, "ensure_page_has",
                        lambda drv, no, **k: no != "MWAA0001")
    assert document_system.pending_doc_prep(_PrepDriver()) is True
    assert opened == ["MWAA0002"]


def test_prep_warns_when_fewer_than_sidebar_says(monkeypatch, capsys):
    """左側寫 13 筆、只處理了 10 筆 → 要明講少了 3 筆，不可以安靜收工。"""
    _patch_prep(monkeypatch, [[f"MWAA00{i:02d}" for i in range(10)]], expect=13)
    document_system.pending_doc_prep(_PrepDriver())
    out = capsys.readouterr().out
    assert "少了 3 筆" in out
