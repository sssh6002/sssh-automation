# -*- coding: utf-8 -*-
"""公告文案產生器 announce_doc 的清稿測試。

風格判斷在 announce_doc.md（LLM 負責），這裡只釘「唯一正解」的機械規則。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import announce_doc as ad  # noqa: E402

SAMPLE = """【活動】酷課APP人文拾光

📝【公告內容】
臺北市政府教育局推出暑期活動。

⚠️【注意事項】
一、需以校園帳號登入。"""


def test_strips_preamble_before_title():
    out = ad.clean_response("好的，以下是公告：\n\n【活動】測試\n\n📝【公告內容】\n內容。")
    assert out.startswith("【活動】測試")
    assert "好的" not in out


def test_strips_code_fence():
    out = ad.clean_response("```markdown\n【活動】測試\n\n📝【公告內容】\n內容。\n```")
    assert out.startswith("【活動】測試")
    assert "```" not in out


def test_rejects_response_without_title():
    assert ad.clean_response("這份公文主要在講暑假活動。") is None
    assert ad.clean_response("") is None
    assert ad.clean_response(None) is None


def test_slices_from_first_full_width_bracket():
    """開頭有雜訊但後面接得上標題 → 從「【」切,不整筆丟掉。"""
    assert ad.clean_response("公告內容如下【活動】測試") == "【活動】測試"


# ── 單條不編號 ─────────────────────────────────────────────────────────────

def test_lone_enumerator_dropped():
    out = ad.clean_response(SAMPLE)
    assert "一、需以校園帳號登入。" not in out
    assert "需以校園帳號登入。" in out


def test_two_items_keep_numbering():
    text = SAMPLE.replace("一、需以校園帳號登入。",
                          "一、需以校園帳號登入。\n二、逾期不受理。")
    out = ad.clean_response(text)
    assert "一、需以校園帳號登入。" in out
    assert "二、逾期不受理。" in out


def test_only_lone_block_affected():
    """一個區塊只有一條、另一個區塊有兩條 → 只動前者。"""
    text = ("【活動】測試\n\n"
            "📎【相關資訊】\n一、詳見官網。\n\n"
            "⚠️【注意事項】\n一、甲。\n二、乙。")
    out = ad.clean_response(text)
    assert "詳見官網。" in out and "一、詳見官網。" not in out
    assert "一、甲。" in out and "二、乙。" in out


def test_does_not_touch_content_starting_with_other_numerals():
    """單獨一條但開頭是「二、」(來源本來就怪) → 不動,交給人看。"""
    text = "【活動】測試\n\n⚠️【注意事項】\n二、只有這條。"
    assert "二、只有這條。" in ad.clean_response(text)


def test_block_heading_variants_recognised():
    """LLM 實際會吐出的各種小標變形都要認得,認不得就拿不掉單條編號。"""
    for head in ("⚠️【注意事項】", "**⚠️【注意事項】**", "⚠️ 【注意事項】",
                 "【注意事項】", "📎【相關資訊】"):
        text = f"【活動】測試\n\n{head}\n一、只有這條。"
        out = ad.clean_response(text)
        assert "一、只有這條。" not in out, head
        assert "只有這條。" in out, head


def test_emoji_heading_recognised_as_block():
    """區塊判斷要吃得下 emoji 前綴,否則整段會被當成同一區。"""
    text = "【活動】測試\n\n🎯【對象】\n一、全校師生。\n\n⚠️【注意事項】\n一、甲。\n二、乙。"
    out = ad.clean_response(text)
    assert "全校師生。" in out and "一、全校師生。" not in out
    assert "一、甲。" in out


# ── prompt 組裝 ────────────────────────────────────────────────────────────

# ── plan() 的護欄 ──────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    import review_sheet as rs
    work = tmp_path / "document_download"
    work.mkdir()
    monkeypatch.setattr(rs, "SCAN_DIRS", [str(work)])
    monkeypatch.setattr(ad.rs, "SCAN_DIRS", [str(work)])
    monkeypatch.setattr(ad, "_should_announce", lambda d: True)
    return work, str(tmp_path / "表.xlsx")


def _doc(work, no):
    d = work / no
    d.mkdir()
    (d / "1_2內容.txt").write_text("說明：\n一、甲。", encoding="utf-8")
    return d


def test_approved_row_never_overwritten(env):
    """打過 OK = 定稿。--overwrite 也不准動。

    2026-07-28 回歸測試:當初 --overwrite --limit 1 把承辦人手寫的公告蓋掉三次。
    """
    import review_sheet as rs
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": "我手寫的定稿", "張貼": "OK"}, path=sheet)
    todo, skip = ad.plan(sheet, overwrite=True)
    assert todo == []
    assert "定稿不覆蓋" in skip[0]["略過"]


def test_approved_but_empty_announcement_still_generated(env):
    """陳會打了 OK 但公告欄還空著 → 該產,不要因為打過 OK 就整列凍結。"""
    import review_sheet as rs
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "陳會": "OK"}, path=sheet)
    todo, _ = ad.plan(sheet, overwrite=False)
    assert [t["文號"] for t in todo] == ["MWAA0001"]


def test_only_restricts_targets(env):
    import review_sheet as rs
    work, sheet = env
    for n in ("MWAA0001", "MWAA0002"):
        _doc(work, n)
    rs.upsert([{"文號": "MWAA0001"}, {"文號": "MWAA0002"}], path=sheet)
    todo, _ = ad.plan(sheet, only="MWAA0002")
    assert [t["文號"] for t in todo] == ["MWAA0002"]


def test_unapproved_existing_text_needs_overwrite(env):
    import review_sheet as rs
    work, sheet = env
    _doc(work, "MWAA0001")
    rs.upsert({"文號": "MWAA0001", "公告": "機器產的草稿"}, path=sheet)
    assert ad.plan(sheet, overwrite=False)[0] == []
    assert [t["文號"] for t in ad.plan(sheet, overwrite=True)[0]] == ["MWAA0001"]


def test_announce_model_defaults_to_sonnet():
    """公告值得用好模型;摘要不該被連累。"""
    assert ad.ANNOUNCE_MODEL_DEFAULT == "sonnet"


def test_model_override_is_scoped_to_the_call(tmp_path, monkeypatch):
    """覆寫只在產文案期間有效,結束後必須還原 —— 否則摘要會默默跟著變貴。"""
    import summarize_doc
    seen = {}

    def fake_backends(prompt):
        seen["model"] = summarize_doc._read_config("summarize_claude_model")
        return "【活動】測\n\n📝【公告內容】\n甲。", "stub", "stub-model"

    monkeypatch.setattr(summarize_doc, "_call_backends", fake_backends)
    d = tmp_path / "MWAA0001"
    d.mkdir()
    (d / "1_2內容.txt").write_text("說明：\n一、甲。", encoding="utf-8")

    assert "summarize_claude_model" not in summarize_doc._CONFIG_OVERRIDE
    ad.generate({"文號": "MWAA0001", "主旨": "x", "目錄": str(d)}, "規格", model="opus")
    assert seen["model"] == "opus"
    assert "summarize_claude_model" not in summarize_doc._CONFIG_OVERRIDE


def test_prompt_carries_spec_subject_and_content():
    p = ad.build_prompt("規格內容", "說明：\n一、甲。", "主旨甲乙丙")
    assert "規格內容" in p
    assert "主旨甲乙丙" in p
    assert "一、甲。" in p
    assert p.rstrip().endswith("第一個字必須是「【」。")


# ── 來文夾帶指令的緩解（2026-07-29）───────────────────────────────────────

def test_stray_link_is_flagged_not_deleted(env, monkeypatch):
    """公告出現來文沒有的網址 → 標註待查核,但不擅自刪除。"""
    import summarize_doc
    work, sheet = env
    d = work / "MWAA0001"
    d.mkdir()
    (d / "1_2內容.txt").write_text(
        "說明：\n一、報名請至 https://www.shs.edu.tw 。", encoding="utf-8")

    monkeypatch.setattr(summarize_doc, "_call_backends", lambda p: (
        "【活動】測\n\n📝【公告內容】\n報名 https://evil.example.com/form",
        "stub", "stub-model"))
    out, _, _ = ad.generate({"文號": "MWAA0001", "主旨": "x", "目錄": str(d)}, "規格")
    assert "https://evil.example.com/form" in out      # 沒被刪
    assert "待查核" in out                              # 但有標


def test_source_link_not_flagged(env, monkeypatch):
    import summarize_doc
    work, sheet = env
    d = work / "MWAA0001"
    d.mkdir()
    (d / "1_2內容.txt").write_text("說明：\n一、報名 https://www.shs.edu.tw 。",
                                  encoding="utf-8")
    monkeypatch.setattr(summarize_doc, "_call_backends", lambda p: (
        "【活動】測\n\n📝【公告內容】\n報名 https://www.shs.edu.tw", "stub", "m"))
    out, _, _ = ad.generate({"文號": "MWAA0001", "主旨": "x", "目錄": str(d)}, "規格")
    assert "待查核" not in out


def test_pii_marked_docs_are_skipped(env):
    """含個資的公文不進待產清單 —— 一毛 token 都不該花。"""
    import review_sheet as rs
    work, sheet = env
    d = work / "MWAA0001"
    d.mkdir()
    (d / "1_2內容.txt").write_text("說明：\n一、甲。", encoding="utf-8")
    (d / "1_2含個資.txt").write_text("⚠️ 偵測到個資", encoding="utf-8")
    rs.upsert({"文號": "MWAA0001"}, path=sheet)
    todo, skip = ad.plan(sheet)
    assert todo == []
    assert "含個資" in skip[0]["略過"]
