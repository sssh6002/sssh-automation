# -*- coding: utf-8 -*-
"""送外部 AI 前的把關測試。

兩個方向都要測:
  - 該擋的要擋（個資外流是法遵問題，不是體驗問題）
  - **不該擋的絕對不能擋**（公文本來就充滿公務 email 與市話，
    濫報會讓整套工具停擺，比漏報更快讓人放棄使用）
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from content_guard import (  # noqa: E402
    has_pii, is_valid_tw_id, pii_report, scan_pii, verify_links,
)


# ── 身分證字號檢查碼 ───────────────────────────────────────────────────────

def test_real_id_passes_checksum():
    assert is_valid_tw_id("A123456789") is True


def test_course_code_rejected_by_checksum():
    """實測誤判:Q115280027 是產業新尖兵課程代碼,格式像身分證但檢查碼不過。"""
    assert is_valid_tw_id("Q115280027") is False
    assert has_pii("課程代碼：Q115280027，請於期限前報名。") is False


@pytest.mark.parametrize("bad", ["", None, "A12345678", "A323456789", "1123456789",
                                 "A1234567890"])
def test_malformed_ids_rejected(bad):
    assert is_valid_tw_id(bad) is False


def test_valid_id_is_flagged():
    hits = scan_pii("身分證字號 A123456789 請核對。")
    assert [h[0] for h in hits] == ["身分證字號"]


# ── 不該擋的（濫報比漏報更致命）─────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "電子郵件：meggroup2011@gmail.com",
    "聯絡人：李宥熏　電話：(02)27333141",
    "本案聯絡人：本館展示組陳小姐，電話：(06)221-7201分機2315",
    "請洽高雄餐旅大學員生消費合作社，電話：07-806-0505 分機50003",
    "投稿網址為中學生網站 https://www.shs.edu.tw",
    "檢送本校圖書館出版主題桌遊相關資訊，請查照。",
    "另請參與領航計畫學校務必指派主任或教師帶領學生參與。",
])
def test_ordinary_official_text_not_flagged(text):
    assert has_pii(text) is False, text


# ── 該擋的 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,label", [
    ("本校211吳承翰同學得獎，待收到卡片後轉交學生。", "學生班級姓名"),
    ("三年五班王小明代表本校參賽。", "學生班級姓名"),
    ("學號：410912345", "學號"),
    ("聯絡電話：0937-567-677", "手機號碼"),
    ("民國95年3月2日生", "出生日期"),
    ("家長：陳大明", "家戶資訊"),
    ("檢附身心障礙手冊影本", "健康資訊"),
    ("低收入戶學生免繳費用", "弱勢身分"),
])
def test_personal_data_is_flagged(text, label):
    hits = scan_pii(text)
    assert label in [h[0] for h in hits], (text, hits)


def test_roster_attachment_filename_flagged():
    hits = scan_pii("一般公文內容。", filenames=["得獎名單.pdf", "計畫書.pdf"])
    assert [h[0] for h in hits] == ["名單型附件"]
    assert "得獎名單.pdf" in hits[0][1]


def test_duplicate_hits_reported_once():
    hits = scan_pii("0937-567-677 聯絡人 0937-567-677")
    assert len(hits) == 1


def test_report_is_human_readable():
    r = pii_report(scan_pii("聯絡電話：0937-567-677"))
    assert "未送 AI" in r
    assert "手機號碼" in r
    assert "不保證完整" in r          # 誠實揭露侷限


def test_report_empty_when_clean():
    assert pii_report([]) == ""


# ── 連結查核（來文沒有的網址不能出現在公告）─────────────────────────────────

SRC = "投稿請至中學生網站 https://www.shs.edu.tw 完成。詳見 https://lic.nkuht.edu.tw/p/412-1007-6798.php"


def test_links_from_source_pass():
    assert verify_links(SRC, "報名網址 https://www.shs.edu.tw") == []


def test_injected_link_detected():
    """來文 PDF 若被植入指令改網址,產出的連結對不上來文 → 必須被抓出來。"""
    bad = verify_links(SRC, "報名請至 https://evil.example.com/form")
    assert bad == ["https://evil.example.com/form"]


def test_trailing_punctuation_tolerated():
    assert verify_links(SRC, "詳見 https://www.shs.edu.tw／。") == [] or \
           verify_links(SRC, "詳見 https://www.shs.edu.tw。") == []


def test_domain_only_shortening_tolerated():
    assert verify_links(SRC, "詳見 https://lic.nkuht.edu.tw") == []


def test_no_links_anywhere():
    assert verify_links("純文字來文", "純文字公告") == []


def test_roc_year_not_mistaken_for_class():
    """實測誤判:「115年數位學生證結合圖書館借閱證」被當成年班姓名。"""
    assert has_pii("轉知本市市立圖書館「115年數位學生證結合市立圖書館借閱證推廣計畫」") is False
    assert has_pii("辦理116年閱讀推廣活動") is False


def test_real_class_and_name_still_caught():
    assert has_pii("三年五班王小明") is True
    assert has_pii("本校211吳承翰同學") is True


# ── 公文頭的來文單位聯絡手機(2026-11-05 實測誤判 MWAA1156011463)──────────────
# 小型協會常只留手機當公務聯絡電話。發文機關的「承辦人／電話」欄位在「受文者」
# 之前,那是公務聯絡資訊,不是個資。只放過公文頭;說明段、附件裡的手機照擋。

HEADER_WITH_MOBILE = (
    "中華民國資訊聯盟協進會 函\n"
    "地址：100台北巿中正區忠孝西路50號18樓\n"
    "之11\n"
    "承辦人：中華民國資訊聯盟協進會\n"
    "電話：0933994032\n"
    "電子信箱：louk_chi@isaroc.org\n"
    "受文者：臺北市立松山高級中學\n"
    "發文日期：中華民國115年11月4日\n"
)


def test_sender_mobile_in_letterhead_not_flagged():
    assert has_pii(HEADER_WITH_MOBILE) is False


@pytest.mark.parametrize("label", ["電話", "聯絡電話", "手機", "行動電話", "傳真"])
def test_letterhead_phone_labels_not_flagged(label):
    text = f"承辦人：王小明\n{label}：0912-345-678\n電子信箱：a@b.org\n受文者：本校\n"
    assert has_pii(text) is False, label


def test_mobile_in_body_after_letterhead_still_flagged():
    text = HEADER_WITH_MOBILE + "主旨：請協助轉知。\n說明：\n一、學生家長聯絡電話：0937-567-677。\n"
    assert "手機號碼" in [h[0] for h in scan_pii(text)]


def test_mobile_far_above_recipient_still_flagged():
    """離「受文者」太遠就不算公文頭(例如前一份附件尾巴的手機)。"""
    text = "聯絡電話：0937-567-677\n" + "內文一行\n" * 10 + "受文者：本校\n"
    assert "手機號碼" in [h[0] for h in scan_pii(text)]


def test_unlabeled_mobile_near_recipient_still_flagged():
    """沒有電話欄位標籤的手機,就算靠近受文者也照擋。"""
    text = "王小明 0937-567-677\n受文者：本校\n"
    assert "手機號碼" in [h[0] for h in scan_pii(text)]
