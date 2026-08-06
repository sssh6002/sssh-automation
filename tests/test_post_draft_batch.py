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


def test_blocks_when_already_archived(env):
    """已存查（辦完了）卻還留著「陳會 OK」→ 不可以再送一次。

    2026-08-04 實測承辦人的審核表:MWAA1156006895、MWAA1156007057 已存查、
    甚至已公告，「陳會」欄仍是 OK —— upsert 只填空白格，當初打的 OK 做完後
    不會被改寫（交接檔坑 #1）。少了這條，這些結案的公文每次都算「可送」。
    """
    work, sheet = env
    d = _doc(work, "MWAA0001")
    (d / "MWAA0001已存查.txt").write_text("x", encoding="utf-8")
    rs.upsert({"文號": "MWAA0001", "擬辦": DEFAULT, "陳會": "OK", "張貼": "OK"},
              path=sheet)
    ready, blocked = pdb.plan(sheet)
    assert ready == []
    assert "已經辦完" in blocked[0]["擋下原因"]


def test_evaluate_matches_plan(env):
    """evaluate() 是 plan() 與 ui.py 陳核頁共用的判定，兩邊結果必須一致。

    介面若自己另寫一套規則，遲早出現「畫面說會擋、其實送出去了」。
    """
    work, sheet = env
    _doc(work, "MWAA0001")
    _doc(work, "MWAA0002")
    rs.upsert([{"文號": "MWAA0001", "擬辦": DEFAULT, "陳會": "OK"},
               {"文號": "MWAA0002", "陳會": "OK"}], path=sheet)
    ready, blocked = pdb.plan(sheet)
    by_no = {r["文號"]: pdb.evaluate(r) for r in rs.rows(sheet)}
    assert by_no["MWAA0001"]["送出文字"] == ready[0]["送出文字"]
    assert by_no["MWAA0001"].get("擋下原因") is None
    assert by_no["MWAA0002"]["擋下原因"] == blocked[0]["擋下原因"]


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


# ── 切清單 frame ───────────────────────────────────────────────────────────

def test_list_xpath_is_not_the_signoff_button():
    """承辦中清單的特徵必須是「公文文號」表頭，不能是「簽收」按鈕。

    2026-08-05 實測:借用 `_switch_to_signoff_frame`（靠簽收按鈕認 frame）送陳核，
    第一筆就停在「切不到清單 frame」—— 承辦中清單頁根本沒有簽收按鈕。
    這條 xpath 與全自動路徑 pending_doc 用的是同一條。
    """
    assert "公文文號" in pdb._LIST_XPATH
    assert "簽收" not in pdb._LIST_XPATH


def test_focus_list_clicks_sidebar_when_list_absent(monkeypatch):
    """清單不在畫面上時，要自己點「承辦中」叫出來，而不是直接放棄。"""
    calls = []

    class D:
        switch_to = property(lambda self: self)

        def default_content(self):
            pass

    import document_system as ds
    import edoc_sidebar as sb
    tries = iter([False, True])          # 第一次切不到，點完 sidebar 後切得到
    monkeypatch.setattr(ds, "_switch_to_frame_with_xpath",
                        lambda *a, **k: (calls.append("切"), next(tries))[1])
    monkeypatch.setattr(sb, "click_sidebar",
                        lambda d, lab: (calls.append(f"點{lab}"), True)[1])
    monkeypatch.setattr(pdb.time, "sleep", lambda s: None)
    assert pdb.focus_list(D()) is True
    assert calls == ["切", "點承辦中", "切"]


class _ListDriver:
    """假清單 frame。`text` 是目前畫面上看得到的文字。"""

    def __init__(self, text=""):
        self.text = text
        self.switch_to = self

    def default_content(self):
        pass

    def execute_script(self, script, *a):
        return self.text.find(a[0]) >= 0 if a else True


def test_focus_list_rejects_wrong_list_even_with_header(monkeypatch):
    """切到的清單有「公文文號」表頭、卻沒有要送的那一筆 → 要重叫承辦中。

    2026-08-06 實測:sidebar 寫「承辦中(5)」，frame 裡卻是只有 1 筆的**待結案**
    清單（那筆已陳核完回來等存查）。兩種清單都有表頭，只認表頭就會在錯的清單裡
    找公文，然後停在那筆要存查的公文上。
    """
    calls = []
    import document_system as ds
    import edoc_sidebar as sb
    d = _ListDriver("待結案清單 MWAA9999")
    monkeypatch.setattr(ds, "_switch_to_frame_with_xpath",
                        lambda *a, **k: (calls.append("切"), True)[1])

    def click(drv, lab):
        calls.append(f"點{lab}")
        drv.text = "承辦中清單 MWAA0001 MWAA9999"
        return True

    monkeypatch.setattr(sb, "click_sidebar", click)
    monkeypatch.setattr(pdb.time, "sleep", lambda s: None)
    assert pdb.focus_list(d, "MWAA0001") is True
    assert calls == ["切", "點承辦中", "切"]


def test_focus_list_fails_when_doc_not_in_any_list(monkeypatch):
    """重叫之後還是找不到那一筆 → 回 False（那份可能已送走或不在承辦中）。"""
    import document_system as ds
    import edoc_sidebar as sb
    monkeypatch.setattr(ds, "_switch_to_frame_with_xpath", lambda *a, **k: True)
    monkeypatch.setattr(sb, "click_sidebar", lambda d, lab: True)
    monkeypatch.setattr(pdb.time, "sleep", lambda s: None)
    assert pdb.focus_list(_ListDriver("只有別人 MWAA9999"), "MWAA0001") is False


class _MultiTabDriver:
    """多分頁的假 driver。tabs = {handle: (url, 有沒有 sidebar)}。"""

    def __init__(self, tabs, current=None):
        self.tabs = tabs
        self.current = current or list(tabs)[0]
        self.switch_to = self

    @property
    def window_handles(self):
        return list(self.tabs)

    @property
    def current_url(self):
        return self.tabs[self.current][0]

    @property
    def current_window_handle(self):
        return self.current

    def window(self, h):
        self.current = h

    def default_content(self):
        pass

    def execute_script(self, *a):
        return self.tabs[self.current][1]


VIEWER = "https://edoc.gov.taipei/tcqb/oa/index.html?app=editor&doSno=1156007696"
HOME = "https://edoc.gov.taipei/tcqb/home/default.jsp?inLine=Y"


def test_focus_main_window_skips_viewer_tab():
    """閱覽器分頁同樣是 edoc 網域，但沒有 sidebar —— 不可以當成主畫面。

    2026-08-05 實測:attach 抓到的第一個分頁就是閱覽器，整批停在第一筆。
    """
    d = _MultiTabDriver({"v": (VIEWER, False), "h": (HOME, True)}, current="v")
    assert pdb.focus_main_window(d) is True
    assert d.current_window_handle == "h"


def test_focus_main_window_fails_without_home():
    d = _MultiTabDriver({"v": (VIEWER, False)}, current="v")
    assert pdb.focus_main_window(d) is False


def test_viewer_tabs_lists_only_viewers():
    d = _MultiTabDriver({"v": (VIEWER, False), "h": (HOME, True),
                         "x": ("https://www.google.com", False)})
    assert pdb.viewer_tabs(d) == ["v"]


class _ViewerDriver:
    """假閱覽器分頁:前 `bad` 次 reload 之前都回「沒渲染」。"""

    def __init__(self, ready_after_reloads):
        self.need = ready_after_reloads
        self.reloads = 0

    def execute_script(self, *a):
        return self.reloads >= self.need

    def refresh(self):
        self.reloads += 1


def test_viewer_ready_reloads_when_page_is_plaintext(monkeypatch):
    """整頁被當成純文字丟出來時要重新整理，不是直接放棄。

    2026-08-06 實測:閱覽器第一次載入常拿到 contentType=text/css，
    ExtJS 沒啟動 → textarea 0 個 → fill_in_draft 停在「填承辦文字失敗」。
    """
    monkeypatch.setattr(pdb.time, "sleep", lambda s: None)
    d = _ViewerDriver(ready_after_reloads=1)
    assert pdb.ensure_viewer_ready(d, tries=2, wait=0.01) is True
    assert d.reloads == 1


def test_viewer_ready_gives_up_after_retries(monkeypatch):
    """重載幾次還是不行就回 False，讓那一筆失敗、整批停下 —— 不可以硬送。"""
    monkeypatch.setattr(pdb.time, "sleep", lambda s: None)
    d = _ViewerDriver(ready_after_reloads=99)
    assert pdb.ensure_viewer_ready(d, tries=2, wait=0.01) is False
    assert d.reloads == 2


def test_viewer_ready_no_reload_when_already_good(monkeypatch):
    monkeypatch.setattr(pdb.time, "sleep", lambda s: None)
    d = _ViewerDriver(ready_after_reloads=0)
    assert pdb.ensure_viewer_ready(d, tries=2, wait=0.01) is True
    assert d.reloads == 0


def test_doc_sno():
    assert pdb.doc_sno("MWAA1156007725") == "1156007725"
    assert pdb.doc_sno("") is None


class _TabsDriver:
    """分頁清單固定的假 driver，用來測 wait_viewer / close_viewer。"""

    def __init__(self, urls):
        self.urls = dict(urls)
        self.current = list(self.urls)[0]
        self.switch_to = self
        self.closed = []

    @property
    def window_handles(self):
        return list(self.urls)

    @property
    def current_url(self):
        return self.urls[self.current]

    def window(self, h):
        self.current = h

    def close(self):
        self.closed.append(self.current)
        del self.urls[self.current]


VIEW = "https://edoc.gov.taipei/tcqb/oa/index.html?app=editor&doSno={}"


def test_wait_viewer_accepts_reused_tab(monkeypatch):
    """edoc 把公文塞進既有分頁（沒有多開）時也要認得出來。

    2026-08-06 實測:一批 3 筆，前兩筆送完分頁沒關，第 3 筆被塞進既有分頁，
    舊寫法只看「window_handles 有沒有多一個」→ 誤判成「沒開出公文閱覽器分頁」
    而中止（前 2 筆其實已經真的送出去了）。
    """
    monkeypatch.setattr(pdb.time, "sleep", lambda s: None)
    d = _TabsDriver({"main": HOME, "v": VIEW.format("1156007725")})
    assert pdb.wait_viewer(d, "MWAA1156007725", timeout=0.1) is True
    assert d.current == "v"


def test_wait_viewer_rejects_other_doc(monkeypatch):
    """開著的是別份公文的閱覽器 → 不算數，不可以拿它去填字。"""
    monkeypatch.setattr(pdb.time, "sleep", lambda s: None)
    d = _TabsDriver({"main": HOME, "v": VIEW.format("1156007710")})
    assert pdb.wait_viewer(d, "MWAA1156007725", timeout=0.1) is False


def test_close_viewer_only_closes_that_doc():
    d = _TabsDriver({"main": HOME, "a": VIEW.format("1156007710"),
                     "b": VIEW.format("1156007725")})
    pdb.close_viewer(d, "MWAA1156007725")
    assert d.closed == ["b"]
    assert set(d.window_handles) == {"main", "a"}


def test_run_stops_when_no_main_window(monkeypatch):
    """找不到主畫面就別開始 —— 回 0 筆成功，不可以硬跑下去。"""
    monkeypatch.setattr(pdb, "focus_main_window", lambda d: False)
    done, failed = pdb.run([{"文號": "MWAA0001", "主旨": "x", "目錄": "d",
                             "送出文字": DEFAULT}], _MultiTabDriver({"h": (HOME, True)}))
    assert done == 0 and failed["文號"] == "MWAA0001"


def test_focus_list_gives_up_when_sidebar_click_fails(monkeypatch):
    class D:
        switch_to = property(lambda self: self)

        def default_content(self):
            pass

    import document_system as ds
    import edoc_sidebar as sb
    monkeypatch.setattr(ds, "_switch_to_frame_with_xpath", lambda *a, **k: False)
    monkeypatch.setattr(sb, "click_sidebar", lambda d, lab: False)
    assert pdb.focus_list(D()) is False


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
