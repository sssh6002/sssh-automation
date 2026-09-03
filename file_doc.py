# -*- coding: utf-8 -*-
"""
file_doc.py
把工作區辦完的公文**複製**進承辦人自己的檔案櫃（`D:\\01-公文`），並套上他的命名慣例。
這是「找舊文」(`find_doc.py`) 的另一半 —— 沒歸進櫃子的公文在那一頁搜不到。

⚠️ **這裡的「歸檔」跟 edoc 的「結案存查」不是同一件事。**
   `archive_batch.py` 是在 edoc 上把公文結案（無 admin 介入無法復原）;
   這一支只在**你自己的硬碟上複製檔案**，跟 edoc 完全無關，也不碰 Chrome。

⚠️ **一定是複製，不是搬移。**
   工作區的目錄名（`MWAA…`）是 `summarize_doc.py`／`review_sheet.py`／
   `post_web_batch.py` 拿來找檔案的鍵。搬走或改名，那幾支會整串找不到東西，
   而且是**安靜地**找不到。所以這一支從頭到尾**不刪除、不改名、不搬移**任何
   工作區的檔案 —— 只有 `shutil.copy2`。

判斷（標籤怎麼判、標題怎麼砍、哪些檔案要複製）**全部在 `file_doc.md`**，
不在這支程式裡。承辦人改那份 Markdown，行為就變。這裡只做機械處理:
比對關鍵字、砍字串、組檔名、複製檔案。

用法:

    python file_doc.py                  預覽:哪幾筆會歸、各自變成什麼名字
    python file_doc.py --go             真的複製（不會動到工作區）
    python file_doc.py --all            連「還沒辦完」的也一起列出來
    python file_doc.py --only MWAA…     只看／只做這幾筆
"""

import argparse
import fnmatch
import json
import os
import re
import shutil
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import find_doc  # noqa: E402

SPEC_MD = os.path.join(_BASE_DIR, "file_doc.md")

_DOC_NO_RE = re.compile(r"(MWAA\d+)", re.IGNORECASE)
# Windows 檔名不能有這些字。主旨裡出現「/」「:」的機率不低（日期、比例）。
_BAD_CHARS_RE = re.compile(r'[\\/:*?"<>|\r\n\t]')
_TABLE_HEAD = "相關字詞"
_TABLE_END = "####"


# ── 規格檔 ────────────────────────────────────────────────────────────────

def _sections(text):
    """把 `### 標題` 底下的 `- 項目` 收成 dict。純文字行（長度上限那種）也收。"""
    out, cur = {}, None
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("### "):
            cur = s[4:].strip()
            out.setdefault(cur, [])
            continue
        if cur is None or not s or s.startswith("#") or s.startswith(">"):
            continue
        if s.startswith("- "):
            out[cur].append(s[2:].strip())
        elif s.startswith("（") or s.startswith("("):
            continue                            # 說明文字，不是資料
        else:
            out[cur].append(s)
    return out


def _tag_table(text):
    """讀標籤對應表。回 [(關鍵字們, 標籤), …]，順序就是優先順序。"""
    rows, started = [], False
    for ln in text.splitlines():
        s = ln.strip()
        if not started:
            if s.startswith(_TABLE_HEAD):
                started = True
            continue
        if not s or set(s) == {"-"}:
            continue
        if s.startswith(_TABLE_END) or s.startswith("##"):
            break
        parts = [p.strip() for p in s.split(",")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        words = [w.strip() for w in re.split("OR", parts[0], flags=re.I) if w.strip()]
        if words:
            rows.append((words, parts[1]))
    return rows


def spec(path=None):
    """讀 file_doc.md。**讀不到就回一份空的** —— 空的規則會讓標籤留白、
    標題不砍字，難看但不會亂歸檔;比起自己編一套預設值安全。"""
    p = path or SPEC_MD
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {"標籤表": [], "開頭": [], "結尾": [], "引號優先": False, "上限": 40,
                "來文": [], "根層": [], "不複製": [], "讀到規格檔": False}
    sec = _sections(text)
    try:
        limit = int((sec.get("長度上限") or ["40"])[0])
    except (ValueError, IndexError):
        limit = 40
    return {
        "標籤表": _tag_table(text),
        "開頭": sec.get("開頭要刪的詞", []),
        "結尾": sec.get("結尾要刪的詞", []),
        "引號優先": (sec.get("引號優先") or ["是"])[0].strip() not in ("否", "no", "0"),
        "上限": max(1, limit),
        "來文": sec.get("放進「來文」子資料夾", []),
        "根層": sec.get("放在資料夾根層", []),
        "不複製": sec.get("不要複製", []),
        "讀到規格檔": True,
    }


# ── 名字怎麼組 ────────────────────────────────────────────────────────────

def pick_tag(subject, sp):
    """照對應表判標籤。**判不出來就留空,不猜** —— 猜錯的標籤比沒有標籤更難找。"""
    s = str(subject or "")
    for words, tag in sp.get("標籤表", []):
        if any(w and w in s for w in words):
            return tag
    return ""


_TAIL_PUNCT = "　 ，,、。.；;！!？?～~-─—"
_QUOTED_RE = re.compile(r"[「『]([^」』]{4,})[」』]")


def short_title(subject, sp):
    """把主旨砍成資料夾名能用的精簡標題。

    三步:先砍前後的公文套語 → 有引號就取引號裡那段（櫃子裡多數名字就長那樣）
    → 最後才截長度。順序不能換:先截長度會把引號截掉一半。

    砍到什麼程度是**判斷**，所以規則全在 `file_doc.md`。這裡只負責照著砍。
    砍出來的東西**本來就會有不好看的**（主旨寫得長又繞的那種）——
    所以畫面上這一格是可以直接改的，程式給的只是草稿。
    """
    t = _BAD_CHARS_RE.sub("", str(subject or "")).strip()
    changed = True
    while changed:                              # 砍完一個可能露出下一個
        changed = False
        t = t.strip(_TAIL_PUNCT)                # 「…請查照。」的句點要先拿掉才比得到
        for w in sp.get("開頭", []):
            if w and t.startswith(w):
                t, changed = t[len(w):].lstrip(_TAIL_PUNCT + ":："), True
        for w in sp.get("結尾", []):
            if w and t.endswith(w):
                t, changed = t[:-len(w)].rstrip(_TAIL_PUNCT), True
    if sp.get("引號優先"):
        m = _QUOTED_RE.search(t)
        if m:
            t = m.group(1)
    t = re.sub(r"\s+", " ", t).strip(_TAIL_PUNCT + "_「」『』")
    t = t[:sp.get("上限", 40)]
    # 截斷可能剛好切在半個引號上（`…「AI賦`），留著只會看起來像壞掉。
    return t.rstrip(_TAIL_PUNCT + "「『（(").strip()


def roc_date(path):
    """資料夾的建立日 → 民國 7 碼(1150624)。讀不到就回空字串,不要瞎編。"""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    ts = getattr(st, "st_birthtime", None) or st.st_ctime or st.st_mtime
    try:
        tm = time.localtime(ts)
    except (OverflowError, ValueError, OSError):
        return ""
    return f"{tm.tm_year - 1911:03d}{tm.tm_mon:02d}{tm.tm_mday:02d}"


def target_name(date, doc_no, tag, title):
    """組資料夾名。有標籤用【】，沒標籤用底線 —— 兩種寫法櫃子裡本來就都有。"""
    tag = _BAD_CHARS_RE.sub("", str(tag or "")).strip()
    title = _BAD_CHARS_RE.sub("", str(title or "")).strip()
    head = f"{date}_{doc_no}" if date else str(doc_no or "")
    if tag:
        return f"{head}【{tag}】{title}".rstrip()
    return (f"{head}_{title}" if title else head).rstrip("_ ")


# ── 哪些檔案要複製 ─────────────────────────────────────────────────────────

def _match(name, patterns):
    low = name.lower()
    return any(fnmatch.fnmatch(low, p.lower()) for p in patterns or [])


def classify(name, sp):
    """一個檔案要放哪裡:`來文` 子資料夾／根層／不複製。

    順序是「不複製」最優先 —— 痕跡檔(`已存查.txt`)同時也長得像 `*.txt`，
    規則衝突時要以「不複製」為準，不然狀態檔會被當成內容檔搬進櫃子。
    沒列到的檔案一律進「來文」:**寧可多帶一份，不要安靜地弄丟東西。**
    """
    if _match(name, sp.get("不複製")):
        return None
    if _match(name, sp.get("根層")):
        return ""
    return "來文"


def files_of(src, sp):
    """回 [(來源檔, 相對目標路徑)]。工作區已有的 `來文` 子資料夾直接併過去。"""
    out = []
    for base, _dirs, names in os.walk(src):
        rel_dir = os.path.relpath(base, src)
        for n in sorted(names):
            where = classify(n, sp)
            if where is None:
                continue
            # 來源本來就在子資料夾裡（多半就叫「來文」）→ 併進目標的「來文」
            sub = "來文" if rel_dir not in (".", "") else where
            out.append((os.path.join(base, n), os.path.join(sub, n) if sub else n))
    return out


# ── 計畫 ──────────────────────────────────────────────────────────────────

def _sheet_rows():
    """審核表那份「辦完了沒」。讀不到就回空 dict —— 那時全部當成還沒辦完,
    寧可少歸一筆讓人自己按，不要把跑到一半的公文歸進櫃子。"""
    try:
        import review_sheet as rs
        return {str(r["文號"]).strip().upper(): r for r in rs.rows()}
    except Exception:
        return {}


def plan(scan_dirs=None, root=None, sp=None, only=None, overrides=None):
    """列出工作區每一筆的去向。**只計算，不動任何檔案。**

    狀態:
      go      　可以歸
      已在櫃子 　櫃子裡已經有這個文號（比文號，不比名字）
      還沒辦完 　review_sheet.is_archived 說還沒 → 預設不歸
      名字撞了 　目標資料夾已經存在（不同文號同名，很罕見）→ 擋下來讓人看
    """
    import review_sheet as rs
    sp = sp or spec()
    root = root or find_doc.archive_root()
    overrides = overrides or {}
    only = {str(x).upper() for x in (only or [])} or None

    cabinet = find_doc.scan(root)
    have = {r["文號"] for r in cabinet if r.get("文號")}
    sheet = _sheet_rows()

    items = []
    for d in rs.iter_doc_dirs(scan_dirs):
        rec = rs.collect(d)
        if not rec:
            continue
        no = rec["文號"].upper()
        if only and no not in only:
            continue
        ov = overrides.get(no, {})
        subject = rec.get("主旨") or ""
        date = roc_date(d)
        tag = ov.get("標籤", pick_tag(subject, sp))
        title = ov.get("標題", short_title(subject, sp))
        name = target_name(date, no, tag, title)
        dst = os.path.join(root, name)

        notes = []
        if no in have:
            state = "已在櫃子"
        elif not rs.is_archived(sheet.get(no, {}), d):
            state = "還沒辦完"
        elif os.path.exists(dst):
            state = "名字撞了"
        else:
            state = "go"
        if not subject:
            notes.append("讀不到主旨，標題是空的 —— 自己補一個再歸")
        if not date:
            notes.append("讀不到資料夾建立日，名字會少掉日期")
        if not tag:
            notes.append("對應表判不出標籤，名字改用底線接標題")

        items.append({"文號": no, "來源": d, "主旨": subject, "日期": date,
                      "標籤": tag, "標題": title, "目標名": name, "目標": dst,
                      "狀態": state, "提醒": notes,
                      "檔案數": len(files_of(d, sp))})
    return {"櫃子": root, "項目": items, "讀到規格檔": sp.get("讀到規格檔", False)}


def copy_one(item, sp=None):
    """真的複製一筆。**目標已存在就不做** —— 絕不覆蓋櫃子裡既有的東西。"""
    sp = sp or spec()
    src, dst = item["來源"], item["目標"]
    if not os.path.isdir(src):
        return {"ok": False, "錯誤": f"來源不見了:{src}"}
    if os.path.exists(dst):
        return {"ok": False, "錯誤": f"櫃子裡已經有同名資料夾了:{item['目標名']}"}
    pairs = files_of(src, sp)
    if not pairs:
        return {"ok": False, "錯誤": "這個資料夾裡沒有要複製的檔案"}
    os.makedirs(dst, exist_ok=False)
    n = 0
    for s, rel in pairs:
        t = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(t), exist_ok=True)
        shutil.copy2(s, t)                      # copy2 = 連時間戳一起帶過去
        n += 1
    return {"ok": True, "目標": dst, "檔案數": n}


def run(items, sp=None):
    """逐筆複製。一筆失敗**不中斷**其餘的 —— 這裡沒有順序相依，
    跟 edoc 存查那條（一筆卡住整批停）不一樣。"""
    sp = sp or spec()
    out = []
    for it in items:
        r = copy_one(it, sp)
        out.append({"文號": it["文號"], "目標名": it["目標名"], **r})
    return out


# ── CLI ───────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="把工作區辦完的公文複製進檔案櫃（不搬、不改名、不刪除）")
    ap.add_argument("--go", action="store_true", help="真的複製（預設只預覽）")
    ap.add_argument("--all", action="store_true", help="連還沒辦完的也列出來")
    ap.add_argument("--only", nargs="*", help="只處理這幾個文號")
    ap.add_argument("--root", help="櫃子路徑")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    sp = spec()
    p = plan(root=a.root, sp=sp, only=a.only)
    items = p["項目"]
    go = [i for i in items if i["狀態"] == "go"]

    if a.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
        return 0

    if not sp["讀到規格檔"]:
        print(f"⚠️ 找不到 {SPEC_MD} —— 標籤與標題都不會處理。")
    print(f"櫃子:{p['櫃子']}")
    for it in items:
        if it["狀態"] != "go" and not a.all:
            continue
        mark = {"go": "→", "已在櫃子": "·", "還沒辦完": "×", "名字撞了": "!"}[it["狀態"]]
        print(f"{mark} {it['文號']}　{it['狀態']}")
        if it["狀態"] == "go":
            print(f"    {it['目標名']}　（{it['檔案數']} 個檔）")
        for n in it["提醒"]:
            print(f"    ⚠️ {n}")
    print(f"\n可以歸 {len(go)} 筆／工作區共 {len(items)} 筆")

    if not a.go:
        print("這只是預覽。要真的複製請加 --go（不會動到工作區的任何檔案）。")
        return 0
    if not go:
        print("沒有可以歸的。")
        return 0
    for r in run(go, sp):
        print(("✅ " if r["ok"] else "❌ ") + r["文號"] + "　"
              + (r["目標名"] if r["ok"] else r.get("錯誤", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
