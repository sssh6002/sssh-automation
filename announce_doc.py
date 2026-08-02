# -*- coding: utf-8 -*-
"""
announce_doc.py
公告文案產生器（3/4 批次）。完全離線,不碰 edoc、不用讀卡機。

    python announce_doc.py              產生所有「該公告且公告欄還空著」的文案
    python announce_doc.py --limit 3    只跑 3 筆(先試水溫)
    python announce_doc.py --dry        只列出會跑哪幾筆,不叫 LLM
    python announce_doc.py --show MWAA…  把某筆已產生的文案印出來看
    python announce_doc.py --overwrite  連已有文案的也重產(會蓋掉你改過的,慎用)

風格與判斷全在 announce_doc.md,程式不做任何文字判斷 —— 要調口氣就改那份 md。

哪些公文會被處理:
  - 承辦文字含「公佈周知／於官網公告／於校網公告」(即 _should_post 為真)
  - 且審核表「公告」欄還是空的
不用公告的(本校目前無參加計畫、只轉知科別、請自行填寫…)一律不處理,不花 token。
"""

import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import review_sheet as rs  # noqa: E402

SPEC_MD = os.path.join(_BASE_DIR, "announce_doc.md")

# 公告文案預設走比較好的模型。理由:公告全校師生家長都看得到,而摘要只有承辦人
# 自己看 —— 沒必要為了公告把摘要一起變貴。實測 haiku 守不住 announce_doc.md 的
# 細規格(該有的區塊會漏、單條還是編號)。
# 想改:env.env 加一行 announce_claude_model=opus(或 haiku/fable);或跑時加 --model。
ANNOUNCE_MODEL_DEFAULT = "sonnet"


def announce_model():
    from summarize_doc import _read_config
    return _read_config("announce_claude_model") or ANNOUNCE_MODEL_DEFAULT

# LLM 偶爾會加開場白 / 用 ``` 包起來 — 清掉再存
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$", re.S)
_TITLE_RE = re.compile(r"^【.+?】")


def _should_announce(doc_dir):
    """沿用校網張貼那支的判斷,不要另立一套標準。"""
    from document_closure.document_closure_post_web import _parse_summary, _should_post
    return _should_post(_parse_summary(doc_dir))


_CJK_NUM_RE = re.compile(r"^[*\s]*([一二三四五六七八九十]+)、\s*(.+?)[*\s]*$")
# 區塊標題行:【…】前後只准有 emoji／符號／空白／markdown 星號。
# 不用 `.{0,3}` — 實測 LLM 會吐 `**⚠️【注意事項】**`、`⚠️ 【注意事項】` 等變形,
# 長度限制會漏接,漏接就整段併進上一區、單條編號拿不掉。
_BLOCK_RE = re.compile(r"^[^\w一-鿿]{0,8}【.+?】[^\w一-鿿]{0,8}$")


def drop_lone_enumerator(text):
    """區塊內只有「一、」一條時,拿掉那個編號 —— 只有一條還編號很怪。

    規格(announce_doc.md)有寫，但小模型常常照舊編號；這是唯一正解的格式規則，
    與其求 LLM 聽話，不如程式收尾。兩條以上一律不動。
    """
    lines = text.split("\n")
    starts = [i for i, ln in enumerate(lines) if _BLOCK_RE.match(ln.strip())]
    starts.append(len(lines))
    for a, b in zip(starts, starts[1:]):
        idx = [i for i in range(a + 1, b) if _CJK_NUM_RE.match(lines[i].strip())]
        if len(idx) == 1:
            m = _CJK_NUM_RE.match(lines[idx[0]].strip())
            if m.group(1) == "一":
                lines[idx[0]] = m.group(2)
    return "\n".join(lines)


def clean_response(text):
    """把 LLM 回應清成純公告本文。抓不到標題開頭則回 None(視為格式不符)。"""
    if not text:
        return None
    s = text.strip()
    m = _FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()
    # 丟掉標題行之前的任何前言
    i = s.find("【")
    if i < 0:
        return None
    s = s[i:]
    if not _TITLE_RE.match(s):
        return None
    return drop_lone_enumerator(s.strip())


def build_prompt(spec_text, content_txt, subject):
    return (
        f"{spec_text}\n\n"
        f"===== 以下是要改寫的公文 =====\n"
        f"主旨：{subject}\n\n"
        f"{content_txt}\n"
        f"===== 公文結束 =====\n\n"
        f"依上方規格輸出公告本文。第一個字必須是「【」。"
    )


def _pii_marker(doc_dir):
    """該公文是否已被個資把關擋下（目錄內有 *含個資.txt）。"""
    import glob
    return bool(glob.glob(os.path.join(doc_dir, "*含個資.txt")))


def _content_of(doc_dir):
    import glob
    hits = sorted(glob.glob(os.path.join(doc_dir, "*內容.txt")))
    if not hits:
        return None
    return open(hits[0], encoding="utf-8").read()


def plan(path=None, overwrite=False, only=None):
    """回 (要跑的清單, 略過清單)。

    only  — 只處理這些文號(可為單一字串或清單)。重產文案時建議指名對象,
            不要用 --limit 亂槍打鳥。
    護欄 — 「張貼」或「陳會」欄已打 OK 的列,**即使 overwrite=True 也不動**。
            打了 OK 代表承辦人審過、那是定稿,程式沒有權力蓋掉。
            (2026-07-28 教訓:--overwrite --limit 1 把承辦人手寫的公告蓋掉三次。)
    """
    if isinstance(only, str):
        only = [only]
    only = {s.strip() for s in only} if only else None

    todo, skip = [], []
    for row in rs.rows(path):
        doc_no = str(row["文號"]).strip()
        if only is not None and doc_no not in only:
            continue
        d = None
        for base in rs.SCAN_DIRS:
            if not os.path.isdir(base):
                continue
            for n in sorted(os.listdir(base)):
                if doc_no in n and os.path.isdir(os.path.join(base, n)):
                    d = os.path.join(base, n)
                    break
            if d:
                break
        item = {"文號": doc_no, "主旨": row.get("主旨") or "", "目錄": d}
        approved = [g for g in rs.GATES if rs.is_approved(row.get(g))]
        if d is None:
            item["略過"] = "找不到公文目錄"
        elif rs.is_done(row.get("張貼")):
            item["略過"] = "你已自己辦完（張貼欄標為已辦）"
        elif approved and (row.get("公告") or "").strip():
            item["略過"] = f"你已在「{'/'.join(approved)}」打 OK — 定稿不覆蓋"
        elif (row.get("公告") or "").strip() and not overwrite:
            item["略過"] = "公告欄已有內容"
        elif not _should_announce(d):
            item["略過"] = "擬辦不是要公告"
        elif _pii_marker(d):
            item["略過"] = "含個資，未送 AI（見資料夾內 *含個資.txt）"
        elif not _content_of(d):
            item["略過"] = "沒有 內容.txt"
        else:
            todo.append(item)
            continue
        skip.append(item)
    return todo, skip


def generate(item, spec_text, model=None):
    """跑一筆。回 (公告文字, backend, model) 或 (None, None, None)。

    model 只在本次呼叫期間覆寫 claude backend 的模型,不影響 summarize_doc 自己
    跑摘要時的設定(env.env 一個字都不用改)。
    """
    import summarize_doc
    from summarize_doc import _call_backends
    content = _content_of(item["目錄"])
    prompt = build_prompt(spec_text, content, item["主旨"])
    summarize_doc._CONFIG_OVERRIDE["summarize_claude_model"] = model or announce_model()
    try:
        return _generate_with(prompt, _call_backends, source_text=content)
    finally:
        summarize_doc._CONFIG_OVERRIDE.pop("summarize_claude_model", None)


def _generate_with(prompt, _call_backends, source_text=None):
    for attempt in (1, 2):
        text, backend, model = _call_backends(prompt)
        if not text:
            print("      [ERROR] 所有 LLM backend 都不可用")
            return None, None, None
        out = clean_response(text)
        if out:
            out = _flag_stray_links(out, source_text)
            return out, backend, model
        print(f"      [WARN] 第 {attempt}/2 次回應格式不符（開頭不是【），重試")
    return None, None, None


def _flag_stray_links(out, source_text):
    """公告裡出現來文沒有的網址 → 在文末標出來,不自行刪除。

    兩種成因都不能貼上校網:模型幻覺,或來文 PDF 裡藏了指令要它換網址。
    但也不該由程式擅自刪 —— 標出來讓承辦人判斷。
    """
    if not source_text:
        return out
    from content_guard import verify_links
    stray = verify_links(source_text, out)
    if not stray:
        return out
    print(f"      [WARN] 公告出現來文沒有的網址,已標註:{stray}")
    warn = "\n\n⚠️【待查核】以下網址在來文中找不到，貼出前請確認：\n" + \
           "\n".join(f"　· {u}" for u in stray)
    return out + warn


def main():
    import argparse
    ap = argparse.ArgumentParser(description="產生校網公告文案,寫回審核表「公告」欄")
    ap.add_argument("--limit", type=int, help="最多跑幾筆")
    ap.add_argument("--dry", action="store_true", help="只列清單,不叫 LLM")
    ap.add_argument("--overwrite", action="store_true", help="連已有文案的也重產")
    ap.add_argument("--show", metavar="文號", help="印出審核表裡該筆的公告欄")
    ap.add_argument("--path", help="審核表路徑")
    ap.add_argument("--model", help=f"這次改用的模型（預設 {ANNOUNCE_MODEL_DEFAULT}）")
    ap.add_argument("--only", nargs="+", metavar="文號",
                    help="只跑指定文號（重產時請用這個，不要用 --limit）")
    a = ap.parse_args()

    if a.show:
        for row in rs.rows(a.path):
            if a.show in str(row["文號"]):
                print(row.get("公告") or "(公告欄是空的)")
                return
        print(f"審核表裡找不到 {a.show}")
        return

    todo, skip = plan(a.path, a.overwrite, a.only)
    if a.limit:
        todo = todo[:a.limit]

    print(f"【要產生】{len(todo)} 筆")
    for it in todo:
        print(f"  - {it['文號']}  {it['主旨'][:44]}")
    print(f"\n【略過】{len(skip)} 筆")
    for reason in sorted({it["略過"] for it in skip}):
        n = sum(1 for it in skip if it["略過"] == reason)
        print(f"  {n:>3} 筆  {reason}")

    if a.dry:
        print("\n--dry：沒有叫 LLM，什麼都沒做。")
        return
    if not todo:
        return

    spec_text = open(SPEC_MD, encoding="utf-8").read()
    use_model = a.model or announce_model()
    print(f"\n公告文案使用模型：{use_model}（摘要不受影響，仍照 env.env 設定）")
    ok = 0
    for i, it in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}] {it['文號']} {it['主旨'][:32]}")
        text, backend, model = generate(it, spec_text, model=use_model)
        if not text:
            print("      x  產生失敗，跳過這筆")
            continue
        print(f"      OK ({backend}/{model}) {len(text)} 字")
        try:
            rs.upsert({"文號": it["文號"], "公告": text},
                      path=a.path, overwrite=a.overwrite)
        except PermissionError as e:
            print(f"\n[announce_doc] {e}")
            print(f"[announce_doc] 已產生 {ok} 筆但寫不進檔，關掉 Excel 後重跑。")
            raise SystemExit(1)
        ok += 1
    print(f"\n===== 完成 {ok}/{len(todo)} 筆，已寫入審核表「公告」欄 =====")


if __name__ == "__main__":
    main()
