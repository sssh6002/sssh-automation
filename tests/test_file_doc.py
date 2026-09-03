# -*- coding: utf-8 -*-
"""歸檔到檔案櫃（`file_doc.py`）的把關測試。

這一支會**在承辦人自己的檔案櫃裡長出資料夾**，所以這裡釘的第一件事不是好不好看，是:

  **工作區一個字都不能少、不能改、不能搬。**

工作區的目錄名（`MWAA…`）是 `summarize_doc.py`／`review_sheet.py`／
`post_web_batch.py` 拿來找檔案的鍵。搬走或改名，那幾支會**安靜地**找不到東西 ——
不會噴錯，只會從此少處理一批公文。所以每一個會動檔案的測試都順便驗一次來源沒變。

其餘釘的:
  · **不覆蓋** —— 櫃子裡已經有的，寧可不做也不蓋掉。
  · **痕跡檔不進櫃子** —— `已存查.txt` 那種是工作區的狀態，
    複製過去會讓人以為櫃子裡那份還在跑流程。而它同時也長得像 `*.txt`
    （根層規則會收），所以規則衝突時「不要複製」必須贏。
  · **判斷都在 `file_doc.md`** —— 這裡只驗程式有照著那份走，不驗它的內容對不對
    （那是承辦人自己要改的）。
  · **判不出標籤就留空，不猜** —— 猜錯的標籤比沒有標籤更難找。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import file_doc  # noqa: E402
import find_doc  # noqa: E402
import review_sheet as rs  # noqa: E402


SPEC = """\
## 標籤對應表

相關字詞 ,標籤
------------------------------
競賽OR大賽,   競賽
研習,        研習
活動,        活動

####

### 開頭要刪的詞
- 函轉
- 有關

### 結尾要刪的詞
- 請查照並轉知
- 請查照
- 一案

### 引號優先
是

### 長度上限
20

### 放進「來文」子資料夾
- *.pdf

### 放在資料夾根層
- *內容.txt
- *總結.*.md

### 不要複製
- *已存查.txt
- *已公告.txt
- *.bak
"""


@pytest.fixture
def sp(tmp_path):
    p = tmp_path / "file_doc.md"
    p.write_text(SPEC, encoding="utf-8")
    return file_doc.spec(str(p))


# ── 規格檔 ────────────────────────────────────────────────────────────────

def test_spec_讀得到對應表與各段(sp):
    assert sp["讀到規格檔"] is True
    assert sp["標籤表"][0] == (["競賽", "大賽"], "競賽")     # OR 拆開，順序保留
    assert sp["開頭"] == ["函轉", "有關"]
    assert sp["上限"] == 20
    assert sp["引號優先"] is True
    assert "*已存查.txt" in sp["不複製"]


def test_spec_找不到檔案就回空的_不要自己編一套(tmp_path):
    got = file_doc.spec(str(tmp_path / "沒這個檔.md"))
    assert got["讀到規格檔"] is False
    assert got["標籤表"] == [] and got["開頭"] == []


# ── 標籤 ──────────────────────────────────────────────────────────────────

def test_pick_tag_愈上方愈優先(sp):
    # 這句同時有「競賽」和「活動」，對應表競賽在上面 → 競賽
    assert file_doc.pick_tag("辦理機器人競賽活動", sp) == "競賽"


def test_pick_tag_判不出來就留空_不要猜(sp):
    assert file_doc.pick_tag("關於本校校舍修繕工程", sp) == ""


# ── 精簡標題 ──────────────────────────────────────────────────────────────

def test_short_title_砍前後套語(sp):
    assert file_doc.short_title("函轉某某研習一案，請查照", sp) == "某某研習"


def test_short_title_句號在後面也要砍得到(sp):
    # 主旨結尾常是「…請查照。」——句點不先拿掉就比不到「請查照」
    assert file_doc.short_title("有關某某研習，請查照。", sp) == "某某研習"


def test_short_title_引號優先(sp):
    got = file_doc.short_title("函轉某大學辦理「2026機器人大賽」一案，請查照並轉知", sp)
    assert got == "2026機器人大賽"


def test_short_title_太長要截_而且不留半個引號(sp):
    got = file_doc.short_title("本校辦理十分冗長的活動名稱一二三四五六七八九十「", sp)
    assert len(got) <= sp["上限"]
    assert not got.endswith("「")


def test_short_title_主旨是空的也不要爆掉(sp):
    assert file_doc.short_title(None, sp) == ""


def test_short_title_檔名不能用的字要拿掉(sp):
    assert "/" not in file_doc.short_title("甲/乙:丙研習", sp)


# ── 組名字 ────────────────────────────────────────────────────────────────

def test_target_name_有標籤用書名號():
    assert file_doc.target_name("1150624", "MWAA1156006169", "活動", "暑假作業") \
        == "1150624_MWAA1156006169【活動】暑假作業"


def test_target_name_沒標籤改用底線():
    assert file_doc.target_name("1150624", "MWAA1156006169", "", "暑假作業") \
        == "1150624_MWAA1156006169_暑假作業"


def test_target_name_日期讀不到也組得出來():
    assert file_doc.target_name("", "MWAA1156006169", "", "") == "MWAA1156006169"


# ── 哪些檔案會被複製 ───────────────────────────────────────────────────────

def test_classify_不要複製最優先(sp):
    # 「*已存查.txt」同時也符合根層的「*內容.txt」以外的規則，衝突時不複製要贏
    assert file_doc.classify("29081861_1153081912已存查.txt", sp) is None
    assert file_doc.classify("29081861_1153081912內容.txt", sp) == ""
    assert file_doc.classify("29081861_1153081912.pdf", sp) == "來文"


def test_classify_沒列到的檔案也帶走_不要安靜弄丟(sp):
    assert file_doc.classify("神秘檔案.zip", sp) == "來文"


def _make_workspace(tmp_path, done=True):
    d = tmp_path / "工作區" / "MWAA1156009001"
    (d / "來文").mkdir(parents=True)
    (d / "28959924_1153076269.pdf").write_bytes(b"%PDF-1.4 fake")
    (d / "28959924_1153076269內容.txt").write_text(
        "主旨：函轉某大學辦理「2026機器人大賽」一案，請查照\n說明：略\n", encoding="utf-8")
    (d / "28959924_1153076269總結.claude.md").write_text("## 承辦文字\n略\n",
                                                        encoding="utf-8")
    (d / "28959924_1153076269總結.claude.md.bak").write_text("舊的", encoding="utf-8")
    (d / "來文" / "合併版.pdf").write_bytes(b"%PDF-1.4 fake")
    if done:
        (d / "28959924_1153076269已存查.txt").write_text("ok", encoding="utf-8")
        (d / "28959924_1153076269已公告.txt").write_text("ok", encoding="utf-8")
    return d


def test_files_of_子資料夾併進來文_痕跡檔不帶(tmp_path, sp):
    d = _make_workspace(tmp_path)
    rels = {rel for _s, rel in file_doc.files_of(str(d), sp)}
    assert rels == {
        os.path.join("來文", "28959924_1153076269.pdf"),
        os.path.join("來文", "合併版.pdf"),
        "28959924_1153076269內容.txt",
        "28959924_1153076269總結.claude.md",
    }


# ── 計畫 ──────────────────────────────────────────────────────────────────

@pytest.fixture
def 空審核表(monkeypatch):
    """不要碰承辦人桌面那份真的 xlsx。空表 = 一切以磁碟痕跡為準。"""
    monkeypatch.setattr(rs, "rows", lambda path=None: [])


def _plan(tmp_path, sp, done=True):
    ws = _make_workspace(tmp_path, done).parent
    cab = tmp_path / "櫃子"
    cab.mkdir(exist_ok=True)
    return file_doc.plan(scan_dirs=[str(ws)], root=str(cab), sp=sp), ws, cab


def test_plan_辦完的才可以歸(tmp_path, sp, 空審核表):
    p, _ws, _cab = _plan(tmp_path, sp)
    it = p["項目"][0]
    assert it["狀態"] == "go"
    assert it["目標名"].endswith("【競賽】2026機器人大賽")
    assert it["檔案數"] == 4


def test_plan_還沒辦完的擋下來(tmp_path, sp, 空審核表):
    p, _ws, _cab = _plan(tmp_path, sp, done=False)
    assert p["項目"][0]["狀態"] == "還沒辦完"


def test_plan_櫃子裡已經有這個文號就不再歸(tmp_path, sp, 空審核表):
    p, _ws, cab = _plan(tmp_path, sp)
    (cab / "1150101_MWAA1156009001【競賽】隨便什麼名字").mkdir()
    p2 = file_doc.plan(scan_dirs=[str(_ws_of(p))], root=str(cab), sp=sp)
    assert p2["項目"][0]["狀態"] == "已在櫃子"


def _ws_of(p):
    return os.path.dirname(p["項目"][0]["來源"])


def test_plan_名字撞了要擋下來_不要蓋掉(tmp_path, sp, 空審核表, monkeypatch):
    """「名字撞了」是最後一道保險。

    正常情況輪不到它 —— 櫃子裡若已有同名資料夾，那個名字裡一定有同一個文號，
    「已在櫃子」會先攔下。它守的是文號比對漏掉的情況（櫃子裡的名字被人改過、
    掃描讀不到那一層）。所以這裡故意讓文號比對失明，只留這道。
    """
    p, ws, cab = _plan(tmp_path, sp)
    (cab / p["項目"][0]["目標名"]).mkdir()
    monkeypatch.setattr(find_doc, "scan", lambda root=None: [])
    p2 = file_doc.plan(scan_dirs=[str(ws)], root=str(cab), sp=sp)
    assert p2["項目"][0]["狀態"] == "名字撞了"


def test_plan_可以改標籤與標題(tmp_path, sp, 空審核表):
    p, ws, cab = _plan(tmp_path, sp)
    p2 = file_doc.plan(scan_dirs=[str(ws)], root=str(cab), sp=sp,
                       overrides={"MWAA1156009001": {"標籤": "轉知", "標題": "我自己取的"}})
    assert p2["項目"][0]["目標名"].endswith("【轉知】我自己取的")


def test_plan_判不出標籤要出提醒(tmp_path, sp, 空審核表):
    p, ws, cab = _plan(tmp_path, sp)
    p2 = file_doc.plan(scan_dirs=[str(ws)], root=str(cab), sp=sp,
                       overrides={"MWAA1156009001": {"標籤": ""}})
    assert any("判不出標籤" in n for n in p2["項目"][0]["提醒"])


# ── 真的複製 ──────────────────────────────────────────────────────────────

def _snapshot(d):
    out = {}
    for base, _dirs, names in os.walk(d):
        for n in names:
            p = os.path.join(base, n)
            out[os.path.relpath(p, d)] = os.path.getsize(p)
    return out


def test_copy_複製過去_而且工作區一個字都沒動(tmp_path, sp, 空審核表):
    p, ws, cab = _plan(tmp_path, sp)
    it = p["項目"][0]
    before = _snapshot(str(ws))
    r = file_doc.copy_one(it, sp)
    assert r["ok"] is True and r["檔案數"] == 4
    # 櫃子裡長出來了
    dst = cab / it["目標名"]
    assert (dst / "來文" / "合併版.pdf").is_file()
    assert (dst / "28959924_1153076269內容.txt").is_file()
    # 痕跡檔與 .bak 沒跟過去
    assert not list(dst.glob("*已存查.txt"))
    assert not list(dst.glob("*.bak"))
    # **工作區原封不動** —— 這是這一整支最重要的一條
    assert _snapshot(str(ws)) == before


def test_copy_絕不覆蓋櫃子裡既有的(tmp_path, sp, 空審核表):
    p, _ws, cab = _plan(tmp_path, sp)
    it = p["項目"][0]
    (cab / it["目標名"]).mkdir()
    (cab / it["目標名"] / "本來就有的檔.txt").write_text("別動我", encoding="utf-8")
    r = file_doc.copy_one(it, sp)
    assert r["ok"] is False
    assert (cab / it["目標名"] / "本來就有的檔.txt").read_text(encoding="utf-8") == "別動我"


def test_run_一筆失敗不會拖垮其他筆(tmp_path, sp, 空審核表):
    p, _ws, cab = _plan(tmp_path, sp)
    good = p["項目"][0]
    bad = dict(good, 文號="MWAA1156009002", 來源=str(tmp_path / "沒有這個資料夾"),
               目標名="壞的", 目標=str(cab / "壞的"))
    out = file_doc.run([bad, good], sp)
    assert [r["ok"] for r in out] == [False, True]


def test_copy_來源不見了就明講_不要當成成功(tmp_path, sp):
    r = file_doc.copy_one({"來源": str(tmp_path / "無"), "目標": str(tmp_path / "x"),
                           "目標名": "x"}, sp)
    assert r["ok"] is False and "來源" in r["錯誤"]
