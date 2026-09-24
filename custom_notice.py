# -*- coding: utf-8 -*-
"""
custom_notice.py
自訂公告 —— 不是公文產生的公告，也要貼得上校網。

為什麼需要這支:整條張貼路徑本來每一關都綁著公文（審核表要有那一列、磁碟要有
公文資料夾、資料夾裡要有總結.md、擬辦還得寫「公佈周知」）。承辦人自己要發的
公告（例如載具領取、設備維護、系統停機）一關都過不了，只能手動貼。

**貼文那段一行都不重寫。** 真正麻煩的部分（登入校網、開新公告、填標題內文、
選同步分類、發布單位設完讀回來驗、送出後驗表單消失）全在
`document_closure/document_closure_post_web.maybe_post_announcement`，
那支是系管師的檔，這個 fork 不改。這裡只做兩件事:

  1. 準備一個**長得像公文資料夾**的目錄（`custom_notices/<編號>/`）
  2. 呼叫期間換掉兩個模組屬性，讓那支拿到自訂公告的標題內文分類
     （沿用 `post_web_batch.announcement_as_summary` 那個既有套路）

⚠️ 目錄**刻意不放在 `document_download/`**。放進去的話備料、找舊文、存查、
歸檔到檔案櫃全都會把它當成一份公文撈進來 —— 那是安靜長歪的那種錯。
"""

import contextlib
import glob
import json
import os
import re
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NOTICE_DIR = os.path.join(_BASE_DIR, "custom_notices")

TEXT_FILE = "公告.txt"
META_FILE = "分類.json"

# 介面上可以勾的分類。**這只是選單,不是保證** —— 真正能不能選中，看校網那個
# 「分類」下拉裡有沒有對得上的項目（貼文那支是拿字去比 <li> 的文字，對不到就
# 印 WARN 略過，而標記檔記的是「實際選到的」，不是「你勾的」）。
# 前三個抄自 summarize_doc.md 的對應表（公文走的也是這幾個）；
# 「重要消息」是承辦人 2026-09-05 指名要的，影響上百個學生的公告會勾它。
CATEGORY_OPTIONS = ("處室公告", "重要消息", "課外活動", "硏習資訊")

# 校網那支排版程式（sssh_style）只認兩種小標:`## 標題` 與 `【標題】`。
# 承辦人習慣打 `■`，而 `■` 它不認得 —— 2026-09-05 實測後果不是「難看」而是
# **順序整個跑掉**:沒有小標就整篇算同一塊，塊內所有條列會被收成一個 <ul> 排到
# 段落前面，證件那段和注意事項那段的條列混成一串。家長看到的順序跟寫的不一樣。
# 所以格式整理是必要的，但**不在送出時偷偷轉**（那等於程式動承辦人的字）——
# 介面上是一顆「整理格式」按鈕，轉完直接寫回輸入框，看得到、可以再改。
_HEAD_BULLETS = "■◆●▪□◇○"
_SUB_BULLETS = "‧・·．"


def normalize_body(text):
    """把承辦人習慣的符號換成校網排版程式認得的寫法。回 (新文字, 改了幾行)。

    - 行首 ■◆●…  → 【小標】   （會切出一張新卡片）
    - 行首 ‧・·   → -          （會變成條列項目）

    已經是 `【】` 或 `##` 的行不動。純粹換符號，**一個字都不改**。
    """
    out = []
    changed = 0
    for raw in str(text or "").split("\n"):
        s = raw.strip()
        if not s:
            out.append(raw)
            continue
        if s[0] in _HEAD_BULLETS:
            inner = s[1:].strip()
            if inner:
                out.append(f"【{inner}】")
                changed += 1
                continue
        if s[0] in _SUB_BULLETS:
            inner = s[1:].strip()
            if inner:
                # 保留原本的縮排,看起來才跟承辦人打的一樣
                indent = raw[:len(raw) - len(raw.lstrip())]
                out.append(f"{indent}- {inner}")
                changed += 1
                continue
        out.append(raw)
    return "\n".join(out), changed


def needs_normalize(text):
    """這段文字還有沒有校網排版程式不認得的符號。給介面決定要不要提醒。"""
    _, changed = normalize_body(text)
    return changed > 0


# ── 存哪、叫什麼 ───────────────────────────────────────────────────────────

def new_id(now=None):
    """編號 = SELF-<西元日期>-<當天流水號>。刻意不用 MW 開頭 —— 一眼就看得出
    這不是公文，清冊、標記檔、log 裡混在一起也分得出來。"""
    now = now or datetime.now()
    day = now.strftime("%Y%m%d")
    n = 1
    while os.path.isdir(os.path.join(NOTICE_DIR, f"SELF-{day}-{n:02d}")):
        n += 1
    return f"SELF-{day}-{n:02d}"


def notice_path(notice_id):
    return os.path.join(NOTICE_DIR, notice_id)


def save(title, body, cats, notice_id=None):
    """存一則自訂公告。回編號。已存在的編號就是覆寫（介面的「儲存」）。"""
    notice_id = notice_id or new_id()
    d = notice_path(notice_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, TEXT_FILE), "w", encoding="utf-8") as f:
        f.write(f"{str(title or '').strip()}\n\n{str(body or '').strip()}\n")
    meta = {"分類": [c for c in (cats or []) if str(c).strip()],
            "更新時間": datetime.now().isoformat(timespec="seconds")}
    with open(os.path.join(d, META_FILE), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return notice_id


def load(notice_id):
    """讀一則。讀不到回 None。"""
    d = notice_path(notice_id)
    p = os.path.join(d, TEXT_FILE)
    if not os.path.isfile(p):
        return None
    try:
        raw = open(p, encoding="utf-8").read()
    except OSError:
        return None
    lines = raw.split("\n")
    title = lines[0].strip() if lines else ""
    body = "\n".join(lines[1:]).strip()
    cats, updated = [], ""
    mp = os.path.join(d, META_FILE)
    if os.path.isfile(mp):
        try:
            meta = json.load(open(mp, encoding="utf-8"))
            cats = list(meta.get("分類") or [])
            updated = str(meta.get("更新時間") or "")
        except (OSError, ValueError) as e:
            print(f"[custom_notice] {notice_id} 的 {META_FILE} 讀不動"
                  f"（分類當成空的）:{type(e).__name__}: {e}")
    return {"編號": notice_id, "標題": title, "內文": body,
            "分類": cats, "更新時間": updated, "目錄": d}


def list_ids():
    if not os.path.isdir(NOTICE_DIR):
        return []
    return sorted(n for n in os.listdir(NOTICE_DIR)
                  if os.path.isdir(os.path.join(NOTICE_DIR, n)))


def is_posted(notice_id):
    """貼出去了沒 —— **看磁碟痕跡**，不看任何狀態欄位（全站同一條規矩）。"""
    d = notice_path(notice_id)
    if not os.path.isdir(d):
        return False
    return any(n.endswith("已公告.txt") for n in os.listdir(d))


# ── 判定 ───────────────────────────────────────────────────────────────────

def evaluate(notice_id):
    """這一則能不能貼。`擋下原因` 為 None 才可以。"""
    it = load(notice_id)
    if not it:
        return {"編號": notice_id, "擋下原因": "找不到這則公告"}
    out = dict(it)
    out["擋下原因"] = None

    def stop(why):
        out["擋下原因"] = why
        return out

    if not it["標題"]:
        return stop("標題是空的")
    if not it["內文"].strip():
        return stop("內文是空的")
    if not it["分類"]:
        return stop("沒有選分類 —— 至少要選一個，不然貼上去不會出現在任何一頁")
    if is_posted(notice_id):
        return stop("已經貼過了（資料夾裡有 *已公告.txt）")
    # 這一則的字是承辦人自己寫的,不是 AI 產的,所以沒有「待查核網址」那道
    # （那道擋的是 AI 幻覺與來文 PDF 夾帶的指令）。自己打的網址自己負責。
    return out


def fingerprint(it):
    """把「確認過的東西」壓成短字串:標題＋內文＋分類。

    跟張貼頁同一個理由（坑 #24）:送出前只比編號的話，確認框開著的時候有人把
    內文改掉，照樣會貼出去。這裡連內容一起比。
    """
    import hashlib
    raw = "\n".join([it.get("標題", ""), it.get("內文", ""),
                     "+".join(it.get("分類") or [])])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def plan():
    """回 (可以貼的, 被擋的)。兩邊都帶指紋，介面按確定時帶回來比對。"""
    ready, blocked = [], []
    for nid in list_ids():
        it = evaluate(nid)
        it["指紋"] = fingerprint(it) if not it.get("擋下原因") else ""
        (blocked if it.get("擋下原因") else ready).append(it)
    return ready, blocked


def preview_html(title, body):
    """用校網**真正在用的那支**排版程式算出 HTML，給介面預覽。

    不自己另外畫 —— 預覽跟實際貼出去的必須是同一支算出來的，
    不然預覽好看、貼出去長不一樣，比沒有預覽還糟。

    ⚠️ **一定要連 `sssh_heading_style()` 一起套。** 貼的時候 `run()` 會套它
    （小標從置中 1.15em 換成靠左 1.3em，post_web_batch 的 HEAD_ALIGN／HEAD_SIZE），
    預覽少套這一層就會是置中版 —— 2026-09-05 我第一版就這樣騙了承辦人一次:
    畫面給他看置中的，實際貼出去是靠左的。預覽要跟貼出去**走完全一樣的路**。
    """
    import post_web_batch as pwb
    from document_closure.document_closure_post_web import _body_to_html
    with pwb.sssh_heading_style():
        return _body_to_html(body, title)


# ── 呼叫前的替換（都用完就還原）─────────────────────────────────────────

@contextlib.contextmanager
def notice_as_summary(it):
    """讓 `maybe_post_announcement` 以為它在處理一份公文。

    換掉兩個模組屬性，`document_closure/` 一行不改:

    1. `_parse_summary` —— 自訂公告的目錄裡沒有總結.md，原本那支會回 None，
       接著 `_should_post(None)` 是 False，整則會被當成「擬辦不是要公告」跳過。
       這裡直接合成一份:handling 塞公告關鍵字讓它過閘門，title/body/分類用
       承辦人填的。
    2. `_posted_marker_path` —— 原本靠「公文主檔名」決定標記檔名，自訂公告
       沒有主檔，回 None → **標記檔寫不出來**。沒有標記就沒有痕跡，下次會
       重複貼（清冊雖然也擋得住，但痕跡要留在看得見的地方）。
    """
    from document_closure import document_closure_post_web as pw
    real_summary = pw._parse_summary
    real_marker = pw._posted_marker_path
    target = os.path.normpath(os.path.abspath(it["目錄"]))

    def patched_summary(extract_dir):
        if os.path.normpath(os.path.abspath(extract_dir)) != target:
            return real_summary(extract_dir)
        return {"handling": "公佈周知（自訂公告，非公文）",
                "title": it["標題"], "body": it["內文"],
                "category": None, "sync_categories": list(it["分類"])}

    def patched_marker(extract_dir):
        if os.path.normpath(os.path.abspath(extract_dir)) != target:
            return real_marker(extract_dir)
        return os.path.join(extract_dir, f"{it['編號']}已公告.txt")

    pw._parse_summary = patched_summary
    pw._posted_marker_path = patched_marker
    try:
        yield
    finally:
        pw._parse_summary = real_summary
        pw._posted_marker_path = real_marker


# ── 真的貼 ─────────────────────────────────────────────────────────────────

def run(expect):
    """真的貼上校網。回 (成功數, 總數)。

    `expect` — 畫面上確認的那幾則，每則是 `編號` 或 `編號:指紋`。
    動手前自己再算一次跟它比對（同張貼頁的理由:介面那道只擋得住畫面過期）。
    逐則失敗即中止 —— 跟陳核、存查、張貼同一條規矩。
    """
    import post_web_batch as pwb

    want = {}
    for tok in (expect or []):
        nid, _, mark = str(tok).partition(":")
        want[nid.strip()] = mark.strip()

    ready, _ = plan()
    now = {it["編號"]: it["指紋"] for it in ready}
    picked = [it for it in ready if it["編號"] in want]
    if sorted(want) != sorted(no for no in want if no in now):
        missing = sorted(no for no in want if no not in now)
        print(f"[custom_notice] ⛔ 這幾則現在貼不了（可能剛被改或已貼過）:"
              f"{'、'.join(missing)} —— 什麼都沒貼，請重新整理。")
        return 0, 0
    changed = sorted(no for no, mark in want.items() if mark and now[no] != mark)
    if changed:
        print(f"[custom_notice] ⛔ 這幾則的字跟你確認過的不一樣了:"
              f"{'、'.join(changed)} —— 什麼都沒貼，請重新整理再看一次。")
        return 0, 0
    if not picked:
        print("[custom_notice] 沒有要貼的公告。")
        return 0, 0

    # 在開 Chrome **之前**擋:收文那個已登入的 edoc Chrome 不能被開去校網
    # （坑 #22;這一道漏了，收尾會把那個 session 關掉）。
    busy = pwb.chrome_in_the_way()
    if busy:
        print(f"[custom_notice] ⛔ {busy}")
        return 0, 0

    from post_web_review import _launch_bare_chrome
    print(f"[custom_notice] 準備貼 {len(picked)} 則 → 開 Chrome、登入校網…")
    driver = _launch_bare_chrome()
    if driver is None:
        return 0, len(picked)

    ok = 0
    try:
        from document_closure.document_closure_post_web import \
            maybe_post_announcement
        with pwb.sssh_heading_style(), pwb.verified_publish_unit():
            for i, it in enumerate(picked, 1):
                print(f"\n[{i}/{len(picked)}] {it['編號']} {it['標題'][:40]}")
                with notice_as_summary(it):
                    posted = maybe_post_announcement(driver, it["目錄"])
                if posted:
                    ok += 1
                    continue
                print("[custom_notice] ⛔ 這一則沒有貼成功，整批停在這裡。")
                print(f"[custom_notice]    已貼出 {ok} 則，剩下的沒有動。")
                break
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    print(f"\n[custom_notice] 完成 {ok}/{len(picked)} 則。")
    return ok, len(picked)


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser(description="自訂公告（不是公文產生的公告）")
    ap.add_argument("--list", action="store_true", help="列出所有自訂公告")
    ap.add_argument("--show", metavar="編號", help="看一則的內容與判定")
    ap.add_argument("--go", nargs="+", metavar="編號",
                    help="真的貼上校網（會開 Chrome、登入校網）")
    a = ap.parse_args()

    if a.go:
        ok, total = run(a.go)
        sys.exit(0 if ok == total and total else 1)
    if a.show:
        it = evaluate(a.show)
        print(json.dumps(it, ensure_ascii=False, indent=2))
        return
    ready, blocked = plan()
    print(f"可以貼 {len(ready)} 則、被擋 {len(blocked)} 則")
    for it in ready:
        print(f"  ✓ {it['編號']}  {it['標題'][:44]}  分類={'+'.join(it['分類'])}")
    for it in blocked:
        print(f"  ✗ {it['編號']}  {it.get('標題', '')[:30]}  ← {it['擋下原因']}")


if __name__ == "__main__":
    main()
