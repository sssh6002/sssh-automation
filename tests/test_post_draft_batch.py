# -*- coding: utf-8 -*-
"""批次送陳核 post_draft_batch 的把關測試。

送陳核收不回來，所以這裡釘的都是「什麼情況下**不可以**送」。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import post_draft_batch as pdb  # noqa: E402
import review_sheet as rs  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")

DEFAULT = "一、於學校公告欄公佈周知。二、文存備查。"


# ── 擬辦文字整理 ───────────────────────────────────────────────────────────

def test_strips_leading_yi_prefix():
    """審核表可能寫成「擬：…」,但模板自己會加「擬:」,不能重複。"""
    assert pdb.clean_fragment("擬：公告於本校電子公佈欄，文存備查") == (
        "公告於本校電子公佈欄，文存備查", None)
    assert pdb.clean_fragment("擬:公告周知")[0] == "公告周知"


def test_strips_suggestion_hint():
    """（建議轉知：○○教師）是給承辦人看的提示,不該送進 edoc。"""
    frag, hint = pdb.clean_fragment(f"{DEFAULT}（建議轉知：生活科技教師）")
    assert frag == DEFAULT
    assert hint == "（建議轉知：生活科技教師）"


def test_plain_text_untouched():
    assert pdb.clean_fragment(DEFAULT) == (DEFAULT, None)


def test_clean_fragment_handles_empty():
    assert pdb.clean_fragment(None) == ("", None)
    assert pdb.clean_fragment("   ") == ("", None)


def test_needs_human_detects_placeholder():
    assert pdb.needs_human("（請自行填寫：本案為回覆本校申請）")
    assert pdb.needs_human("(請自行填寫)")
    assert not pdb.needs_human(DEFAULT)


# ── plan() 的擋下條件 ──────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    work = tmp_path / "document_download"
    work.mkdir()
    monkeypatch.setattr(rs, "SCAN_DIRS", [str(work)])
    monkeypatch.setattr(pdb.rs, "SCAN_DIRS", [str(work)])
    return work, str(tmp_path / "表.xlsx")


def _doc(work, no):
    d = work / no
    d.mkdir()
    return d


def test_only_approved_rows_are_planned(env):
    work, sheet = env
    for no in ("MWAA0001", "MWAA0002"):
        _doc(work, no)
    rs.upsert([{"文號": "MWAA0001", "擬辦": DEFAULT, "陳會": "OK"},
               {"文號": "MWAA0002", "擬辦": DEFAULT, "陳會": ""}], path=sheet)
    ready, blocked = pdb.plan(sheet)
    assert [r["文號"] for r in ready] == ["MWAA0001"]
    assert blocked == []


def test_blocks_when_placeholder_left(env):
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "擬辦": "（請自行填寫：本案為回覆本校申請）",
               "陳會": "OK"}, path=sheet)
    ready, blocked = pdb.plan(sheet)
    assert ready == []
    assert "請自行填寫" in blocked[0]["擋下原因"]


def test_blocks_when_draft_blank(env):
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "陳會": "OK"}, path=sheet)
    ready, blocked = pdb.plan(sheet)
    assert ready == []
    assert "空白" in blocked[0]["擋下原因"]


def test_blocks_when_dir_missing(env):
    _, sheet = env
    rs.upsert({"文號": "MWAA9999", "擬辦": DEFAULT, "陳會": "OK"}, path=sheet)
    ready, blocked = pdb.plan(sheet)
    assert ready == []
    assert "找不到公文目錄" in blocked[0]["擋下原因"]


def test_blocks_when_already_sent(env):
    work, sheet = env
    d = _doc(work, "MWAA0001")
    (d / "MWAA0001已陳核.txt").write_text("x", encoding="utf-8")
    rs.upsert({"文號": "MWAA0001", "擬辦": DEFAULT, "陳會": "OK"}, path=sheet)
    ready, blocked = pdb.plan(sheet)
    assert ready == []
    assert "先前送過" in blocked[0]["擋下原因"]


def test_ready_item_carries_cleaned_text(env):
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "陳會": "OK",
               "擬辦": f"擬：{DEFAULT}（建議轉知：資訊教師）"}, path=sheet)
    ready, _ = pdb.plan(sheet)
    assert ready[0]["送出文字"] == DEFAULT
    assert ready[0]["移除的提示"] == "（建議轉知：資訊教師）"


def test_dated_folder_name_still_resolves(env):
    """歸檔式命名 1150727_MWAA…【轉知】標題 也要找得到。"""
    work, sheet = env
    _doc(work, "1150727_MWAA0001【轉知】桌遊")
    rs.upsert({"文號": "MWAA0001", "擬辦": DEFAULT, "陳會": "OK"}, path=sheet)
    ready, blocked = pdb.plan(sheet)
    assert len(ready) == 1


# ── 模板不可再接「陳閱後文存查」 ───────────────────────────────────────────

def test_template_does_not_append_chenyue():
    """2026-07-28 修:模板結尾接「，陳閱後文存查。」會與承辦文字的「文存備查。」打架。"""
    from fill_in_draft import _load_config, _render
    default, template = _load_config()
    assert "陳閱後文存查" not in template
    out = _render(template, DEFAULT)
    assert out.strip() == f"擬:\n{DEFAULT}"
    assert "文存查" not in out.replace("文存備查", "")


def test_config_default_fragment_is_the_house_default():
    from fill_in_draft import _load_config
    default, _ = _load_config()
    assert default["承辦文字"] == DEFAULT
    assert default["動作"] == "none"      # fallback 不可自動送簽


# ── 陳核路徑判斷:以發文字別為主（2026-07-29）─────────────────────────────

def test_flags_loaded_from_yaml():
    f = pdb.routing_flags()
    assert "北市教資" in f["本人字別"]
    assert "北市教中" in f["他人字別"]
    assert "陳核路徑" in f["說明"]


def test_doc_prefix_extracted():
    assert pdb.doc_prefix("發文字號：北市教中字第1153079946號") == "北市教中"
    assert pdb.doc_prefix("發文字號：高餐大圖字第1151800055號") == "高餐大圖"
    assert pdb.doc_prefix("沒有字號") is None


@pytest.mark.parametrize("prefix,stop", [
    ("北市教資", False),      # 資訊教育科 — 我的
    ("北市教職", False),      # 職業教育科（無人機屬廣義資訊設備）— 我的
    ("臺教資(一)", False),
    ("北市教中", True),       # 中等教育科 — 原圖資老師業務
    ("北市圖諮", True),       # 臺北市立圖書館
])
def test_prefix_decides(prefix, stop):
    assert pdb.routing_hit("", prefix=prefix)[0] is stop


def test_sender_own_library_unit_not_mistaken():
    """高餐大圖 = 高雄餐旅大學圖書館,是來文機關自己的單位,與本校誰承辦無關。"""
    stop, why = pdb.routing_hit("檢送本校圖書館出版主題桌遊", prefix="高餐大圖")
    assert stop is False, why


def test_topic_beats_own_prefix():
    """業務按主題分,不按來文機關分。

    實例:MWAA1156007510「數位學生證結合圖書館借閱證」發文字別是本人的
    「北市教資」,但學生證業務屬圖資老師 —— 只看字別會判錯。
    """
    stop, why = pdb.routing_hit("數位學生證結合市立圖書館借閱證推廣計畫",
                                prefix="北市教資")
    assert stop is True
    assert "學生證" in why or "借閱證" in why


@pytest.mark.parametrize("subject", [
    "有關貴校申請本館行動展乙案，本館敬表同意",
    "檢送本校書展活動相關資訊",
    "國立臺灣文學館辦理巡迴展",
])
def test_exhibition_topics_belong_to_other(subject):
    assert pdb.routing_hit(subject)[0] is True


@pytest.mark.parametrize("subject", [
    "全國高中職程式設計競賽HSPC 2026",
    "115學年度推動高級中等學校無人機設備計畫",
    "AI機器人開發實務研習營",
])
def test_information_and_equipment_stay_mine(subject):
    """資訊競賽與設備是本人業務,不可被擋。"""
    assert pdb.routing_hit(subject, prefix="北市教資")[0] is False


def test_keyword_used_when_prefix_unknown():
    stop, why = pdb.routing_hit("辦理閱讀心得寫作比賽", prefix="某大學教務")
    assert stop is True and "閱讀心得" in why


def test_no_prefix_falls_back_to_keyword():
    assert pdb.routing_hit("借閱證推廣計畫")[0] is True
    assert pdb.routing_hit("程式設計競賽")[0] is False


def test_flagged_doc_blocked_even_when_approved(env):
    """打了 OK 也不送 —— 業務移交後 edoc 看不出代理,只有承辦人知道。"""
    work, sheet = env
    d = _doc(work, "MWAA0001")
    # 主旨刻意不含任何關鍵字 —— 這條測的是「純靠發文字別」也擋得住
    (d / "1_2內容.txt").write_text(
        "發文字號：北市教中字第1153079946號\n主旨：地球科學教材開發計畫研習",
        encoding="utf-8")
    rs.upsert({"文號": "MWAA0001", "主旨": "地球科學教材開發計畫研習",
               "擬辦": DEFAULT, "陳會": "OK"}, path=sheet)
    ready, blocked = pdb.plan(sheet)
    assert ready == []
    assert "北市教中" in blocked[0]["擋下原因"]


def test_own_business_still_passes(env):
    work, sheet = env
    d = _doc(work, "MWAA0001")
    (d / "1_2內容.txt").write_text(
        "發文字號：北市教資字第1153076269號\n主旨：酷課APP", encoding="utf-8")
    rs.upsert({"文號": "MWAA0001", "主旨": "酷課APP人文拾光",
               "擬辦": DEFAULT, "陳會": "OK"}, path=sheet)
    assert [r["文號"] for r in pdb.plan(sheet)[0]] == ["MWAA0001"]


# ── 被退過的公文不自動送（2026-07-29）─────────────────────────────────────

OPINION_RETURNED = """公文文號：1156007093
★意見欄
1.松山高中 松山高中各組室 幹事 李潔 送陳/會 115/04/16 10:25:13
擬：一、於學校公告欄公佈周知。二、文存備查。
3.松山高中 松山高中各組室 圖書館主任 蔡伊涵 退文 115/04/17 15:27:11
"""

OPINION_CLEAN = """公文文號：1156006707
★意見欄
1.松山高中 松山高中各組室 幹事 李潔 送陳/會 115/04/16 10:25:13
擬：一、於學校公告欄公佈周知。二、文存備查。
"""


def _fake_opinion(monkeypatch, text):
    """把 pypdf 換掉,不必真的做一個 PDF。"""
    class _Page:
        def extract_text(self):
            return text

    class _Reader:
        def __init__(self, *a, **k):
            self.pages = [_Page()]

    import pypdf
    monkeypatch.setattr(pypdf, "PdfReader", _Reader)


def test_returned_doc_detected(tmp_path, monkeypatch):
    _fake_opinion(monkeypatch, OPINION_RETURNED)
    d = tmp_path / "MWAA0001"
    d.mkdir()
    (d / "1_1_x(opinion).pdf").write_bytes(b"x")
    ok, line = pdb.was_returned(str(d))
    assert ok is True
    assert "退文" in line and "圖書館主任" in line


def test_clean_doc_not_flagged(tmp_path, monkeypatch):
    _fake_opinion(monkeypatch, OPINION_CLEAN)
    d = tmp_path / "MWAA0001"
    d.mkdir()
    (d / "1_1_x(opinion).pdf").write_bytes(b"x")
    assert pdb.was_returned(str(d)) == (False, None)


def test_no_opinion_file_is_not_returned(tmp_path):
    d = tmp_path / "MWAA0001"
    d.mkdir()
    assert pdb.was_returned(str(d)) == (False, None)


def test_returned_doc_blocked_even_when_approved(env, monkeypatch):
    """被退過 = 有人對擬辦有意見。同一份再送一次只會再被退。"""
    _fake_opinion(monkeypatch, OPINION_RETURNED)
    work, sheet = env
    d = _doc(work, "MWAA0001")
    (d / "1_1_x(opinion).pdf").write_bytes(b"x")
    (d / "1_2內容.txt").write_text("發文字號：北市教資字第1號\n主旨：資訊課綱意見調查",
                                  encoding="utf-8")
    rs.upsert({"文號": "MWAA0001", "主旨": "資訊課綱課程推動意見調查",
               "擬辦": DEFAULT, "陳會": "OK"}, path=sheet)
    ready, blocked = pdb.plan(sheet)
    assert ready == []
    assert "被退過" in blocked[0]["擋下原因"]
