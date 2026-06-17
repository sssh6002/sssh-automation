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
    p = pw._write_posted_marker(str(d), "測試主旨", "圖書館公告", ["課外活動", "硏習資訊"])
    assert p is not None and (d / f"{base}已公告.txt").is_file()
    lines = (d / f"{base}已公告.txt").read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("_已公告。")
    assert lines[1] == "主旨:測試主旨"
    assert lines[2] == "公告於:圖書館公告。同步顯示於:課外活動+硏習資訊。"
    assert pw._already_posted(str(d)) is True


def test_write_posted_marker_empty_cats_shows_none(tmp_path):
    # 沒選任何同步分類 → 「同步顯示於:無。」
    d, base = _make_doc_dir(tmp_path)
    pw._write_posted_marker(str(d), "標題", "圖書館公告", [])
    content = (d / f"{base}已公告.txt").read_text(encoding="utf-8")
    assert content.splitlines()[2] == "公告於:圖書館公告。同步顯示於:無。"


def _doc_dir_with_summary(tmp_path, summary_text, base="12345_678"):
    d = tmp_path / "MWAA_s"; d.mkdir()
    (d / f"{base}總結.gemini.md").write_text(summary_text, encoding="utf-8")
    (d / f"{base}內容.txt").write_text("x", encoding="utf-8")
    return d, base


def test_maybe_post_skips_when_not_triggered(tmp_path, monkeypatch):
    d, _ = _doc_dir_with_summary(tmp_path, OLD_FORMAT)  # 承辦文字「不參加」
    called = {"login": False}
    monkeypatch.setattr(pw, "_open_and_login_sssh", lambda drv: called.__setitem__("login", True) or True)
    assert pw.maybe_post_announcement(object(), str(d)) is False
    assert called["login"] is False  # 沒觸發 → 連登入都不做


def test_maybe_post_publishes_when_triggered(tmp_path, monkeypatch):
    import taipeion_login_selenium
    d, base = _doc_dir_with_summary(tmp_path, NEW_FORMAT)  # 含「於官網公告」
    rec = {}
    # 不依賴真實 env.env:固定回傳發布單位
    monkeypatch.setattr(taipeion_login_selenium, "_read_config",
                        lambda key: "系管師群組" if key == "sssh_publish_unit" else None)
    monkeypatch.setattr(pw, "_open_and_login_sssh",
                        lambda drv: rec.__setitem__("login", True) or True)
    # _submit_announcement 現回傳 dict(selected_cats / board_name),非 bool
    monkeypatch.setattr(pw, "_submit_announcement",
                        lambda drv, t, b, *a: rec.update(title=t, body=b)
                        or {"selected_cats": ["課外活動"], "board_name": "圖書館公告"})
    assert pw.maybe_post_announcement(object(), str(d)) is True
    assert rec["login"] is True
    assert rec["title"].startswith("請貴校加強")
    assert rec["body"].startswith("1. 近期發現")
    marker = d / f"{base}已公告.txt"
    assert marker.is_file()  # 成功後寫標記
    assert "公告於:圖書館公告。同步顯示於:課外活動。" in marker.read_text(encoding="utf-8")


def test_maybe_post_skips_when_already_posted(tmp_path, monkeypatch):
    d, base = _doc_dir_with_summary(tmp_path, NEW_FORMAT)
    (d / f"{base}已公告.txt").write_text("2026-06-05T00:00:00_已公告", encoding="utf-8")
    called = {"login": False}
    monkeypatch.setattr(pw, "_open_and_login_sssh", lambda drv: called.__setitem__("login", True) or True)
    assert pw.maybe_post_announcement(object(), str(d)) is True
    assert called["login"] is False


def test_already_posted_matches_any_marker_glob(tmp_path):
    # 使用者規則:夾內任何 *已公告.txt(即使檔名對不上主檔名)即視為已公告。
    d, base = _make_doc_dir(tmp_path)  # 主檔名 base=12345_678
    assert pw._already_posted(str(d)) is False
    (d / "公文已公告.txt").write_text("手動標記免公告", encoding="utf-8")  # 名稱≠主檔名
    assert pw._already_posted(str(d)) is True


def test_already_posted_false_on_missing_dir(tmp_path):
    assert pw._already_posted(str(tmp_path / "不存在")) is False


def test_maybe_post_skips_when_marker_name_differs(tmp_path, monkeypatch):
    # 觸發公告但夾內已有不同檔名的 *已公告.txt → 仍應跳過、不登入發佈。
    d, _ = _doc_dir_with_summary(tmp_path, NEW_FORMAT)
    (d / "前次已公告.txt").write_text("x", encoding="utf-8")
    called = {"login": False}
    monkeypatch.setattr(pw, "_open_and_login_sssh",
                        lambda drv: called.__setitem__("login", True) or True)
    assert pw.maybe_post_announcement(object(), str(d)) is True
    assert called["login"] is False


def test_ledger_csv_append_and_read_roundtrip(tmp_path):
    import csv as _csv
    d, _ = _make_doc_dir(tmp_path)  # tmp_path/MWAA_x;清冊在上層 tmp_path
    assert pw._ledger_doc_nos(str(d)) == set()
    pw._append_to_ledger(str(d), "MWAA999", "標題,含逗號與「引號」", "圖書館公告", ["課外活動", "硏習資訊"])
    assert "MWAA999" in pw._ledger_doc_nos(str(d))
    ledger = tmp_path / "_已公告清單.csv"
    assert ledger.is_file()
    # 用 csv 正確解析:表頭 + 1 列資料;公文檔號在第 1 欄、含逗號的主旨完整保留
    with open(ledger, encoding="utf-8-sig", newline="") as f:
        rows = list(_csv.reader(f))
    assert rows[0] == ["公文檔號", "時間", "主旨", "公告於", "同步顯示於"]
    assert rows[1][0] == "MWAA999"
    assert rows[1][2] == "標題,含逗號與「引號」"
    assert rows[1][3] == "圖書館公告"
    assert rows[1][4] == "課外活動+硏習資訊"


def test_backfill_ledger_parses_marker_content(tmp_path):
    # 補登時解析 *已公告.txt 三行格式,把主旨/公告於/同步顯示於一併寫入 CSV。
    a = tmp_path / "MWAA_A"; a.mkdir()
    (a / "x已公告.txt").write_text(
        "2026-06-13T13:26:33_已公告。\n主旨:測試主旨\n公告於:圖書館公告。同步顯示於:課外活動+硏習資訊。",
        encoding="utf-8")
    (tmp_path / "MWAA_B").mkdir()  # 無標記
    pw._backfill_ledger_from_markers(str(a))
    nos = pw._ledger_doc_nos(str(a))
    assert "MWAA_A" in nos and "MWAA_B" not in nos
    import csv as _csv
    with open(tmp_path / "_已公告清單.csv", encoding="utf-8-sig", newline="") as f:
        rows = [r for r in _csv.reader(f) if r and r[0] == "MWAA_A"]
    assert len(rows) == 1  # 冪等前提:一筆
    assert rows[0] == ["MWAA_A", "2026-06-13T13:26:33", "測試主旨", "圖書館公告", "課外活動+硏習資訊"]
    pw._backfill_ledger_from_markers(str(a))  # 再跑不重複
    with open(tmp_path / "_已公告清單.csv", encoding="utf-8-sig", newline="") as f:
        again = [r for r in _csv.reader(f) if r and r[0] == "MWAA_A"]
    assert len(again) == 1


def test_maybe_post_skips_when_in_ledger_without_marker(tmp_path, monkeypatch):
    # 核心情境:之前已公告(登錄清冊),這次公文夾是重新下載的全新目錄、無 *已公告.txt。
    d, _ = _doc_dir_with_summary(tmp_path, NEW_FORMAT)
    pw._append_to_ledger(str(d), pw._doc_no_of(str(d)), "前次已公告", "圖書館公告", ["課外活動"])
    called = {"login": False}
    monkeypatch.setattr(pw, "_open_and_login_sssh",
                        lambda drv: called.__setitem__("login", True) or True)
    assert pw.maybe_post_announcement(object(), str(d)) is True
    assert called["login"] is False  # 清冊命中 → 不重複公告


def test_maybe_post_appends_ledger_on_success(tmp_path, monkeypatch):
    import taipeion_login_selenium
    d, _ = _doc_dir_with_summary(tmp_path, NEW_FORMAT)
    monkeypatch.setattr(taipeion_login_selenium, "_read_config",
                        lambda key: "系管師群組" if key == "sssh_publish_unit" else None)
    monkeypatch.setattr(pw, "_open_and_login_sssh", lambda drv: True)
    monkeypatch.setattr(pw, "_submit_announcement",
                        lambda drv, t, b, *a: {"selected_cats": ["課外活動"], "board_name": "圖書館公告"})
    assert pw.maybe_post_announcement(object(), str(d)) is True
    assert pw._doc_no_of(str(d)) in pw._ledger_doc_nos(str(d))  # 成功後已登錄清冊


def test_maybe_post_returns_false_and_no_marker_when_submit_fails(tmp_path, monkeypatch):
    d, base = _doc_dir_with_summary(tmp_path, NEW_FORMAT)
    monkeypatch.setattr(pw, "_open_and_login_sssh", lambda drv: True)
    monkeypatch.setattr(pw, "_submit_announcement", lambda drv, t, b, *a: False)
    assert pw.maybe_post_announcement(object(), str(d)) is False
    assert not (d / f"{base}已公告.txt").exists()


def test_maybe_post_returns_false_when_login_fails(tmp_path, monkeypatch):
    d, base = _doc_dir_with_summary(tmp_path, NEW_FORMAT)
    submit_called = {"v": False}
    monkeypatch.setattr(pw, "_open_and_login_sssh", lambda drv: False)
    monkeypatch.setattr(pw, "_submit_announcement",
                        lambda drv, t, b, *a: submit_called.__setitem__("v", True) or True)
    assert pw.maybe_post_announcement(object(), str(d)) is False
    assert submit_called["v"] is False
    assert not (d / f"{base}已公告.txt").exists()


def test_parse_summary_extracts_category():
    # NEW_FORMAT 首行 #存查分類:資安 03750402
    assert _parse_summary_text(NEW_FORMAT)["category"] == "資安"
    text = "#存查分類:研習 03750401\n##於官網公告\n主旨：標題\n1. 內容。\n"
    assert _parse_summary_text(text)["category"] == "研習"


def test_parse_summary_category_none_when_absent():
    text = "##於官網公告\n主旨：標題\n1. 內容。\n"
    assert _parse_summary_text(text)["category"] is None


def test_parse_sync_categories_from_quad_hash():
    # #### 校網同步顯示行 → 以 + 分隔的分類清單(取代舊單值邏輯)
    text = ("#存查分類:研習 03750401\n##於官網公告\n###陳會\n"
            "####課外活動+硏習資訊\n主旨：標題\n1. 內容。\n")
    assert _parse_summary_text(text)["sync_categories"] == ["課外活動", "硏習資訊"]
    # #### 空白 → 不選
    text2 = "#存查分類:資安\n##於官網公告\n###陳會\n####\n主旨：標題\n1. 內容。\n"
    assert _parse_summary_text(text2)["sync_categories"] == []
    # 完全沒有 #### 行 → []
    assert _parse_summary_text(NEW_FORMAT)["sync_categories"] == []


def test_find_attachments_matches_attch_files(tmp_path):
    import os as _os
    d = tmp_path / "doc"; d.mkdir()
    (d / "123_456.pdf").write_text("x", encoding="utf-8")
    (d / "123_456_ATTCH1.pdf").write_text("x", encoding="utf-8")
    (d / "123_456_ATTCH2.pdf").write_text("x", encoding="utf-8")
    (d / "123_456內容.txt").write_text("x", encoding="utf-8")
    got = pw._find_attachments(str(d))
    assert len(got) == 2
    assert all("ATTCH" in _os.path.basename(g) for g in got)
    assert all(_os.path.isabs(g) for g in got)
