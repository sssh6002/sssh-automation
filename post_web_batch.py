# -*- coding: utf-8 -*-
"""
post_web_batch.py
校網張貼（fork 版執行器）。跟 `post_draft_batch.py`（陳核）、`archive_batch.py`
（存查）同一層 —— UI 的「張貼」頁按下去就是跑這一支。

    python post_web_batch.py                只預覽:會貼哪幾筆、標題與分類長什麼樣
    python post_web_batch.py --json         印一行 JSON 給 UI 用（同樣不貼）
    python post_web_batch.py --go --expect=<文號,文號…>   真的貼上校網

⚠️ **這是整套流程裡唯一真的對外的動作**。貼出去全校師生家長都看得到
（可事後刪，但已經被看到就是被看到了）。所以關卡比其他頁多一道:
**文案裡有「待查核」網址的一律擋下，不給貼**。那種網址可能是 AI 幻覺，
也可能是來文 PDF 裡夾帶的指令 —— 貼上校網就等於幫它散布出去。

跟 `post_web_review.py`（原作者那支、`main.py 5`）的兩個根本差別:

1. **貼的內容不一樣**。那支貼的是 `*總結*.md` 解析出來的「主旨＋數字條列」;
   這支貼的是**審核表「公告」欄**那份給人讀的文案（承辦人可以逐字改的那份）。
   2026-08-11 發現兩者一直是斷開的 —— 公告頁辛苦產的文案根本沒被拿去貼。
2. **不用 `input()`**。那支是終端機逐筆問 y/n，包進 subprocess 會卡死;
   這支照 fork 的慣例走「畫面確認 → `--expect` 帶清單下來 → 動手前再比對一次」。

`document_closure/` 一行都沒改:發佈本體仍是系管師的 `maybe_post_announcement`，
只在呼叫前把 `_parse_summary` 與版型常數換掉，用完還原。
"""

import argparse
import contextlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import announce_doc as ad  # noqa: E402
import review_sheet as rs  # noqa: E402

PLAN_MARK = "##PLAN#"

# 校網版型:區塊標題靠左、字大一級。承辦人 2026-08-11 看過實物後定的。
# 預設值（置中 1.15em）是資媒組長模板的規格，**不改 sssh_style.py**，
# 只在這條路呼叫前覆寫模組屬性 —— 系管師跑 main.py 3 的版面一個字都不會變。
HEAD_ALIGN = "left"
HEAD_SIZE = "1.3em"


def split_announcement(text):
    """公告文案 → (標題, 內文)。

    第一行就是【類別】標題那行 —— announce_doc.md 的規格就這樣設計的。
    校網布告欄本身有標題列，所以標題抽出來單獨給，內文不重複印。
    """
    lines = str(text or "").strip().split("\n")
    if not lines or not lines[0].strip():
        return "", ""
    return lines[0].strip(), "\n".join(lines[1:]).strip()


def _doc_dir(doc_no):
    for base in rs.SCAN_DIRS:
        if not os.path.isdir(base):
            continue
        for n in sorted(os.listdir(base)):
            if doc_no in n and os.path.isdir(os.path.join(base, n)):
                return os.path.join(base, n)
    return None


# ── 判定 ───────────────────────────────────────────────────────────────────

def evaluate(row):
    """這一筆能不能貼。回 dict，`擋下原因` 為 None 代表可以貼。

    判定來源一律沿用既有的那幾支（`_should_post` / `_already_announced`），
    不另立標準 —— 兩套規則遲早分岔，而最壞的方向是「畫面說會擋、其實貼出去了」。
    """
    from document_closure.document_closure_post_web import (
        _already_announced, _parse_summary, _should_post)

    no = str(row["文號"]).strip()
    text = str(row.get("公告") or "").strip()
    title, body = split_announcement(text)
    d = _doc_dir(no)
    out = {"文號": no, "主旨": row.get("主旨") or "", "目錄": d or "",
           "標題": title, "內文": body, "分類": [], "擋下原因": None}

    def stop(why):
        out["擋下原因"] = why
        return out

    if not d:
        return stop("找不到公文資料夾")
    if not text:
        return stop("公告欄是空的 —— 先去「公告」頁產文案")
    # ⚠️ 這一道是張貼專屬的。文案裡的「待查核」= 來文找不到的網址，
    # 可能是 AI 幻覺，也可能是來文 PDF 夾帶的指令。貼上校網就是幫它散布。
    # 公告頁只是標紅提醒（那時還沒對外），到這裡必須**擋下**。
    if ad.STRAY_MARK in text:
        return stop("文案裡有「待查核」網址 —— 請先到公告頁確認那幾個網址，"
                    "確認過就把那段標註刪掉")
    if not title or not body:
        return stop("文案格式不對（第一行要是【類別】標題，底下才是內文）")

    summary = _parse_summary(d)
    if not _should_post(summary):
        return stop("擬辦不是要公告（承辦文字沒有「公佈周知／於官網公告」）")
    if _already_announced(d):
        return stop("已經公告過了（資料夾有 *已公告.txt，或清冊裡already有）")
    out["分類"] = (summary or {}).get("sync_categories") or []
    return out


def plan(path=None):
    """回 (可貼, 擋下, 候選, 警告)。分堆規則跟陳核頁一樣。

    「張貼」欄打 OK = 承辦人說這筆可以貼（跟陳核的「陳會」欄對稱）。
    """
    ready, blocked, cand = [], [], []
    for row in rs.rows(path):
        no = str(row["文號"]).strip()
        d = _doc_dir(no)
        # 辦完的（已存查＋已張貼/不用公告）不該再出現。
        if rs.is_archived(row, d) or rs.is_done(row.get("張貼")):
            continue
        it = evaluate(row)
        if rs.is_approved(row.get("張貼")):
            (blocked if it["擋下原因"] else ready).append(it)
        else:
            cand.append(it)

    warn = []
    from taipeion_login_selenium import _read_config
    if not _read_config("sssh_publish_unit"):
        warn.append("env.env 缺 sssh_publish_unit —— 真的按下去會在發佈那步停住。"
                    "請先填「要發佈到哪個單位/群組」。")
    return ready, blocked, cand, warn


# ── 呼叫前的替換（都用完就還原）─────────────────────────────────────────

@contextlib.contextmanager
def announcement_as_summary(texts):
    """把 `_parse_summary` 換成「title/body 吃審核表公告欄」的版本。

    texts — {文號: 公告文案}。沒有文案的公文一律走回原本的解析結果。

    **只換 title 與 body**，其餘欄位（承辦文字、同步分類、存查分類）原樣保留 ——
    `_should_post` 與分類選擇都還要靠它們，換掉會出鬼故事。
    """
    from document_closure import document_closure_post_web as pw
    real = pw._parse_summary

    def patched(extract_dir):
        s = real(extract_dir)
        if not s:
            return s
        text = texts.get(pw._doc_no_of(extract_dir))
        if not text:
            return s
        title, body = split_announcement(text)
        if not title or not body:
            return s
        return {**s, "title": title, "body": body}

    pw._parse_summary = patched
    try:
        yield
    finally:
        pw._parse_summary = real


@contextlib.contextmanager
def sssh_heading_style(align=None, size=None):
    """把校網版型的區塊標題改成靠左、字大一級。用完還原。

    `sssh_style.py` 在 `document_closure/` 底下（系管師那條路也在用），
    所以**不改檔案**，只在這條路呼叫期間換模組屬性。
    """
    from document_closure import sssh_style
    old = sssh_style._S["h3"]
    new = (old.replace("text-align:center", f"text-align:{align or HEAD_ALIGN}")
              .replace("font-size:1.15em", f"font-size:{size or HEAD_SIZE}"))
    sssh_style._S["h3"] = new
    try:
        yield
    finally:
        sssh_style._S["h3"] = old


# ── 真的貼 ─────────────────────────────────────────────────────────────────

def run(expect, path=None):
    """真的貼上校網。回 (成功數, 總數)。

    `expect` — 畫面上看到的那幾筆文號。**動手前自己再算一次**，跟它比對:
    介面那道只擋得住畫面過期，擋不住這半秒內審核表又被改了。
    逐筆失敗即中止 —— 跟陳核、存查同一條規矩。
    """
    ready, _, _, warn = plan(path)
    now = [it["文號"] for it in ready]
    if sorted(now) != sorted(expect):
        print(f"[post_web_batch] ⛔ 清單變了（你看到 {len(expect)} 筆，"
              f"現在算出來 {len(now)} 筆）—— 什麼都沒貼，請重新整理再確認。")
        return 0, 0
    if warn:
        for w in warn:
            print(f"[post_web_batch] ⛔ {w}")
        return 0, 0
    if not ready:
        print("[post_web_batch] 沒有要貼的公文。")
        return 0, 0

    texts = {}
    for row in rs.rows(path):
        no = str(row["文號"]).strip()
        if no in now:
            texts[no] = str(row.get("公告") or "")

    from post_web_review import _launch_bare_chrome
    print(f"[post_web_batch] 準備貼 {len(ready)} 筆 → 開 Chrome、登入校網…")
    driver = _launch_bare_chrome()
    if driver is None:
        return 0, len(ready)

    ok = 0
    try:
        from document_closure.document_closure_post_web import maybe_post_announcement
        with announcement_as_summary(texts), sssh_heading_style():
            for i, it in enumerate(ready, 1):
                print(f"\n[{i}/{len(ready)}] {it['文號']} {it['標題'][:40]}")
                if maybe_post_announcement(driver, it["目錄"]):
                    ok += 1
                    continue
                # 逐筆失敗即中止:後面那幾筆的狀態已經不確定了，硬跑下去
                # 只會把問題擴大（跟陳核、存查同一條規矩）。
                print("[post_web_batch] ⛔ 這一筆沒有貼成功，整批停在這裡。")
                print(f"[post_web_batch]    已貼出 {ok} 筆，剩下的沒有動。")
                break
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    print(f"\n[post_web_batch] 完成 {ok}/{len(ready)} 筆。")
    return ok, len(ready)


# ── CLI ───────────────────────────────────────────────────────────────────

def _slim(it):
    return {k: it[k] for k in ("文號", "主旨", "標題", "分類", "擋下原因", "目錄")}


def main():
    ap = argparse.ArgumentParser(description="校網張貼（貼審核表「公告」欄那份文案）")
    ap.add_argument("--go", action="store_true", help="真的貼（要搭配 --expect）")
    ap.add_argument("--expect", help="畫面上看到的文號清單，逗號分隔")
    ap.add_argument("--json", action="store_true", help="印一行 JSON 給 UI 用")
    ap.add_argument("--path", help="審核表路徑")
    a = ap.parse_args()

    ready, blocked, cand, warn = plan(a.path)

    if a.json:
        print(PLAN_MARK + json.dumps(
            {"可貼": [_slim(i) for i in ready],
             "擋下": [_slim(i) for i in blocked],
             "候選": [_slim(i) for i in cand],
             "警告": warn}, ensure_ascii=False))
        return

    if a.go:
        if not a.expect:
            print("[post_web_batch] --go 一定要帶 --expect=<文號清單> ——")
            print("[post_web_batch] 這是唯一對外的動作，不接受「就照你算的貼」。")
            raise SystemExit(2)
        run([s.strip() for s in a.expect.split(",") if s.strip()], a.path)
        return

    for w in warn:
        print(f"⚠️ {w}\n")
    print(f"【會貼】{len(ready)} 筆")
    for it in ready:
        print(f"  - {it['文號']}  {it['標題'][:44]}")
        print(f"      分類:{'+'.join(it['分類']) or '(無)'}　內文 {len(it['內文'])} 字")
    print(f"\n【勾了要貼，但貼不出去】{len(blocked)} 筆")
    for it in blocked:
        print(f"  - {it['文號']}  {it['擋下原因']}")
    print(f"\n【還沒勾】{len(cand)} 筆")
    for reason in sorted({it["擋下原因"] or "（可以貼，在審核表「張貼」欄打 OK）"
                          for it in cand}):
        n = sum(1 for it in cand
                if (it["擋下原因"] or "（可以貼，在審核表「張貼」欄打 OK）") == reason)
        print(f"  {n:>3} 筆  {reason}")
    print("\n（這只是預覽，什麼都沒貼。）")


if __name__ == "__main__":
    main()
