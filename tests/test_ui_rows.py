# -*- coding: utf-8 -*-
"""摘要頁「擬辦 OK」按鈕給不給按 —— /api/rows 的「可打OK」「陳會OK」。

已送過陳核的公文「陳會」欄會一直停在 OK（upsert 只填空白格），
這種不能再顯示「✕ 先不送」讓人去清掉它。判定沿用 announce_doc._is_sent。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import announce_doc as ad  # noqa: E402
import post_web_batch as pwb  # noqa: E402
import review_sheet as rs  # noqa: E402
import ui  # noqa: E402


def _payload(monkeypatch, rows, sent=(), posted=()):
    monkeypatch.setattr(rs, "sync", lambda: (0, 0, 0, 0))
    monkeypatch.setattr(rs, "rows", lambda *a, **k: rows)
    monkeypatch.setattr(ui, "_status", lambda r, d: [])
    monkeypatch.setattr(rs, "is_archived", lambda r, d: False)
    monkeypatch.setattr(ad, "_is_sent", lambda r, d: r["文號"] in sent)
    monkeypatch.setattr(ui, "_doc_dir", lambda no: no)       # 目錄名 = 文號,方便假貼過
    monkeypatch.setattr(rs, "_marker_names", lambda d: [])
    monkeypatch.setattr(pwb, "posted_detail",
                        lambda d: ("2026-10-30", "圖書館") if d in posted else (None, None))
    out, _ = ui.rows_payload()
    return {o["文號"]: o for o in out}


def test_blank_and_ok_can_be_toggled(monkeypatch):
    got = _payload(monkeypatch, [{"文號": "A", "陳會": None},
                                 {"文號": "B", "陳會": "ok"}])
    assert got["A"]["可打OK"] and not got["A"]["陳會OK"]
    assert got["B"]["可打OK"] and got["B"]["陳會OK"]


def test_sent_or_done_cannot_be_toggled(monkeypatch):
    got = _payload(monkeypatch, [{"文號": "S", "陳會": "OK"},
                                 {"文號": "D", "陳會": "已辦"}], sent={"S"})
    assert not got["S"]["可打OK"]          # 送過了,欄位還停在 OK
    assert not got["D"]["可打OK"]


def test_post_ok_needs_text_and_not_posted(monkeypatch):
    got = _payload(monkeypatch, [
        {"文號": "T", "公告": "【研習】x內文", "張貼": None},
        {"文號": "E", "公告": "", "張貼": None},
        {"文號": "P", "公告": "【研習】x內文", "張貼": "OK"},
        {"文號": "D", "公告": "【研習】x內文", "張貼": "已辦"},
    ], posted={"P"})
    assert got["T"]["可打張貼OK"] and not got["T"]["張貼OK"]
    assert not got["E"]["可打張貼OK"]       # 沒文案沒東西可審
    assert not got["P"]["可打張貼OK"]       # 貼過了,欄位停在 OK 也不能清
    assert not got["D"]["可打張貼OK"]


def test_stray_link_flagged(monkeypatch):
    got = _payload(monkeypatch, [{"文號": "S", "公告": "內文" + ad.STRAY_MARK}])
    assert got["S"]["待查核"]
