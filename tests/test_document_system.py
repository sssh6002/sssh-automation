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
