# -*- coding: utf-8 -*-
"""ui.py 摘要頁的狀態標籤測試。

摘要頁是待辦清單。辦完的會移到舊文頁,所以**還留在這一頁的每一筆都欠著什麼**,
清單就有義務講清楚欠的是哪一關。

2026-09-22 的起因:13 筆公文陳核跑完、校網也貼了,只差結案存查。清單上只看得到
「陳會 OK／張貼 OK」兩個綠燈,沒有任何東西提到存查,於是看起來像辦完了卻不肯消失。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import review_sheet as rs  # noqa: E402
import ui  # noqa: E402

CONTENT = """發文日期：中華民國115年7月21日
發文字號：高餐大圖字第1151800055號
主旨：檢送本校圖書館出版主題桌遊相關資訊，請查照。
說明：
一、第一點。
附件：
"""

SUMMARY = """#存查分類:資安 03750402
##於官網公告，並轉知各處室及師生
###陳會
####重要消息

主旨：檢送本校圖書館出版主題桌遊相關資訊，請查照。

1. 摘要條列一。
"""


def _dir(tmp_path, name="MWAA1156009438", *, 陳核=False, 公告=False, 存查=False):
    d = tmp_path / name
    d.mkdir()
    (d / "123_456內容.txt").write_text(CONTENT, encoding="utf-8")
    (d / "123_456總結.claude.md").write_text(SUMMARY, encoding="utf-8")
    if 陳核:
        (d / f"{name}已陳核.txt").write_text("x", encoding="utf-8")
    if 公告:
        (d / "123_456已公告.txt").write_text("x", encoding="utf-8")
    if 存查:
        (d / "123_456已存查.txt").write_text("x", encoding="utf-8")
    return str(d)


def _texts(row, doc_dir):
    return [s["文字"] for s in ui._status(row, doc_dir)]


def test_posted_but_not_archived_says_pending_archive(tmp_path):
    """就差存查的那一筆,標籤要講出來。"""
    d = _dir(tmp_path, 陳核=True, 公告=True)
    assert "待存查" in _texts({"陳會": "OK", "張貼": "OK", "擬辦": "甲"}, d)


def test_pending_archive_pill_is_a_waiting_colour(tmp_path):
    """用「還欠一關」的色票,不可以是綠的 —— 綠燈正是當初誤導人的元凶。"""
    d = _dir(tmp_path, 陳核=True, 公告=True)
    pill = [s for s in ui._status({"陳會": "OK", "張貼": "OK", "擬辦": "甲"}, d)
            if s["文字"] == "待存查"]
    assert pill and pill[0]["類型"] == "wait"


def test_fresh_document_has_no_pending_archive_pill(tmp_path):
    """剛收進來的缺的是陳核,不是存查。"""
    d = _dir(tmp_path)
    assert "待存查" not in _texts({"陳會": None, "張貼": None, "擬辦": "甲"}, d)


def test_archived_document_has_no_pending_archive_pill(tmp_path):
    d = _dir(tmp_path, 陳核=True, 公告=True, 存查=True)
    row = {"陳會": "OK", "張貼": "OK", "擬辦": "甲"}
    assert rs.is_archived(row, d) is True
    assert "待存查" not in _texts(row, d)


def test_pending_archive_pill_survives_missing_folder(tmp_path):
    """資料夾不在也不能整頁爆掉。"""
    assert "待存查" not in _texts({"陳會": "OK", "張貼": "OK", "擬辦": "甲"}, None)
