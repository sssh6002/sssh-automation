"""document_closure_post_web 純函式測試（解析總結.md + 觸發判定）。

只測不需瀏覽器的部分：_parse_summary_text / _parse_summary / _should_post。
登入與發佈為 live Selenium，於實機驗證。
"""
import pathlib

from document_closure.document_closure_post_web import (
    _parse_summary_text,
    _parse_summary,
    _should_post,
    ANNOUNCE_KEYWORD,
)

# 新格式（2026-05 後 gemini 產出）：主旨無 * 前綴、有 ### 承辦方式、body 為 1. 條列
NEW_FORMAT = """#存查分類:資安 03750402
##於官網公告，並轉知各處室及師生
###陳會

發文日期：中華民國115年5月20日
發文字號：臺教國署高字第1155402124A號
主旨：請貴校加強校務行政系統帳號密碼及個人資料安全宣導，請查照。

1. 近期發現非授權第三方校務查詢 APP。
2. 學校應宣導學生僅能使用官方授權系統。
3. 學校須與系統廠商檢視是否有異常存取情事。
"""

# 舊格式：主旨有 * 前綴、承辦文字不含於官網公告、body 用 "1. "
OLD_FORMAT = """#存查分類:研習 03750401
##不參加

*發文日期：中華民國115年5月22日
*發文字號：台灣資安字第1150050003號
*主旨：本協會訂於115年6月12日舉辦線上課程，敬邀報名參加，請查照。

1. 115年6月12日辦理資安AI攻防線上課程。
2. 6月8日前線上報名，需繳費。
"""


def test_parse_new_format_handling_title_body():
    r = _parse_summary_text(NEW_FORMAT)
    assert r is not None
    assert r["handling"] == "於官網公告，並轉知各處室及師生"
    assert r["title"] == "請貴校加強校務行政系統帳號密碼及個人資料安全宣導，請查照。"
    assert r["body"] == (
        "1. 近期發現非授權第三方校務查詢 APP。\n"
        "2. 學校應宣導學生僅能使用官方授權系統。\n"
        "3. 學校須與系統廠商檢視是否有異常存取情事。"
    )


def test_parse_old_format_with_star_prefix():
    r = _parse_summary_text(OLD_FORMAT)
    assert r is not None
    assert r["handling"] == "不參加"
    assert r["title"] == "本協會訂於115年6月12日舉辦線上課程，敬邀報名參加，請查照。"
    assert r["body"].startswith("1. 115年6月12日辦理資安AI攻防線上課程。")
    assert "2. 6月8日前線上報名，需繳費。" in r["body"]


def test_handling_is_first_double_hash_not_triple():
    # ## 在 ### 之前，承辦文字取 ## 那行（不是 ### 承辦方式）
    r = _parse_summary_text(NEW_FORMAT)
    assert r["handling"] != "陳會"


def test_subject_fullwidth_and_halfwidth_colon():
    half = "##於官網公告\n主旨: 半形冒號標題\n1. 內容一。\n"
    full = "##於官網公告\n主旨：全形冒號標題\n1. 內容一。\n"
    assert _parse_summary_text(half)["title"] == "半形冒號標題"
    assert _parse_summary_text(full)["title"] == "全形冒號標題"


def test_body_accepts_dunhao_delimiter_and_no_space():
    text = "##於官網公告\n主旨：標題\n1、第一點。\n2.第二點。\n"
    r = _parse_summary_text(text)
    assert r["body"] == "1、第一點。\n2.第二點。"


def test_no_subject_returns_none():
    text = "#存查分類:資安 03750402\n##於官網公告\n1. 沒有主旨行。\n"
    assert _parse_summary_text(text) is None


def test_no_body_returns_none():
    text = "##於官網公告\n主旨：只有主旨沒有條列。\n"
    assert _parse_summary_text(text) is None


def test_no_handling_line_still_parses_with_none_handling():
    text = "主旨：沒有承辦文字行的公文。\n1. 內容一。\n"
    r = _parse_summary_text(text)
    assert r is not None
    assert r["handling"] is None
    assert r["title"] == "沒有承辦文字行的公文。"


def test_should_post_true_when_handling_contains_keyword():
    assert ANNOUNCE_KEYWORD == "於官網公告"
    assert _should_post({"handling": "於官網公告，並轉知各處室及師生"}) is True
    assert _should_post({"handling": "於官網公告"}) is True


def test_should_post_false_when_keyword_absent_or_none():
    assert _should_post({"handling": "參加線上研習"}) is False
    assert _should_post({"handling": None}) is False
    assert _should_post(None) is False


def test_parse_summary_reads_file_from_dir(tmp_path):
    d = tmp_path / "MWAA1156005154"
    d.mkdir()
    (d / "28694062_1155402124A總結.gemini-3-flash-preview.md").write_text(
        NEW_FORMAT, encoding="utf-8")
    r = _parse_summary(str(d))
    assert r is not None
    assert r["handling"] == "於官網公告，並轉知各處室及師生"


def test_parse_summary_missing_file_returns_none(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert _parse_summary(str(d)) is None


import document_closure.document_closure_post_web as pw
from document_closure.document_closure_post_web import _body_to_html


def test_body_to_html_one_paragraph_per_line():
    assert _body_to_html("1. 甲\n2. 乙") == "<p>1. 甲</p><p>2. 乙</p>"


def test_body_to_html_skips_blank_lines():
    assert _body_to_html("1. 甲\n\n2. 乙\n") == "<p>1. 甲</p><p>2. 乙</p>"


def test_body_to_html_escapes_html_chars():
    assert _body_to_html("a < b & c") == "<p>a &lt; b &amp; c</p>"


def _make_doc_dir(tmp_path, base="12345_678"):
    d = tmp_path / "MWAA_x"
    d.mkdir()
    (d / f"{base}內容.txt").write_text("x", encoding="utf-8")  # 供 _find_main_doc_basename
    return d, base


def test_posted_marker_path_uses_main_basename(tmp_path):
    d, base = _make_doc_dir(tmp_path)
    assert pw._posted_marker_path(str(d)).endswith(f"{base}已公告.txt")


def test_posted_marker_path_none_when_no_main_doc(tmp_path):
    d = tmp_path / "empty"; d.mkdir()
    assert pw._posted_marker_path(str(d)) is None


def test_write_and_detect_posted_marker(tmp_path):
    d, base = _make_doc_dir(tmp_path)
    assert pw._already_posted(str(d)) is False
    p = pw._write_posted_marker(str(d))
    assert p is not None and (d / f"{base}已公告.txt").is_file()
    assert (d / f"{base}已公告.txt").read_text(encoding="utf-8").endswith("_已公告")
    assert pw._already_posted(str(d)) is True
