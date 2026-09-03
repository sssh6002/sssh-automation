# -*- coding: utf-8 -*-
"""
find_doc.py
用關鍵字找舊文 —— 掃承辦人自己的檔案櫃（預設 `D:\01-公文`），比對**資料夾名**。

為什麼只比資料夾名:那個櫃子裡每一筆只有 PDF（來文／合併版／opinion），沒有純文字檔。
要搜內文得先把一千多份 PDF 抽字建索引，那是另一批工程。而資料夾名本身就帶了承辦人
歸檔時寫下的四樣東西 —— 日期、公文文號、【標籤】、精簡標題 —— 平常想得起來的
關鍵字都在裡面。

⚠️ 這一支**只讀不寫**:不搬檔、不改名、不刪除。
   工作區（`document_download*`）的目錄名是 `summarize_doc.py`／`review_sheet.py`／
   `post_web_batch.py` 拿來找檔案的鍵,動了會整串壞掉。要把工作區的公文歸進櫃子,
   做法是「**複製**到櫃子再改名、工作區留原名」,那是另一支的事,不在這裡做。

命名格式（承辦人的歸檔慣例,不是程式定的規矩）:

    <民國下載日 7 碼>_<MWAA公文號>【標籤】<精簡標題>
    例:1150624_MWAA1156006169【活動】2026時空學員暑假作業

但櫃子裡有一千六百個目錄、五六種寫法:早期沒有底線（`1121003MWAA1126009285清華…`）、
沒有【標籤】（改用 `_` 接標題）、甚至有 `1120MWAA1156000609` 這種日期只剩 4 碼的。
所以解析一律**盡量猜、猜不到也不擋** —— 解析結果只決定畫面上分成哪幾欄顯示,
**真正拿去比對的是原始資料夾名**,名字再怪都找得到。

用法:

    python find_doc.py 機器人 競賽      多個關鍵字 = 全部都要出現(AND)
    python find_doc.py --tag 研習       只看某個標籤(可與關鍵字並用)
    python find_doc.py --tags           列出所有標籤與筆數
    python find_doc.py --json 機器人    給程式吃的輸出
"""

import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

# 櫃子的位置。承辦人換電腦或改路徑時,在 env.env 加一行 archive_root=... 就好,
# 不必改程式。
DEFAULT_ROOT = r"D:\01-公文"

# `109年`~`115年` 這種年份資料夾:本身不是公文,要進去一層。
_YEAR_RE = re.compile(r"^\d{2,4}\s*年$")
# 公文目錄裡放來文 PDF 的子資料夾,不是公文本身。
_SKIP_NAMES = {"來文", "附件", "__pycache__"}

_DOC_NO_RE = re.compile(r"(MWAA\d+)", re.IGNORECASE)
# 民國日期 7 碼(1150624)。有些早期目錄只寫到年(114)或殘缺(1120),另外處理。
_DATE7_RE = re.compile(r"^(\d{7})(?=\D|$)")
_TAG_RE = re.compile(r"【([^】]*)】")


def archive_root():
    """櫃子的路徑。env.env 的 archive_root 優先,沒有就用 D:\01-公文。"""
    try:
        import env_config
        v = (env_config.read_values() or {}).get("archive_root", "")
        if str(v).strip():
            return os.path.expandvars(os.path.expanduser(str(v).strip()))
    except Exception:
        pass                                    # 讀不到設定不是錯,退回預設
    return DEFAULT_ROOT


def _squash(s):
    """把空白全部拿掉。櫃子裡的標題常有空格（`臺北市 113 年寒假 STEAM`）,
    承辦人打字時不會照著空 —— 兩邊都壓掉再比一次,才找得到。"""
    return re.sub(r"\s+", "", s or "")


def _norm(s):
    return _squash(s).casefold()


def parse_name(name):
    """把資料夾名拆成日期／文號／標籤／標題。拆不出來的欄位留空字串。

    **拆錯不影響找得到找不到** —— 搜尋比的是原始名字,這裡只管畫面分欄。
    """
    rest = str(name or "").strip()
    date = ""
    m = _DATE7_RE.match(rest)
    if m:
        date = m.group(1)
        rest = rest[m.end():]
    doc_no = ""
    m = _DOC_NO_RE.search(rest)
    if m:
        doc_no = m.group(1).upper()
        # 文號**之前**的東西是日期殘骸(`1120`、`114`),之後的才是標籤與標題。
        if not date:
            head = re.sub(r"\D", "", rest[:m.start()])
            date = head
        rest = rest[m.end():]
    elif not date:
        # 沒有文號、但開頭是「一串數字＋分隔符」(`114【徵稿】…`)的,那串數字是日期,
        # 不是標題的一部分。要有分隔符才算 —— `2023弘光盃…` 的 2023 是標題,別吃掉。
        m = re.match(r"^(\d{2,7})(?=[【_\-\s])", rest)
        if m:
            date = m.group(1)
            rest = rest[m.end():]
    tag = ""
    m = _TAG_RE.search(rest)
    if m:
        tag = m.group(1).strip()
        rest = rest[:m.start()] + rest[m.end():]
    title = rest.strip(" 　_-–—．.")
    return {"日期": date, "文號": doc_no, "標籤": tag, "標題": title}


def show_date(date):
    """1150624 → 115/06/24。位數不對就原樣吐回去,不要自作聰明補零。"""
    d = str(date or "")
    if len(d) == 7 and d.isdigit():
        return f"{d[:3]}/{d[3:5]}/{d[5:]}"
    return d


def _hidden(name):
    """`.claude`、`$RECYCLE.BIN`、`~$暫存` 這種不是公文,不要列進清單。"""
    return str(name or "").startswith((".", "$", "~"))


def _row(path, name, year_dir):
    info = parse_name(name)
    return {
        "資料夾": name,
        "路徑": path,
        "年份": year_dir,
        "日期": info["日期"],
        "顯示日期": show_date(info["日期"]),
        "文號": info["文號"],
        "標籤": info["標籤"],
        "標題": info["標題"],
    }


def scan(root=None):
    """掃櫃子。只走兩層:根目錄下的公文,以及年份資料夾裡的公文。

    不往公文目錄裡面鑽（裡面是 `來文` 和一堆 PDF,鑽下去只會多幾千個雜項）。
    """
    root = root or archive_root()
    rows = []
    if not os.path.isdir(root):
        return rows
    try:
        top = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return rows
    for e in top:
        if not e.is_dir() or e.name in _SKIP_NAMES or _hidden(e.name):
            continue
        if _YEAR_RE.match(e.name):
            try:
                inner = sorted(os.scandir(e.path), key=lambda x: x.name)
            except OSError:
                continue
            for c in inner:
                if c.is_dir() and c.name not in _SKIP_NAMES and not _hidden(c.name):
                    rows.append(_row(c.path, c.name, e.name))
        else:
            rows.append(_row(e.path, e.name, ""))
    return rows


def haystack(row):
    """一列拿去比對的全部字。原始資料夾名 ＋ 年份資料夾（讓「113年」也找得到）。"""
    return _norm(f"{row.get('資料夾', '')} {row.get('年份', '')}")


def search(rows, query="", tag=None):
    """關鍵字以空白分開,**每個都要出現**才算命中(AND)。大小寫不分,空白不計。"""
    out = rows
    if tag:
        t = str(tag).strip()
        out = [r for r in out if r.get("標籤", "") == t]
    terms = [_norm(t) for t in str(query or "").split() if _norm(t)]
    if not terms:
        return list(out)
    return [r for r in out if all(t in haystack(r) for t in terms)]


def tags(rows):
    """有哪些標籤、各幾筆。多的排前面,一樣多照筆畫（其實是碼位）排。"""
    counter = {}
    for r in rows:
        t = r.get("標籤", "")
        if t:
            counter[t] = counter.get(t, 0) + 1
    return [{"標籤": k, "筆數": v}
            for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def workspace_doc_nos(scan_dirs=None):
    """工作區裡有哪些文號。給「還有幾筆沒歸進櫃子」用的,純比對、不搬東西。"""
    dirs = scan_dirs
    if dirs is None:
        try:
            import review_sheet as rs
            dirs = rs.SCAN_DIRS
        except Exception:
            dirs = []
    found = set()
    for base in dirs or []:
        if not os.path.isdir(base):
            continue
        try:
            for e in os.scandir(base):
                if not e.is_dir():
                    continue
                m = _DOC_NO_RE.search(e.name)
                if m:
                    found.add(m.group(1).upper())
        except OSError:
            continue
    return found


def not_archived(rows=None, scan_dirs=None, root=None):
    """工作區有、櫃子裡還沒有的文號。排序後回傳,方便直接顯示。"""
    rows = scan(root) if rows is None else rows
    in_cabinet = {r["文號"] for r in rows if r.get("文號")}
    return sorted(workspace_doc_nos(scan_dirs) - in_cabinet)


def main(argv=None):
    ap = argparse.ArgumentParser(description="用關鍵字找舊文（只讀櫃子,不動任何檔案）")
    ap.add_argument("keywords", nargs="*", help="關鍵字,多個就都要出現")
    ap.add_argument("--tag", help="只看這個標籤")
    ap.add_argument("--tags", action="store_true", help="列出所有標籤與筆數")
    ap.add_argument("--root", help=f"櫃子路徑（預設 {DEFAULT_ROOT}）")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    ap.add_argument("--limit", type=int, default=50, help="最多印幾筆（預設 50）")
    a = ap.parse_args(argv)

    root = a.root or archive_root()
    rows = scan(root)
    if not rows:
        print(f"櫃子裡一筆都沒有,或路徑不存在:{root}")
        return 1

    if a.tags:
        for t in tags(rows):
            print(f"{t['標籤']}\t{t['筆數']}")
        return 0

    hit = search(rows, " ".join(a.keywords), a.tag)
    if a.json:
        print(json.dumps({"櫃子": root, "全部": len(rows), "找到": len(hit),
                          "結果": hit}, ensure_ascii=False, indent=2))
        return 0

    print(f"櫃子:{root}（共 {len(rows)} 筆）")
    print(f"找到 {len(hit)} 筆" + (f"，只印前 {a.limit} 筆" if len(hit) > a.limit else ""))
    for r in hit[:a.limit]:
        tag = f"【{r['標籤']}】" if r["標籤"] else ""
        print(f"  {r['顯示日期'] or '　　　　':<10} {r['文號'] or '':<16} "
              f"{tag}{r['標題'] or r['資料夾']}")
        print(f"      {r['路徑']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
