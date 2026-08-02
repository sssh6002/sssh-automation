# -*- coding: utf-8 -*-
"""公文批次審核表 review_sheet 的行為測試。

最重要的一條:程式**不能覆蓋使用者手改過的儲存格**。番茄會在 Excel 裡潤摘要、
重寫擬辦,再跑一次同步不可以把他的字蓋掉。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import review_sheet as rs  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")


@pytest.fixture
def sheet(tmp_path):
    return str(tmp_path / "審核表.xlsx")


def _cell(path, row, col):
    ws = openpyxl.load_workbook(path).worksheets[0]
    return ws.cell(row=row, column=rs.COLUMNS.index(col) + 1).value


def test_creates_file_with_header(sheet):
    rs.upsert({"文號": "MWAA1", "主旨": "甲"}, path=sheet)
    ws = openpyxl.load_workbook(sheet).worksheets[0]
    assert [c.value for c in ws[1]] == rs.COLUMNS


def test_upsert_appends_then_updates_same_row(sheet):
    assert rs.upsert({"文號": "MWAA1", "主旨": "甲"}, path=sheet) == (1, 0)
    assert rs.upsert({"文號": "MWAA1", "摘要": "乙"}, path=sheet) == (0, 1)
    ws = openpyxl.load_workbook(sheet).worksheets[0]
    assert ws.max_row == 2                      # 沒有長出第二列
    assert _cell(sheet, 2, "主旨") == "甲"
    assert _cell(sheet, 2, "摘要") == "乙"


def test_never_overwrites_user_edits(sheet):
    rs.upsert({"文號": "MWAA1", "摘要": "機器版"}, path=sheet)
    wb = openpyxl.load_workbook(sheet)
    wb.worksheets[0].cell(row=2, column=rs.COLUMNS.index("摘要") + 1, value="我潤過的")
    wb.save(sheet)
    rs.upsert({"文號": "MWAA1", "摘要": "機器版"}, path=sheet)
    assert _cell(sheet, 2, "摘要") == "我潤過的"


def test_overwrite_flag_forces(sheet):
    rs.upsert({"文號": "MWAA1", "摘要": "舊"}, path=sheet)
    rs.upsert({"文號": "MWAA1", "摘要": "新"}, path=sheet, overwrite=True)
    assert _cell(sheet, 2, "摘要") == "新"


def test_none_values_skipped(sheet):
    rs.upsert({"文號": "MWAA1", "主旨": "甲"}, path=sheet)
    rs.upsert({"文號": "MWAA1", "主旨": None}, path=sheet, overwrite=True)
    assert _cell(sheet, 2, "主旨") == "甲"


@pytest.mark.parametrize("val,expect", [
    ("OK", True), ("ok", True), (" O ", True), ("是", True), ("Y", True),
    ("　OK　", True), (None, False), ("", False), ("待確認", False), ("NO", False),
])
def test_is_approved(val, expect):
    assert rs.is_approved(val) is expect


def test_approved_filters_by_gate(sheet):
    rs.upsert([{"文號": "MWAA1", "陳會": "OK"},
               {"文號": "MWAA2", "陳會": "", "張貼": "OK"}], path=sheet)
    assert [r["文號"] for r in rs.approved("陳會", sheet)] == ["MWAA1"]
    assert [r["文號"] for r in rs.approved("張貼", sheet)] == ["MWAA2"]


def test_approved_rejects_bad_gate(sheet):
    rs.upsert({"文號": "MWAA1"}, path=sheet)
    with pytest.raises(ValueError):
        rs.approved("摘要", sheet)


def test_bad_header_raises(tmp_path):
    p = str(tmp_path / "壞表頭.xlsx")
    wb = openpyxl.Workbook()
    wb.active.append(["編號", "標題"])
    wb.save(p)
    with pytest.raises(ValueError):
        rs.upsert({"文號": "MWAA1"}, path=p)


# ── 從公文目錄抽欄位 ───────────────────────────────────────────────────────

CONTENT = """發文日期：中華民國115年7月21日
發文字號：高餐大圖字第1151800055號
主旨：檢送本校圖書館出版主題桌遊相關資訊，請查照。
說明：
一、第一點。
二、第二點，網址 https://example.org 。
附件：
"""

SUMMARY = """#存查分類:資安 03750402
##於官網公告，並轉知各處室及師生
###陳會
####重要消息+處室公告

主旨：檢送本校圖書館出版主題桌遊相關資訊，請查照。

1. 摘要條列一。
"""


def _doc_dir(tmp_path, name="MWAA1156007365"):
    d = tmp_path / name
    d.mkdir()
    (d / "123_456內容.txt").write_text(CONTENT, encoding="utf-8")
    (d / "123_456總結.claude.md").write_text(SUMMARY, encoding="utf-8")
    return str(d)


def test_collect_pulls_four_columns(tmp_path):
    rec = rs.collect(_doc_dir(tmp_path))
    assert rec["文號"] == "MWAA1156007365"
    assert rec["主旨"] == "檢送本校圖書館出版主題桌遊相關資訊，請查照。"
    assert rec["擬辦"] == "於官網公告，並轉知各處室及師生"


def test_collect_summary_is_explanation_verbatim(tmp_path):
    """摘要欄 = 內容.txt「說明」段原文,不含標頭、不到附件。"""
    rec = rs.collect(_doc_dir(tmp_path))
    assert rec["摘要"] == "一、第一點。\n二、第二點，網址 https://example.org 。"
    assert "說明" not in rec["摘要"]
    assert "附件" not in rec["摘要"]
    assert "發文日期" not in rec["摘要"]


def test_collect_handling_ignores_deeper_hashes(tmp_path):
    """### 陳會 / #### 同步分類 不可被當成擬辦。"""
    assert rs.collect(_doc_dir(tmp_path))["擬辦"] == "於官網公告，並轉知各處室及師生"


def test_collect_without_doc_number_returns_none(tmp_path):
    d = tmp_path / "隨便一個資料夾"
    d.mkdir()
    assert rs.collect(str(d)) is None


def test_collect_handles_dated_folder_name(tmp_path):
    """歸檔後的目錄名(1150727_MWAA…【轉知】標題)也要抓得到文號。"""
    rec = rs.collect(_doc_dir(tmp_path, "1150727_MWAA1156007365【轉知】桌遊"))
    assert rec["文號"] == "MWAA1156007365"


def test_sync_writes_all_dirs(tmp_path, sheet):
    base = tmp_path / "work"
    base.mkdir()
    for n in ("MWAA1156000001", "MWAA1156000002"):
        d = base / n
        d.mkdir()
        (d / "1_2內容.txt").write_text(CONTENT, encoding="utf-8")
        (d / "1_2總結.x.md").write_text(SUMMARY, encoding="utf-8")
    added, updated, n_dirs, skipped = rs.sync(scan_dirs=[str(base)], path=sheet)
    assert (added, n_dirs, skipped) == (2, 2, 0)
    assert len(rs.rows(sheet)) == 2


def test_sync_skips_docs_without_summary(tmp_path, sheet):
    """只有來文、還沒跑摘要的空殼不該生列 — 空白列對審核沒幫助。"""
    base = tmp_path / "work"
    base.mkdir()
    done = base / "MWAA1156000001"
    done.mkdir()
    (done / "1_2內容.txt").write_text(CONTENT, encoding="utf-8")
    (done / "1_2總結.x.md").write_text(SUMMARY, encoding="utf-8")
    (base / "MWAA1156000002" / "來文").mkdir(parents=True)

    added, _, n_dirs, skipped = rs.sync(scan_dirs=[str(base)], path=sheet)
    assert (added, n_dirs, skipped) == (1, 2, 1)
    assert [r["文號"] for r in rs.rows(sheet)] == ["MWAA1156000001"]


# ── 「我自己已經辦完了」標記 ───────────────────────────────────────────────

@pytest.mark.parametrize("val,expect", [
    ("已辦", True), ("已公告", True), ("跳過", True), ("done", True),
    ("X", True), ("-", True), ("　已辦 ", True),
    ("OK", False), ("", False), (None, False), ("待確認", False),
])
def test_is_done(val, expect):
    assert rs.is_done(val) is expect


def test_done_and_approved_are_disjoint():
    """已辦 ≠ 放行。兩者混淆會讓「我做過的」被程式再做一次。"""
    for t in rs.DONE_TOKENS:
        assert not rs.is_approved(t), t
    for t in rs.APPROVE_TOKENS:
        assert not rs.is_done(t), t


def test_detect_done_from_marker_files(tmp_path):
    d = tmp_path / "MWAA0001"
    d.mkdir()
    (d / "1_2已公告.txt").write_text("x", encoding="utf-8")
    assert rs._detect_done(str(d)) == {"張貼": rs.DONE_MARK}

    d2 = tmp_path / "MWAA0002"
    d2.mkdir()
    (d2 / "1_2已存查.txt").write_text("x", encoding="utf-8")
    assert rs._detect_done(str(d2)) == {"陳會": rs.DONE_MARK}


def test_detect_done_from_closure_dir(tmp_path):
    """進得了待結案 = 陳核一定跑過了。"""
    base = tmp_path / "document_download_closure"
    d = base / "MWAA0001"
    d.mkdir(parents=True)
    assert rs._detect_done(str(d))["陳會"] == rs.DONE_MARK


def test_detect_done_absent_for_plain_dir(tmp_path):
    d = tmp_path / "document_download" / "MWAA0001"
    d.mkdir(parents=True)
    assert rs._detect_done(str(d)) == {}


def test_auto_done_never_overwrites_user_ok(tmp_path, sheet):
    """使用者已經打了 OK,自動偵測不可以把它改成「已辦」。"""
    base = tmp_path / "document_download_closure"
    d = base / "MWAA0001"
    d.mkdir(parents=True)
    (d / "1_2內容.txt").write_text(CONTENT, encoding="utf-8")
    (d / "1_2總結.x.md").write_text(SUMMARY, encoding="utf-8")
    rs.upsert({"文號": "MWAA0001", "陳會": "OK"}, path=sheet)
    rs.sync(scan_dirs=[str(base)], path=sheet)
    assert _cell(sheet, 2, "陳會") == "OK"


# ── 文號超連結到資料夾（2026-07-28）───────────────────────────────────────

def test_doc_no_cell_links_to_folder(tmp_path, sheet):
    base = tmp_path / "document_download"
    d = base / "MWAA0001"
    d.mkdir(parents=True)
    (d / "1_2內容.txt").write_text(CONTENT, encoding="utf-8")
    (d / "1_2總結.x.md").write_text(SUMMARY, encoding="utf-8")
    rs.sync(scan_dirs=[str(base)], path=sheet)
    ws = openpyxl.load_workbook(sheet).worksheets[0]
    cell = ws.cell(row=2, column=1)
    assert cell.hyperlink is not None
    assert cell.hyperlink.target.startswith("file:///")
    assert "MWAA0001" in cell.hyperlink.target


def test_no_hyperlink_when_folder_missing(sheet):
    rs.upsert({"文號": "MWAA9999", "主旨": "甲"}, path=sheet)
    ws = openpyxl.load_workbook(sheet).worksheets[0]
    assert ws.cell(row=2, column=1).hyperlink is None


def test_private_keys_not_written_as_columns(tmp_path, sheet):
    """_dir 這種內部欄位不可以被當成表格欄寫進去。"""
    rs.upsert({"文號": "MWAA0001", "_dir": str(tmp_path), "主旨": "甲"}, path=sheet)
    ws = openpyxl.load_workbook(sheet).worksheets[0]
    assert [c.value for c in ws[1]] == rs.COLUMNS
    assert _cell(sheet, 2, "主旨") == "甲"
