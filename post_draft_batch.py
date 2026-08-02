# -*- coding: utf-8 -*-
"""
post_draft_batch.py
批次貼擬辦 + 送陳核（2/4 批次）。

讀桌面審核表「公告彙整.xlsx」中**「陳會」欄已打 OK** 的公文，逐筆回 edoc：
    用文號定位公文 → 開閱覽器分頁 → 填擬辦 → 儲存 → 按陳會 → PIN 簽章

擬辦文字**以審核表為準**（你在 Excel 改過的版本），不是總結檔裡機器產的那份。

安全設計（送陳核收不回來，所以預設什麼都不做）:
  python post_draft_batch.py         → 只預覽:列出會送哪幾筆、實際要送的文字
  python post_draft_batch.py --go    → 真的送

  - 「陳會」欄留白的一律不碰（代理公文、陳核路徑特殊的那些）。
  - 任一筆失敗即整批中止，不硬著頭皮往下跑。
  - 送成功的在公文目錄寫 <文號>已陳核.txt，再跑一次會自動跳過，不重複送簽。
  - 需要先跑過 main.py（Chrome 已開在 edoc、讀卡機插著），本程式 attach 既有 session。
"""

import os
import re
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import review_sheet as rs  # noqa: E402

GATE = "陳會"
MARKER_SUFFIX = "已陳核.txt"
ROUTING_YAML = os.path.join(_BASE_DIR, "routing_flags.yaml")


_DOC_PREFIX_RE = re.compile(r"發文字號[:：]\s*(\S+?)字第")


def routing_flags():
    """讀 routing_flags.yaml。回 dict（讀不到就回全空，等於不做任何攔阻）。"""
    cfg = {}
    try:
        import yaml
        with open(ROUTING_YAML, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        pass

    def lst(k):
        return [str(x).strip() for x in (cfg.get(k) or []) if str(x).strip()]

    return {"本人字別": lst("本人字別"), "他人字別": lst("他人字別"),
            "關鍵字": lst("關鍵字"),
            "未知字別": str(cfg.get("未知字別") or "pass").strip().lower(),
            "說明": str(cfg.get("說明") or "").strip()}


def doc_prefix(content_txt):
    """從 內容.txt 抽發文字別（「○○字第…號」的○○）。抽不到回 None。"""
    m = _DOC_PREFIX_RE.search(content_txt or "")
    return m.group(1) if m else None


def routing_hit(text, flags=None, prefix=None):
    """判斷這份公文該不該擋。回 (要擋嗎, 原因) — 不擋時原因為 None。

    判斷順序（主題優先於字別 —— 業務是按主題分的，不是按來文機關分的）:
      1. 主題關鍵字命中 → 擋。實例:「數位學生證結合圖書館借閱證」發文字別是
         本人的「北市教資」，但學生證業務屬他人，只看字別會判錯。
      2. 發文字別在「他人字別」→ 擋
      3. 發文字別在「本人字別」→ 放行
      4. 字別抓不到或不在兩份清單 → 依「未知字別」設定（預設放行）
    """
    f = routing_flags() if flags is None else flags
    s = str(text or "")
    for k in f["關鍵字"]:
        if k in s:
            return True, f"主題含「{k}」屬他人業務"
    if prefix:
        for p in f["他人字別"]:
            if prefix.startswith(p) or p in prefix:
                return True, f"發文字別「{prefix}」屬他人業務"
        for p in f["本人字別"]:
            if prefix.startswith(p) or p in prefix:
                return False, None
        if f["未知字別"] == "block":
            return True, f"發文字別「{prefix}」不在清單上"
    return False, None

# 擬辦欄可能帶的前綴與註記 — 送進 edoc 前要清掉
_LEAD_RE = re.compile(r"^\s*擬\s*[:：]\s*")
_HINT_RE = re.compile(r"[（(]建議轉知[:：][^）)]*[）)]\s*$")
_TODO_RE = re.compile(r"[（(]請自行填寫[:：]?[^）)]*[）)]")


def clean_fragment(text):
    """把審核表的擬辦欄整理成可送出的承辦文字。

    - 去掉開頭的「擬：」（fill_in_draft 的模板會自己加）
    - 去掉結尾的「（建議轉知：○○教師）」— 那是給承辦人看的提示，不是公文內容
    回 (承辦文字, 被拿掉的提示 or None)。
    """
    if not text:
        return "", None
    s = str(text).strip()
    s = _LEAD_RE.sub("", s)
    hint = None
    m = _HINT_RE.search(s)
    if m:
        hint = m.group(0).strip()
        s = _HINT_RE.sub("", s).strip()
    return s, hint


def needs_human(text):
    """擬辦欄還留著「（請自行填寫…）」→ 不可送出。"""
    return bool(_TODO_RE.search(str(text or "")))


def marker_path(extract_dir, doc_no):
    return os.path.join(extract_dir, f"{doc_no}{MARKER_SUFFIX}")


def already_sent(extract_dir, doc_no):
    """該目錄內出現任何 *已陳核.txt → 視為已送簽，不重送。"""
    try:
        return any(n.endswith(MARKER_SUFFIX) for n in os.listdir(extract_dir))
    except OSError:
        return False


_RETURNED_RE = re.compile(r"^\s*\d+\.松山高中.*?\s退文\s", re.M)


def was_returned(doc_dir):
    """這份公文的簽核單裡有沒有「退文」紀錄。有 → 曾被打回，不可自動送。

    退文代表**有人對擬辦有意見**，而那個意見不會出現在任何規格檔裡（是人跟人
    之間的判斷）。同一份擬辦再送一次多半會再被退，反而更花時間。
    實測 1433 份公文中僅 14 份有退文紀錄（<1%），其中 11 份是圖書館主任退的。

    回 (是否退過, 退文那一行) — 沒退過回 (False, None)。
    """
    import glob
    for f in sorted(glob.glob(os.path.join(doc_dir, "**", "*opinion*.pdf"),
                              recursive=True)):
        try:
            from pypdf import PdfReader
            t = "\n".join(p.extract_text() or "" for p in PdfReader(f).pages)
        except Exception:
            continue
        m = _RETURNED_RE.search(t)
        if m:
            return True, re.sub(r"\s+", " ", m.group(0).strip())
    return False, None


def _read_content(doc_dir):
    """讀該公文目錄的 *內容.txt（抽發文字別用）。讀不到回空字串。"""
    import glob
    hits = sorted(glob.glob(os.path.join(doc_dir, "*內容.txt")))
    if not hits:
        return ""
    try:
        return open(hits[0], encoding="utf-8").read()
    except OSError:
        return ""


def _resolve_dir(doc_no):
    """以文號找工作區公文目錄。找不到回 None。"""
    for base in rs.SCAN_DIRS:
        if not os.path.isdir(base):
            continue
        for n in sorted(os.listdir(base)):
            if doc_no in n and os.path.isdir(os.path.join(base, n)):
                return os.path.join(base, n)
    return None


def plan(path=None):
    """回 (可送清單, 擋下清單)。每筆為 dict。"""
    flags = routing_flags()
    ready, blocked = [], []
    for row in rs.approved(GATE, path):
        doc_no = str(row["文號"]).strip()
        d = _resolve_dir(doc_no)
        item = {"文號": doc_no, "主旨": row.get("主旨") or "", "目錄": d,
                "原文": row.get("擬辦")}
        prefix = doc_prefix(_read_content(d)) if d else None
        stop, why = routing_hit(f"{row.get('主旨') or ''}\n{row.get('摘要') or ''}",
                                flags, prefix)
        returned, when = (was_returned(d) if d else (False, None))
        if d is None:
            item["擋下原因"] = "找不到公文目錄（可能已結案移走）"
        elif returned:
            item["擋下原因"] = f"這份公文被退過（{when}）— 擬辦有人有意見，請自行處理"
        elif stop:
            item["擋下原因"] = f"{why} — {flags['說明'] or '陳核路徑可能不同，請自行處理'}"
        elif already_sent(d, doc_no):
            item["擋下原因"] = "已有 已陳核.txt，先前送過"
        elif not (row.get("擬辦") or "").strip():
            item["擋下原因"] = "擬辦欄空白"
        elif needs_human(row.get("擬辦")):
            item["擋下原因"] = "擬辦欄還留著「請自行填寫」，需你先寫"
        else:
            frag, hint = clean_fragment(row["擬辦"])
            item.update({"送出文字": frag, "移除的提示": hint})
            ready.append(item)
            continue
        blocked.append(item)
    return ready, blocked


def _print_plan(ready, blocked):
    print(f"\n【會送出】{len(ready)} 筆")
    for i, it in enumerate(ready, 1):
        print(f"\n  {i}. {it['文號']}  {it['主旨'][:40]}")
        print(f"     實際送進 edoc 的文字：")
        print(f"       擬:")
        print(f"       {it['送出文字']}")
        if it["移除的提示"]:
            print(f"     （已移除給你看的提示：{it['移除的提示']}）")
    print(f"\n【不會送】{len(blocked)} 筆")
    for it in blocked:
        print(f"  - {it['文號']}  {it['擋下原因']}")


def run(ready, driver):
    """逐筆送陳核。任一筆失敗即中止，回 (成功數, 失敗的那筆 or None)。"""
    from document_system import _click_doc_by_no, _switch_to_signoff_frame
    from fill_in_draft import fill_in_draft

    main_handle = driver.current_window_handle
    done = 0
    for it in ready:
        doc_no, d = it["文號"], it["目錄"]
        print(f"\n[{done + 1}/{len(ready)}] {doc_no} — {it['主旨'][:36]}")

        driver.switch_to.window(main_handle)
        driver.switch_to.default_content()
        if not _switch_to_signoff_frame(driver):
            print("      x  切不到清單 frame")
            return done, it
        before = set(driver.window_handles)
        if not _click_doc_by_no(driver, doc_no):
            print("      x  清單裡找不到這份公文（可能已被送走或不在承辦中）")
            return done, it

        for _ in range(20):
            time.sleep(0.5)
            new = set(driver.window_handles) - before
            if new:
                break
        if not new:
            print("      x  沒開出公文閱覽器分頁")
            return done, it
        driver.switch_to.window(next(iter(new)))
        time.sleep(1.5)

        ok = fill_in_draft(driver, d, fragment=it["送出文字"], action="陳會")
        if not ok:
            print("      x  填字/儲存/陳會/簽章失敗 — 整批中止")
            return done, it

        with open(marker_path(d, doc_no), "w", encoding="utf-8") as f:
            f.write(f"已陳核。擬辦：{it['送出文字']}\n")
        print(f"      OK 已送陳核，寫入標記 {doc_no}{MARKER_SUFFIX}")
        done += 1
    return done, None


def main():
    import argparse
    ap = argparse.ArgumentParser(description="批次貼擬辦 + 送陳核（讀審核表「陳會」欄）")
    ap.add_argument("--go", action="store_true", help="真的送出（預設只預覽）")
    ap.add_argument("--path", help="審核表路徑")
    a = ap.parse_args()

    ready, blocked = plan(a.path)
    _print_plan(ready, blocked)

    if not a.go:
        print(f"\n這是預覽，什麼都沒做。確認無誤後加 --go 才會真的送出。")
        return
    if not ready:
        print("\n沒有可送的公文。")
        return

    print(f"\n===== 開始送出 {len(ready)} 筆（送陳核收不回來）=====")
    from fill_in_draft import _attach_existing_chrome
    driver = _attach_existing_chrome()
    if driver is None:
        print("[post_draft_batch] attach 不到 Chrome — 請先跑 main.py 登入 edoc 並停在清單頁。")
        raise SystemExit(1)
    if "edoc.gov.taipei" not in (driver.current_url or ""):
        print(f"[post_draft_batch] 目前不在 edoc（{driver.current_url}）— 請切到承辦中清單頁再跑。")
        raise SystemExit(1)

    done, failed = run(ready, driver)
    print(f"\n===== 完成 {done}/{len(ready)} 筆 =====")
    if failed:
        print(f"停在 {failed['文號']}，其後未處理。修好後重跑，已送出的會自動跳過。")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
