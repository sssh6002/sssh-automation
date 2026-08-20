# -*- coding: utf-8 -*-
"""
spec_forms.py
把規格檔變成**表單**（fork 新檔，給設定頁用）。

為什麼要這一支（承辦人 2026-08-12 的問題:「MD 檔沒在寫程式的同仁會不知道怎麼
處理」）:判斷都放在 `routing_flags.yaml` 與 `summarize_doc.md` 的對應表裡 ——
那是刻意的（改一行行為就變一片，比改 Python 好），但**代價是要會 YAML 跟表格語法**。

而 `routing_flags.yaml` 改壞的後果特別惡劣:`post_draft_batch.routing_flags()` 是

    try: cfg = yaml.safe_load(f) or {}
    except Exception: pass          # ← 讀壞就當成空的

也就是少一個空格、打成全形冒號、用了 tab，整份清單會**靜靜失效**，
「他人業務不要自動送陳核」那道保護就不見了，而畫面上什麼都不會說。
所以這一支做三件事:

  1. **表單化** —— 一列一列填，不用碰縮排與符號。
  2. **存檔前自己驗過** —— 寫出去之前先 `yaml.safe_load` 解一次、再比對解出來的
     內容跟使用者填的一樣，不一樣就退回不寫。寧可存不進去，也不要存出一個
     「看起來好了、其實讀不到」的檔案。
  3. **把註解留著** —— 那些註解是這份清單的來歷（哪個詞為什麼加、哪個為什麼拿掉）。
     整份重寫最省事，但那會把後人唯一的說明書刪掉。所以逐段保留:每個鍵前面那幾行
     註解跟著那個鍵搬，每個項目後面的行內註解變成表單裡的「備註」欄。
"""

import os
import re

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROUTING_YAML = os.path.join(_BASE_DIR, "routing_flags.yaml")
SUMMARIZE_MD = os.path.join(_BASE_DIR, "summarize_doc.md")

# 表單只認這幾個鍵。不認識的段落**原樣保留、不給改** —— 這一頁不該變成
# 「用瀏覽器改任何檔案」的入口。
LIST_KEYS = ("本人字別", "他人字別", "關鍵字")
SCALAR_KEYS = ("未知字別", "說明")


class Rejected(Exception):
    """不合格的輸入。訊息是給人看的中文。"""


# ── routing_flags.yaml ─────────────────────────────────────────────────────

_KEY_RE = re.compile(r"^(\S[^:：]*)[:：]\s*(.*)$")
_ITEM_RE = re.compile(r"^\s*-\s*(.+?)\s*(?:#\s*(.*))?$")


def _split_routing(path=None):
    """把 yaml 拆成 [檔頭, 段落…]。每段 = {鍵, 前言, 型別, 項目/值}。

    「前言」= 緊貼在那個鍵上面的註解與空行。跟著鍵一起搬，重寫才不會掉。
    """
    p = path or ROUTING_YAML
    if not os.path.isfile(p):
        return [], []
    with open(p, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]

    head, blocks, pending = [], [], []
    cur = None
    for ln in lines:
        m = _KEY_RE.match(ln) if not ln.startswith((" ", "\t", "-")) else None
        if m and (m.group(1).strip() in LIST_KEYS + SCALAR_KEYS
                  or not ln.startswith("#")):
            key, inline = m.group(1).strip(), m.group(2).strip()
            cur = {"鍵": key, "前言": pending, "型別": "值" if inline else "清單",
                   "值": inline, "項目": []}
            blocks.append(cur)
            pending = []
            continue
        if cur is None:
            if ln.strip().startswith("#") or not ln.strip():
                pending.append(ln)
                # 註解可能是下一個鍵的前言，也可能是檔頭。先囤著;
                # 遇到鍵就變成它的前言，到最後還沒用掉的就是檔頭。
                continue
            head.append(ln)
            continue
        im = _ITEM_RE.match(ln) if ln.strip().startswith("-") else None
        if im:
            cur["項目"].append({"值": im.group(1).strip(),
                                "備註": (im.group(2) or "").strip()})
            continue
        if ln.strip().startswith("#") or not ln.strip():
            pending.append(ln)          # 這一段結束了，囤給下一個鍵
            continue
        cur["項目"].append({"值": ln.strip(), "備註": ""})

    if pending and not blocks:
        head = pending + head
        pending = []
    return head, blocks


def routing_state(path=None):
    """設定頁要的東西。附上「現在程式真的讀到什麼」——
    檔案壞掉時那邊會是空的，而畫面上看得出來就不會被騙。"""
    head, blocks = _split_routing(path)
    by = {b["鍵"]: b for b in blocks}
    out = {"檔案": path or ROUTING_YAML, "段落": [], "其他段落": [],
           "生效中": _routing_effective()}
    for k in LIST_KEYS:
        b = by.get(k) or {"項目": [], "前言": []}
        out["段落"].append({"鍵": k, "型別": "清單", "項目": b["項目"],
                            "說明": _note(b.get("前言"))})
    for k in SCALAR_KEYS:
        b = by.get(k) or {"值": "", "前言": []}
        out["段落"].append({"鍵": k, "型別": "值", "值": b.get("值") or "",
                            "說明": _note(b.get("前言"))})
    out["其他段落"] = [b["鍵"] for b in blocks
                       if b["鍵"] not in LIST_KEYS + SCALAR_KEYS]
    return out


def _note(pre):
    """把前言的註解變成給人看的說明（去掉 # 與空行）。"""
    if not pre:
        return ""
    txt = [ln.lstrip("#").strip() for ln in pre if ln.strip().startswith("#")]
    return "\n".join(t for t in txt if t)


def _routing_effective():
    """程式現在**真的**讀到什麼。讀壞的話這裡會是空的 —— 那正是要讓人看到的。"""
    try:
        import post_draft_batch as pdb
        f = pdb.routing_flags()
        return {"本人字別": len(f["本人字別"]), "他人字別": len(f["他人字別"]),
                "關鍵字": len(f["關鍵字"]), "未知字別": f["未知字別"]}
    except Exception as e:
        return {"錯誤": f"{type(e).__name__}: {e}"}


def _render_routing(head, blocks):
    out = list(head)
    for b in blocks:
        out += b["前言"]
        if b["型別"] == "值":
            out.append(f"{b['鍵']}: {b['值']}")
        else:
            out.append(f"{b['鍵']}:")
            width = max([len(i["值"]) for i in b["項目"]] or [0])
            for i in b["項目"]:
                line = f"  - {i['值']}"
                if i["備註"]:
                    line += " " * max(1, width - len(i["值"]) + 2) + f"# {i['備註']}"
                out.append(line)
    return "\n".join(out).rstrip("\n") + "\n"


def routing_write(data, path=None):
    """把表單填的東西寫回 yaml。回改動的段落名 list。

    **寫出去之前先自己驗過**:`yaml.safe_load` 解一次，再比對解出來的清單跟使用者
    填的完全一樣。不一樣就 `Rejected` 不寫 —— 讀壞的檔案會讓那道保護靜靜失效。
    """
    p = path or ROUTING_YAML
    head, blocks = _split_routing(p)
    by = {b["鍵"]: b for b in blocks}
    changed = []

    for key in LIST_KEYS:
        if key not in data:
            continue
        items = []
        for raw in data[key] or []:
            v = str(raw.get("值") or "").strip()
            note = str(raw.get("備註") or "").strip()
            if not v:
                continue
            _check_word(key, v)
            items.append({"值": v, "備註": note.replace("\n", " ")})
        seen, uniq = set(), []
        for i in items:                     # 去重保序:重複的詞沒有意義
            if i["值"] not in seen:
                seen.add(i["值"])
                uniq.append(i)
        b = by.get(key)
        if b is None:
            b = {"鍵": key, "前言": [""], "型別": "清單", "值": "", "項目": []}
            blocks.append(b)
            by[key] = b
        if [(i["值"], i["備註"]) for i in b["項目"]] != \
           [(i["值"], i["備註"]) for i in uniq]:
            changed.append(key)
        b["項目"] = uniq
        b["型別"] = "清單"

    if "未知字別" in data:
        v = str(data["未知字別"] or "").strip().lower()
        if v not in ("pass", "block"):
            raise Rejected("「都沒命中時怎麼辦」只能是 pass（照常送）或 "
                           "block（一律擋下先問人）。")
        b = by.get("未知字別")
        if b and b["值"] != v:
            changed.append("未知字別")
        if b:
            b["值"], b["型別"] = v, "值"

    if "說明" in data:
        v = str(data["說明"] or "").strip().replace("\n", " ")
        if not v:
            raise Rejected("「命中時顯示的說明」不能空白 —— 那句話是承辦人看到"
                           "「這筆不自動送」時唯一的解釋。")
        b = by.get("說明")
        if b and b["值"] != v:
            changed.append("說明")
        if b:
            b["值"], b["型別"] = v, "值"

    text = _render_routing(head, blocks)
    _verify_routing(text, by)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, p)
    return changed


def _check_word(key, v):
    """一個字別／關鍵字寫得下去嗎。只擋「一定會出事」的。"""
    if len(v) > 30:
        raise Rejected(f"「{v[:12]}…」太長了（{len(v)} 字）——"
                       f"這一欄放的是字別或關鍵字，不是整句話。")
    for bad in ("#", ":", "：", "\t"):
        if bad in v:
            raise Rejected(f"「{v}」裡有「{bad}」—— 那個符號在設定檔裡有特殊意思，"
                           f"會讓整份清單讀不進去。請拿掉。")
    if key == "關鍵字" and len(v) <= 1:
        raise Rejected("關鍵字只有一個字太危險（幾乎每份公文都會中，"
                       "會把不該擋的也擋掉）。")


def _verify_routing(text, by):
    """寫出去之前先解一次。解不開、或解出來跟填的不一樣 → 不寫。"""
    try:
        import yaml
    except ImportError:
        return                              # 沒裝 yaml 就沒得驗（程式那邊也讀不到）
    try:
        cfg = yaml.safe_load(text) or {}
    except Exception as e:
        raise Rejected(f"存出來的內容電腦讀不懂（{type(e).__name__}）——"
                       f"沒有寫入。請看看有沒有奇怪的符號。") from e
    for key in LIST_KEYS:
        want = [i["值"] for i in (by.get(key) or {"項目": []})["項目"]]
        got = [str(x).strip() for x in (cfg.get(key) or [])]
        if want != got:
            raise Rejected(f"「{key}」寫出來之後讀回來不一樣（填了 {len(want)} 筆，"
                           f"讀到 {len(got)} 筆）—— 沒有寫入，以免那道保護"
                           f"靜靜失效。")


# ── summarize_doc.md 的對應表 ──────────────────────────────────────────────
#
# 那張表決定「存查分類／承辦方式／校網同步顯示至」，**愈上面愈優先**。
# 它長在 md 裡（是給 LLM 讀的規格的一部分），所以這裡只換掉那一塊，
# 其他文字一個字都不動。

TABLE_COLS = ("相關字詞", "存查分類", "承辦方式", "校網同步顯示至")
_TABLE_HEAD = "相關字詞"
_TABLE_END = "####"


def _table_bounds(lines):
    """回 (表頭 index, 表尾 index)；找不到回 (None, None)。"""
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith(_TABLE_HEAD) and "," in ln:
            start = i
            break
    if start is None:
        return None, None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].lstrip().startswith(_TABLE_END):
            end = j
            break
    return start, end


def table_state(path=None):
    p = path or SUMMARIZE_MD
    if not os.path.isfile(p):
        return {"檔案": p, "有這張表": False, "列": []}
    with open(p, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]
    start, end = _table_bounds(lines)
    if start is None:
        return {"檔案": p, "有這張表": False, "列": []}
    rows = []
    for ln in lines[start + 1:end]:
        s = ln.strip()
        if not s or set(s) <= set("-—= "):
            continue
        cells = [c.strip() for c in s.split(",")]
        while len(cells) < 4:
            cells.append("")
        rows.append(dict(zip(TABLE_COLS, cells[:4])))
    return {"檔案": p, "有這張表": True, "列": rows}


def table_write(rows, path=None):
    """把表單填的列寫回 md 的那一塊。回是否有改動。"""
    p = path or SUMMARIZE_MD
    with open(p, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f]
    start, end = _table_bounds(lines)
    if start is None:
        raise Rejected("在 summarize_doc.md 裡找不到那張對應表 —— 沒有寫入。")

    clean = []
    for r in rows or []:
        cells = [str(r.get(c) or "").strip() for c in TABLE_COLS]
        if not cells[0]:
            continue                        # 相關字詞空白 = 這一列不算
        for c in cells:
            if "," in c or "\n" in c:
                raise Rejected(f"「{c[:16]}」裡有逗號或換行 —— 這張表用逗號分欄，"
                               f"會把一欄變兩欄。多個分類請用 + 串（例:"
                               f"課外活動+硏習資訊）。")
        if not cells[1]:
            raise Rejected(f"「{cells[0]}」沒有填存查分類 —— 那一欄空白的話，"
                           f"歸檔時讀不到檔號會停下來（不會亂歸），但這一列等於沒用。")
        clean.append(cells)

    body = [f"{_TABLE_HEAD} ,{','.join(TABLE_COLS[1:])}", "-" * 30]
    width = max([len(c[0]) for c in clean] or [0])
    for cells in clean:
        pad = " " * max(0, width - len(cells[0]))
        body.append(f"{cells[0]},{pad}{cells[1]},{cells[2]},{cells[3]},")
    body.append("")

    # 比對時把空白拿掉:原檔那張表是用空白對齊的，而重寫出來的對齊寬度不一定一樣。
    # 只有「內容真的變了」才動檔案 —— 為了排版去改一份 git 追蹤的檔案沒有意義，
    # 而且會讓 diff 看起來像有人改了規則。
    def norm(block):
        return [ln.replace(" ", "").rstrip() for ln in block if ln.strip()]

    old = lines[start:end]
    if norm(old) == norm(body):
        return False
    lines[start:end] = body
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")
    os.replace(tmp, p)
    return True
