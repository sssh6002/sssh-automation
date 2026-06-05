"""
document_closure_post_web.py
結案存查下載後，若總結「承辦文字」含「於官網公告」，把公文發佈到松高校網
(www.sssh.tp.edu.tw)：以總結的「主旨」為標題、主旨下方的「條列摘要」為內容。

設計見 docs/superpowers/specs/2026-06-03-closure-post-web-design.md。

呼叫方式：
  1) 結案存查流程中自動串接（document_closure._process_one_pending_closure_doc）：
       maybe_post_announcement(driver, extract_dir)
  2) 單獨執行（只對指定的「一個」公文目錄）：
       C:\\Python314\\python.exe document_closure/document_closure_post_web.py <公文目錄>
"""

import glob
import html
import os
import re
from datetime import datetime

# 觸發關鍵字：總結「承辦文字」(## 行) 含此字串才發佈到校網。
ANNOUNCE_KEYWORD = "於官網公告"

# 主旨行：兼容有無 * 前綴、半/全形冒號、冒號前後空白。
_SUBJECT_RE = re.compile(r'^\*?\s*主旨\s*[:：]\s*(.+)$')
# 條列行：1. / 1、 / 1.（後接內容，可無空白）。
_BODY_ITEM_RE = re.compile(r'^\s*\d+\s*[.、]\s*.+$')


def _parse_summary_text(text):
    """解析總結.md 文字，回 {'handling', 'title', 'body'} 或 None。

    - handling（承辦文字）：第一個以 `##` 開頭的行（## 或 ### 皆以 ## 開頭，取最先出現的，
      即承辦文字那行），去掉開頭所有 # 與前後空白；無此行則為 None。
    - title（主旨）：第一個 `主旨[:：]` 行冒號後的文字。
    - body（條列摘要）：主旨行之後、檔尾之前，所有 `數字.`／`數字、` 開頭的條列行，
      原樣（strip 過）以換行串接。

    主旨或 body 任一缺 → 回 None（寧缺勿發殘缺公告）。
    """
    handling = None
    title = None
    body_lines = []
    subject_seen = False

    for raw in text.splitlines():
        line = raw.strip()
        if handling is None and line.startswith("##"):
            handling = line.lstrip("#").strip() or None
            continue
        if title is None:
            m = _SUBJECT_RE.match(line)
            if m:
                title = m.group(1).strip()
                subject_seen = True
                continue
        if subject_seen and _BODY_ITEM_RE.match(line):
            body_lines.append(line)

    if title is None or not body_lines:
        return None
    return {"handling": handling, "title": title, "body": "\n".join(body_lines)}


def _parse_summary(extract_dir):
    """從 extract_dir 內 *總結.*.md 讀檔並解析。回 dict 或 None。"""
    summaries = sorted(glob.glob(os.path.join(extract_dir, "*總結.*.md")))
    if not summaries:
        return None
    try:
        text = open(summaries[0], encoding="utf-8").read()
    except OSError:
        return None
    return _parse_summary_text(text)


def _should_post(summary):
    """承辦文字是否含「於官網公告」→ 該不該發佈到校網。summary 為 None / handling
    為 None / 不含關鍵字 → False。"""
    if not summary:
        return False
    handling = summary.get("handling")
    return bool(handling) and ANNOUNCE_KEYWORD in handling


def _body_to_html(body):
    """把條列摘要(每行一條)轉成 CKEditor 可吃的 HTML 段落,逐行一個 <p>。

    跳過空行;對每行做 HTML escape,避免內容含 < & 破版。
    """
    lines = [ln for ln in body.split("\n") if ln.strip()]
    return "".join(f"<p>{html.escape(ln)}</p>" for ln in lines)


def _posted_marker_path(extract_dir):
    """回 <公文主檔名>已公告.txt 完整路徑;找不到主檔名回 None。"""
    from document_closure.document_closure import _find_main_doc_basename  # 避免循環 import
    base = _find_main_doc_basename(extract_dir)
    if not base:
        return None
    return os.path.join(extract_dir, f"{base}已公告.txt")


def _already_posted(extract_dir):
    """extract_dir 是否已有 <主檔名>已公告.txt(已公告過)。"""
    p = _posted_marker_path(extract_dir)
    return bool(p) and os.path.isfile(p)


def _write_posted_marker(extract_dir):
    """寫 <主檔名>已公告.txt(內容 ISO8601_已公告)。成功回路徑,否則 None。"""
    p = _posted_marker_path(extract_dir)
    if not p:
        print("      [WARN] 找不到公文主檔名,無法寫已公告標記")
        return None
    content = datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + "_已公告"
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"      OK:已寫已公告標記檔 {os.path.basename(p)}(內容: {content!r})")
        return p
    except OSError as e:
        print(f"      [WARN] 寫已公告標記失敗:{type(e).__name__}: {e}")
        return None
