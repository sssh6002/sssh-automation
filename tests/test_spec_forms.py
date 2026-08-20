# -*- coding: utf-8 -*-
"""`spec_forms.py`（規格檔表單）的把關測試。

承辦人 2026-08-12 的問題:「MD 檔沒在寫程式的同仁會不知道怎麼處理」。
判斷還是留在 `routing_flags.yaml` 與 `summarize_doc.md` 裡，但填的人不該需要懂
YAML 的縮排。

⚠️ 這裡釘的第一件事是**存壞了就整批不寫**。理由:`post_draft_batch.routing_flags()`
讀不到就回全空（`except Exception: pass`），等於「他人業務不要自動送陳核」那道
保護**靜靜失效**，而畫面上什麼都不會說。寧可存不進去。

其餘釘的:
  · 註解要留著（那是這份清單的來歷:哪個詞為什麼加、哪個為什麼拿掉）
  · 對應表的**順序**是資訊（愈上面愈優先），不能被重寫打亂
  · 表單以外的東西一個字都不能動
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spec_forms as sf  # noqa: E402

yaml = pytest.importorskip("yaml")

YAML_SRC = """\
# 陳核路徑判斷 — 命中「他人業務」者不自動送陳核。
#
# 主要訊號:來文的**發文字別**。

# 這些字別的公文屬於「我的業務」→ 照常自動陳核
本人字別:
  - 北市教資          # 臺北市政府教育局 資訊教育科
  - 臺教資            # 教育部 資訊及科技教育司

# 這些字別屬於「他人業務」→ 擋下
他人字別:
  - 北市教中          # 原圖資老師業務
  - 圖推

# 都沒命中時:'pass' 照常送 / 'block' 一律擋下
未知字別: pass

# 只放明確屬於他人業務的詞。太泛的詞會誤擋 ——
# 曾經放過又拿掉的:「閱讀推廣」、「圖書館」
關鍵字:
  - 借閱證
  - 學生證

# 命中時顯示的說明（可自行改寫）
說明: 這類公文的陳核路徑不同，請自行在 edoc 送陳核。
"""

MD_SRC = """\
### 承辦文字（##）— 預設一句
這一段是別的東西，不可以被動到。

### 以下為對應表，決定三欄
愈上方的愈優先，若有衝突，以上方為準

相關字詞 ,存查分類,承辦方式,校網同步顯示至
------------------------------
線上課程,             研習,陳會,硏習資訊,
競賽,                 競賽,陳會,課外活動,

#### 再以LLM做總結
*說明裏的內容，字數需(<原總說明15%)
"""


@pytest.fixture
def files(tmp_path, monkeypatch):
    y = tmp_path / "routing_flags.yaml"
    m = tmp_path / "summarize_doc.md"
    y.write_text(YAML_SRC, encoding="utf-8")
    m.write_text(MD_SRC, encoding="utf-8")
    monkeypatch.setattr(sf, "ROUTING_YAML", str(y))
    monkeypatch.setattr(sf, "SUMMARIZE_MD", str(m))
    return y, m


# ── 讀進來要正確 ───────────────────────────────────────────────────────────

def test_routing_state_reads_items_and_notes(files):
    s = sf.routing_state()
    by = {b["鍵"]: b for b in s["段落"]}
    assert [i["值"] for i in by["本人字別"]["項目"]] == ["北市教資", "臺教資"]
    assert by["本人字別"]["項目"][0]["備註"] == "臺北市政府教育局 資訊教育科"
    assert [i["值"] for i in by["關鍵字"]["項目"]] == ["借閱證", "學生證"]
    assert by["未知字別"]["值"] == "pass"
    # 前面那幾行註解要變成給人看的說明 —— 那是「為什麼有這一段」。
    assert "太泛的詞會誤擋" in by["關鍵字"]["說明"]


def test_table_state_keeps_order(files):
    t = sf.table_state()
    assert t["有這張表"] is True
    assert [r["相關字詞"] for r in t["列"]] == ["線上課程", "競賽"]
    assert t["列"][0]["校網同步顯示至"] == "硏習資訊"


# ── 寫回去:註解留著、其他段落不動、YAML 讀得回來 ─────────────────────────

def test_routing_write_keeps_comments(files):
    y, _ = files
    sf.routing_write({"關鍵字": [{"值": "借閱證", "備註": ""},
                                 {"值": "學生證", "備註": ""},
                                 {"值": "小論文", "備註": "2026-08 新增"}]})
    text = y.read_text(encoding="utf-8")
    assert "太泛的詞會誤擋" in text                 # 那段來歷還在
    assert "曾經放過又拿掉的" in text
    assert "# 2026-08 新增" in text                 # 備註寫成行內註解
    cfg = yaml.safe_load(text)
    assert cfg["關鍵字"] == ["借閱證", "學生證", "小論文"]
    assert cfg["本人字別"] == ["北市教資", "臺教資"]      # 沒被動到
    assert cfg["未知字別"] == "pass"


def test_routing_write_result_is_readable_by_the_program(files):
    """寫完之後，程式那條路（yaml.safe_load）要讀得到一樣的東西。

    這是這一頁存在的理由:讀不到就當空的，那道保護會靜靜失效。
    """
    y, _ = files
    sf.routing_write({"他人字別": [{"值": "北市教中", "備註": "原圖資老師業務"},
                                   {"值": "北市圖", "備註": ""}]})
    cfg = yaml.safe_load(y.read_text(encoding="utf-8"))
    assert cfg["他人字別"] == ["北市教中", "北市圖"]


def test_routing_write_reports_only_real_changes(files):
    assert sf.routing_write({"關鍵字": [{"值": "借閱證", "備註": ""},
                                        {"值": "學生證", "備註": ""}]}) == []
    assert sf.routing_write({"關鍵字": [{"值": "借閱證", "備註": ""}]}) == ["關鍵字"]


def test_routing_write_dedupes(files):
    y, _ = files
    sf.routing_write({"關鍵字": [{"值": "借閱證", "備註": ""},
                                 {"值": "借閱證", "備註": "重複"}]})
    assert yaml.safe_load(y.read_text(encoding="utf-8"))["關鍵字"] == ["借閱證"]


# ── 會讓整份失效的輸入一律擋下 ─────────────────────────────────────────────

def test_symbols_that_break_yaml_are_refused(files):
    """冒號、井號、tab 會讓整份清單讀不進去 —— 擋在存檔之前。"""
    y, _ = files
    before = y.read_text(encoding="utf-8")
    for bad in ("借閱證: 是", "借閱證#註", "借閱\t證"):
        with pytest.raises(sf.Rejected):
            sf.routing_write({"關鍵字": [{"值": bad, "備註": ""}]})
    assert y.read_text(encoding="utf-8") == before      # 一個字都沒動


def test_single_char_keyword_is_refused(files):
    """一個字的關鍵字幾乎每份公文都會中，會把不該擋的也擋掉。"""
    with pytest.raises(sf.Rejected):
        sf.routing_write({"關鍵字": [{"值": "圖", "備註": ""}]})


def test_unknown_mode_only_pass_or_block(files):
    with pytest.raises(sf.Rejected):
        sf.routing_write({"未知字別": "隨便"})


def test_note_cannot_be_blank(files):
    """那句話是承辦人看到「這筆不自動送」時唯一的解釋。"""
    with pytest.raises(sf.Rejected):
        sf.routing_write({"說明": "   "})


def test_verify_refuses_when_roundtrip_differs(files, monkeypatch):
    """萬一寫出來的東西讀回來不一樣（產生器有 bug）→ 也要擋下不寫。

    這是最後一道:前面的檢查都是「猜哪些字會出事」，這一道是「真的解一次看看」。
    """
    y, _ = files
    before = y.read_text(encoding="utf-8")
    monkeypatch.setattr(sf, "_render_routing",
                        lambda h, b: "關鍵字:\n  - 只剩這個\n")
    with pytest.raises(sf.Rejected):
        sf.routing_write({"關鍵字": [{"值": "借閱證", "備註": ""},
                                     {"值": "學生證", "備註": ""}]})
    assert y.read_text(encoding="utf-8") == before


# ── 對應表 ─────────────────────────────────────────────────────────────────

def test_table_write_keeps_the_rest_of_the_md(files):
    _, m = files
    sf.table_write([{"相關字詞": "書展", "存查分類": "館際合作",
                     "承辦方式": "陳會", "校網同步顯示至": "課外活動"}])
    text = m.read_text(encoding="utf-8")
    assert "這一段是別的東西，不可以被動到。" in text
    assert "#### 再以LLM做總結" in text
    assert "*說明裏的內容" in text
    assert "書展,館際合作,陳會,課外活動," in text.replace(" ", "")
    assert "線上課程" not in text                  # 舊的那幾列被換掉了


def test_table_write_preserves_order(files):
    """順序是資訊:同一份公文中兩列時以上面那一列為準。"""
    _, m = files
    sf.table_write([{"相關字詞": "競賽", "存查分類": "競賽", "承辦方式": "陳會",
                     "校網同步顯示至": "課外活動"},
                    {"相關字詞": "線上課程", "存查分類": "研習", "承辦方式": "陳會",
                     "校網同步顯示至": "硏習資訊"}])
    assert [r["相關字詞"] for r in sf.table_state()["列"]] == ["競賽", "線上課程"]


def test_table_write_refuses_comma_in_cell(files):
    """這張表用逗號分欄，欄位裡再有逗號就會把一欄變兩欄。"""
    with pytest.raises(sf.Rejected):
        sf.table_write([{"相關字詞": "研習,課程", "存查分類": "研習",
                         "承辦方式": "陳會", "校網同步顯示至": ""}])


def test_table_write_needs_a_category(files):
    with pytest.raises(sf.Rejected):
        sf.table_write([{"相關字詞": "書展", "存查分類": "",
                         "承辦方式": "陳會", "校網同步顯示至": ""}])


def test_table_write_skips_blank_rows(files):
    """「相關字詞」空白 = 那一列不算（表單上按了加一列又沒填）。"""
    assert sf.table_write([{"相關字詞": "", "存查分類": "", "承辦方式": "",
                            "校網同步顯示至": ""},
                           {"相關字詞": "競賽", "存查分類": "競賽",
                            "承辦方式": "陳會", "校網同步顯示至": "課外活動"}])
    assert [r["相關字詞"] for r in sf.table_state()["列"]] == ["競賽"]


def test_table_write_reports_no_change(files):
    assert sf.table_write(sf.table_state()["列"]) is False
