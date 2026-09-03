# -*- coding: utf-8 -*-
"""找舊文（`find_doc.py`）的把關測試。

這一頁不送出任何東西、也不動檔案，所以這裡釘的不是安全，是**「找不到」的後果**:
承辦人搜不到會以為公文不見了，跑去 edoc 重查、甚至重新歸檔一份。所以釘住三件事:

  · **名字再怪都要找得到** —— 櫃子裡有五六種寫法（早期沒底線、沒【標籤】、
    日期殘缺）。解析拆錯只影響畫面分欄，**比對永遠拿原始資料夾名去比**。
  · **空白不計** —— 櫃子裡的標題常有空格（`臺北市 113 年寒假 STEAM`），
    承辦人打字時不會照著空。
  · **只讀不寫** —— 掃描不進公文目錄裡面，也不碰工作區。

另外釘住「工作區還有幾筆沒歸進櫃子」的算法:那幾筆在這一頁**搜不到**，
畫面要講得出數字，不然就是騙人。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import find_doc  # noqa: E402


# ── 資料夾名怎麼拆 ─────────────────────────────────────────────────────────

def test_parse_標準格式():
    got = find_doc.parse_name("1150624_MWAA1156006169【活動】2026時空學員暑假作業")
    assert got == {"日期": "1150624", "文號": "MWAA1156006169",
                   "標籤": "活動", "標題": "2026時空學員暑假作業"}


def test_parse_沒有標籤_改用底線接標題():
    got = find_doc.parse_name("1150309_MWAA1156000843_115年度中小學資通訊機器人競賽")
    assert got["文號"] == "MWAA1156000843"
    assert got["標籤"] == ""
    assert got["標題"] == "115年度中小學資通訊機器人競賽"


def test_parse_早期沒有底線():
    got = find_doc.parse_name("1121003MWAA1126009285清華大學TechGirls")
    assert got["日期"] == "1121003"
    assert got["文號"] == "MWAA1126009285"
    assert got["標題"] == "清華大學TechGirls"


def test_parse_日期殘缺也不要擋():
    got = find_doc.parse_name("1120MWAA1156000609")
    assert got["文號"] == "MWAA1156000609"
    assert got["日期"] == "1120"          # 不合格式，但照樣吐出來，不猜也不丟掉
    assert got["標題"] == ""


def test_parse_只有標籤沒有文號():
    got = find_doc.parse_name("114【徵稿】2025年臺灣網際網路研討會")
    assert got["標籤"] == "徵稿"
    assert got["標題"] == "2025年臺灣網際網路研討會"   # 開頭的 114 不能黏進標題


def test_parse_標題開頭的年份不是日期():
    # `2023弘光盃…` 的 2023 是標題的一部分（後面沒有分隔符），不可以被當成日期吃掉。
    got = find_doc.parse_name("2023弘光盃全國高中職VLOG大賽")
    assert got["日期"] == ""
    assert got["標題"] == "2023弘光盃全國高中職VLOG大賽"


def test_parse_完全不合格式的也照樣有標題():
    got = find_doc.parse_name("代辦")
    assert got["標題"] == "代辦"


def test_show_date():
    assert find_doc.show_date("1150624") == "115/06/24"
    assert find_doc.show_date("1120") == "1120"      # 位數不對就原樣吐回，不補零
    assert find_doc.show_date("") == ""


# ── 掃櫃子 ────────────────────────────────────────────────────────────────

def _make_cabinet(tmp_path):
    (tmp_path / "1150624_MWAA1156006169【活動】暑假作業" / "來文").mkdir(parents=True)
    (tmp_path / "113年" / "1130102_MWAA1126013085_臺北市 113 年寒假 STEAM 營隊"
     / "來文").mkdir(parents=True)
    (tmp_path / "113年" / "1130104_MWAA1136000009【競賽】智慧鐵人").mkdir(parents=True)
    (tmp_path / ".claude").mkdir()               # 隱藏目錄，不是公文
    (tmp_path / "$RECYCLE.BIN").mkdir()
    (tmp_path / "一份說明.txt").write_text("我是檔案不是目錄", encoding="utf-8")
    return tmp_path


def test_scan_年份資料夾要進去一層_來文不算一筆(tmp_path):
    rows = find_doc.scan(str(_make_cabinet(tmp_path)))
    names = sorted(r["資料夾"] for r in rows)
    assert names == ["1130102_MWAA1126013085_臺北市 113 年寒假 STEAM 營隊",
                     "1130104_MWAA1136000009【競賽】智慧鐵人",
                     "1150624_MWAA1156006169【活動】暑假作業"]
    # 年份資料夾本身不是公文，但要記在列上（讓「113年」也搜得到）
    assert {r["年份"] for r in rows} == {"", "113年"}
    # 公文目錄**裡面**不鑽 —— 鑽下去只會多出一堆「來文」
    assert not [r for r in rows if r["資料夾"] == "來文"]


def test_scan_跳過隱藏與系統目錄(tmp_path):
    rows = find_doc.scan(str(_make_cabinet(tmp_path)))
    assert not [r for r in rows if r["資料夾"].startswith((".", "$"))]


def test_scan_路徑不存在就回空的_不要爆掉(tmp_path):
    assert find_doc.scan(str(tmp_path / "沒有這個櫃子")) == []


# ── 搜尋 ──────────────────────────────────────────────────────────────────

def _rows(tmp_path):
    return find_doc.scan(str(_make_cabinet(tmp_path)))


def test_search_多個關鍵字要全部出現(tmp_path):
    rows = _rows(tmp_path)
    assert len(find_doc.search(rows, "STEAM")) == 1
    assert len(find_doc.search(rows, "STEAM 智慧鐵人")) == 0    # AND，不是 OR


def test_search_空白與大小寫都不計(tmp_path):
    rows = _rows(tmp_path)
    # 櫃子裡寫的是「臺北市 113 年寒假 STEAM 營隊」，承辦人不會照著空格打
    assert len(find_doc.search(rows, "113年寒假")) == 1
    assert len(find_doc.search(rows, "steam")) == 1


def test_search_文號後幾碼也找得到(tmp_path):
    rows = _rows(tmp_path)
    assert len(find_doc.search(rows, "6013085")) == 1


def test_search_年份資料夾也算在比對範圍(tmp_path):
    rows = _rows(tmp_path)
    assert len(find_doc.search(rows, "113年")) == 2


def test_search_沒打字就是全部(tmp_path):
    rows = _rows(tmp_path)
    assert len(find_doc.search(rows, "")) == len(rows)


def test_search_標籤篩選(tmp_path):
    rows = _rows(tmp_path)
    assert [r["標籤"] for r in find_doc.search(rows, "", "競賽")] == ["競賽"]


def test_tags_多的排前面(tmp_path):
    rows = _rows(tmp_path) * 1
    rows.append({"資料夾": "x", "標籤": "競賽", "年份": ""})
    got = find_doc.tags(rows)
    assert got[0] == {"標籤": "競賽", "筆數": 2}
    assert {"標籤": "活動", "筆數": 1} in got


# ── 工作區還有幾筆沒歸進櫃子 ───────────────────────────────────────────────

def test_not_archived_只比文號(tmp_path):
    cab = _make_cabinet(tmp_path / "櫃子")
    ws = tmp_path / "工作區"
    (ws / "MWAA1156006169").mkdir(parents=True)        # 櫃子裡已經有了
    (ws / "MWAA1156009999").mkdir()                    # 還沒歸
    (ws / "不是公文的資料夾").mkdir()
    got = find_doc.not_archived(find_doc.scan(str(cab)), scan_dirs=[str(ws)])
    assert got == ["MWAA1156009999"]


# ── 接到 UI 那一頁 ─────────────────────────────────────────────────────────
#
# 這一頁**只讀不寫**。釘住這件事:回應裡不能出現任何會動到檔案的東西，
# 而且「還有幾筆沒歸進櫃子」要真的算出來（畫面靠它出黃框警告）。

import json                                     # noqa: E402
import threading                                # noqa: E402
import urllib.request                           # noqa: E402
from http.server import ThreadingHTTPServer     # noqa: E402

import pytest                                   # noqa: E402

import ui                                       # noqa: E402


@pytest.fixture
def srv():
    s = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


def test_api_find_plan(srv, tmp_path, monkeypatch):
    cab = _make_cabinet(tmp_path / "櫃子")
    ws = tmp_path / "工作區"
    (ws / "MWAA1156009999").mkdir(parents=True)
    monkeypatch.setattr(find_doc, "archive_root", lambda: str(cab))
    monkeypatch.setattr(find_doc, "workspace_doc_nos",
                        lambda scan_dirs=None: {"MWAA1156009999"})
    with urllib.request.urlopen(srv + "/api/find/plan", timeout=10) as r:
        j = json.load(r)
    assert j["ok"] is True
    assert len(j["rows"]) == 3
    assert j["未歸檔"] == ["MWAA1156009999"]
    assert "26" not in j["訊息"] and "1 筆沒複製進櫃子" in j["訊息"]


def test_api_find_plan_櫃子不見了要講清楚(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(find_doc, "archive_root", lambda: str(tmp_path / "沒有這個櫃子"))
    with urllib.request.urlopen(srv + "/api/find/plan", timeout=10) as r:
        j = json.load(r)
    assert j["ok"] is True
    assert j["rows"] == []
    assert "找不到檔案櫃" in j["訊息"]      # 不能默默給一張空清單，那看起來像公文不見了
