# -*- coding: utf-8 -*-
"""自訂公告（不是公文產生的公告）的把關測試。

這一支釘的重點:

  1. 格式整理只換符號，**一個字都不能改**（2026-09-05:承辦人習慣打 ■，
     而校網排版程式不認得，後果不是難看是「條列被抽到最前面、順序跑掉」）
  2. 貼出去的字 = 確認過的字（同坑 #24:只比編號不比內容，改掉照樣貼得出去）
  3. 已貼過的不會再貼（看磁碟痕跡，不看狀態欄位）
  4. 換掉的模組屬性一定會還原 —— 沒還原的話**下一次貼真正的公文會用到假的
     總結**，那是會把錯的字貼上校網的等級
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import custom_notice as cn  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_notice_dir(tmp_path, monkeypatch):
    """每個測試用自己的 custom_notices/，不要碰到真的工作區。"""
    monkeypatch.setattr(cn, "NOTICE_DIR", str(tmp_path / "custom_notices"))


# ───────────────── 格式整理:只換符號，不改字 ─────────────────

def test_normalize_turns_block_bullet_into_heading():
    out, n = cn.normalize_body("■ 領取期間\n9月7日～9月24日")
    assert out.split("\n")[0] == "【領取期間】"
    assert n == 1


def test_normalize_turns_sub_bullet_into_list_item():
    out, _ = cn.normalize_body("      ‧ 身分證")
    assert out.strip() == "- 身分證"
    assert out.startswith("      "), "縮排要留著,看起來才跟他打的一樣"


def test_normalize_keeps_every_character_of_the_text():
    """換符號不准動內容 —— 這是承辦人自己寫的字。"""
    src = "■ 重要注意事項\n   1. 一律限本人親自領取，禁止代領（同學、家長皆不受理）。"
    out, _ = cn.normalize_body(src)
    assert "一律限本人親自領取，禁止代領（同學、家長皆不受理）。" in out
    assert "重要注意事項" in out


def test_normalize_leaves_existing_headings_alone():
    src = "【領取期間】\n## 另一種寫法\n- 已經是條列"
    out, n = cn.normalize_body(src)
    assert out == src and n == 0


def test_needs_normalize_flags_only_unknown_symbols():
    assert cn.needs_normalize("■ 小標") is True
    assert cn.needs_normalize("【小標】\n- 條列") is False


# ───────────────── 存讀 ─────────────────

def test_save_and_load_round_trip():
    nid = cn.save("【載具領取公告】9/7-9/24 至資媒組領取",
                  "【領取期間】\n9月7日～9月24日", ["處室公告"])
    it = cn.load(nid)
    assert it["標題"].startswith("【載具領取公告】")
    assert "【領取期間】" in it["內文"]
    assert it["分類"] == ["處室公告"]


def test_new_id_does_not_collide_on_the_same_day():
    a = cn.save("A", "內文", ["處室公告"])
    b = cn.save("B", "內文", ["處室公告"])
    assert a != b


def test_load_missing_notice_returns_none():
    assert cn.load("SELF-19000101-01") is None


# ───────────────── 判定 ─────────────────

def test_evaluate_passes_a_complete_notice():
    nid = cn.save("標題", "【小標】\n內文", ["處室公告"])
    assert cn.evaluate(nid)["擋下原因"] is None


def test_evaluate_blocks_notice_without_category():
    """沒分類貼上去不會出現在任何一頁 —— 等於白貼。"""
    nid = cn.save("標題", "內文", [])
    assert "沒有選分類" in cn.evaluate(nid)["擋下原因"]


@pytest.mark.parametrize("title,body,why", [
    ("", "內文", "標題"),
    ("標題", "   ", "內文"),
])
def test_evaluate_blocks_empty_fields(title, body, why):
    nid = cn.save(title, body, ["處室公告"])
    assert why in cn.evaluate(nid)["擋下原因"]


def test_evaluate_blocks_already_posted_by_disk_marker():
    """已貼過看的是磁碟痕跡,不是任何狀態欄位。"""
    nid = cn.save("標題", "內文", ["處室公告"])
    open(os.path.join(cn.notice_path(nid), f"{nid}已公告.txt"),
         "w", encoding="utf-8").write("x")
    assert "已經貼過" in cn.evaluate(nid)["擋下原因"]
    assert cn.is_posted(nid) is True


def test_plan_splits_ready_and_blocked():
    ok = cn.save("可以貼", "內文", ["處室公告"])
    bad = cn.save("沒分類", "內文", [])
    ready, blocked = cn.plan()
    assert [it["編號"] for it in ready] == [ok]
    assert [it["編號"] for it in blocked] == [bad]
    assert ready[0]["指紋"] and not blocked[0]["指紋"]


# ───────────────── 指紋:改了內容就不能貼 ─────────────────

def test_fingerprint_changes_when_body_changes():
    nid = cn.save("標題", "原本的內文", ["處室公告"])
    before = cn.fingerprint(cn.evaluate(nid))
    cn.save("標題", "被換掉的內文", ["處室公告"], notice_id=nid)
    assert cn.fingerprint(cn.evaluate(nid)) != before


def test_fingerprint_changes_when_category_changes():
    nid = cn.save("標題", "內文", ["處室公告"])
    before = cn.fingerprint(cn.evaluate(nid))
    cn.save("標題", "內文", ["處室公告", "重要消息"], notice_id=nid)
    assert cn.fingerprint(cn.evaluate(nid)) != before


def test_run_refuses_when_text_changed_after_confirm(capsys):
    """確認框開著的時候內文被改掉 → 一則都不准貼（坑 #24 同型）。"""
    nid = cn.save("標題", "確認過的內文", ["處室公告"])
    stale = cn.fingerprint(cn.evaluate(nid))
    cn.save("標題", "後來被改掉的內文", ["處室公告"], notice_id=nid)

    ok, total = cn.run([f"{nid}:{stale}"])
    assert (ok, total) == (0, 0)
    assert "跟你確認過的不一樣" in capsys.readouterr().out


def test_run_refuses_notice_that_is_no_longer_postable(capsys):
    nid = cn.save("標題", "內文", [])          # 沒分類 → 不在 ready 裡
    ok, total = cn.run([nid])
    assert (ok, total) == (0, 0)
    assert "貼不了" in capsys.readouterr().out


# ───────────────── 替換一定要還原 ─────────────────

def test_notice_as_summary_restores_module_attributes():
    """沒還原的話,下一次貼**真正的公文**會拿到這則自訂公告的字。"""
    from document_closure import document_closure_post_web as pw
    nid = cn.save("標題", "內文", ["處室公告"])
    it = cn.evaluate(nid)
    before = (pw._parse_summary, pw._posted_marker_path)
    with cn.notice_as_summary(it):
        assert pw._parse_summary is not before[0]
    assert (pw._parse_summary, pw._posted_marker_path) == before


def test_notice_as_summary_restores_even_when_body_raises():
    from document_closure import document_closure_post_web as pw
    nid = cn.save("標題", "內文", ["處室公告"])
    before = (pw._parse_summary, pw._posted_marker_path)
    with pytest.raises(RuntimeError):
        with cn.notice_as_summary(cn.evaluate(nid)):
            raise RuntimeError("貼到一半炸了")
    assert (pw._parse_summary, pw._posted_marker_path) == before


def test_patched_summary_only_answers_for_this_notice():
    """別的目錄要走回原本那支 —— 不能讓自訂公告的字外溢到公文上。"""
    from document_closure import document_closure_post_web as pw
    nid = cn.save("標題", "內文", ["處室公告"])
    it = cn.evaluate(nid)
    with cn.notice_as_summary(it):
        mine = pw._parse_summary(it["目錄"])
        other = pw._parse_summary(os.path.join(cn.NOTICE_DIR, "不存在的公文夾"))
    assert mine["title"] == "標題"
    assert mine["sync_categories"] == ["處室公告"]
    assert other is None


def test_patched_summary_passes_the_should_post_gate():
    """合成的 handling 必須真的過得了那道閘門,不然整則會被當成不公告跳過。"""
    from document_closure.document_closure_post_web import _should_post
    from document_closure import document_closure_post_web as pw
    nid = cn.save("標題", "內文", ["處室公告"])
    it = cn.evaluate(nid)
    with cn.notice_as_summary(it):
        assert _should_post(pw._parse_summary(it["目錄"])) is True


def test_patched_marker_path_lands_inside_the_notice_folder():
    """沒有這道,標記檔寫不出來 → 沒痕跡 → 會重複貼。"""
    from document_closure import document_closure_post_web as pw
    nid = cn.save("標題", "內文", ["處室公告"])
    it = cn.evaluate(nid)
    with cn.notice_as_summary(it):
        p = pw._posted_marker_path(it["目錄"])
    assert os.path.dirname(p) == it["目錄"]
    assert os.path.basename(p) == f"{nid}已公告.txt"


# ───────────────── 預覽走的是校網那支 ─────────────────

def test_preview_uses_the_real_school_site_renderer():
    """預覽跟實際貼出去必須同一支算 —— 不然預覽好看、貼出去長不一樣。"""
    html = cn.preview_html("標題", "【領取期間】\n9月7日～9月24日")
    assert "領取期間" in html and "<section" in html


def test_preview_headings_match_what_actually_gets_posted():
    """小標靠左＋大一級。

    貼的時候 run() 會套 sssh_heading_style()（HEAD_ALIGN=left、HEAD_SIZE=1.3em），
    預覽少套這一層就會是置中 1.15em 的版本 —— 2026-09-05 第一版就是這樣,
    畫面給承辦人看置中的、實際貼出去是靠左的。這條釘住兩邊同一個樣子。
    """
    html = cn.preview_html("標題", "【領取期間】\n9月7日～9月24日")
    head = html[html.index("<h3"):html.index("</h3>")]
    assert "text-align:left" in head and "text-align:center" not in head
    assert "font-size:1.3em" in head


def test_preview_restores_the_style_it_borrowed():
    """預覽是唯讀的動作,不可以把版型設定留在改過的狀態 ——
    留著的話下一次貼**真正的公文**會用到被改過的樣式。"""
    from document_closure import sssh_style
    before = sssh_style._S["h3"]
    cn.preview_html("標題", "【小標】\n內文")
    assert sssh_style._S["h3"] == before
