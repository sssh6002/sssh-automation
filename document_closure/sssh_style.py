# -*- coding: utf-8 -*-
"""
sssh_style.py
把公文總結的條列文字轉成「松高風格表格卡片」HTML fragment(inline style)。

規格來源:資媒組長的模板提示詞(2026-07-27)。原提示詞是給 ChatGPT 用的,這裡改成
純 Python 決定性產出 — 因為規格第 10 條「文字:完全不修改」,不需要 LLM 介入,
省一次 API 呼叫,也保證不會被改字。

輸入(body)沿用 *總結.*.md 的條列格式,並額外支援人工潤稿時加的結構:
    【小標】或 ## 小標   → 新開一張卡片,此行當卡片標題
    1. / 一、 / - / •    → 條列項目
    純文字行             → 段落
    行內 https://...     → 自動轉超連結;markdown [文字](網址) 亦可
    同一區塊內 >=2 行帶連結 → 依規格第 6 條自動改排成表格

輸出為 fragment(<section> 起頭),不含 <html>/<head>,不含 <style> 區塊,
所有樣式走 inline style — 規格第 14 條。
"""

import html
import re

# ── 規格 1:主題色 ──────────────────────────────────────────────────────────
THEME = "#0a5c44"
# 表格首列柔和底色。刻意選 #edf5f1 而非更深的綠:
# #0a5c44 對 #edf5f1 的對比 7.16:1,剛好過無障礙 AAA(一般文字需 7:1);
# 底色再深一階(#e8f2ee)只有 6.95:1,會卡在 AA。
TABLE_HEAD_BG = "#edf5f1"
GRAD_BG = "linear-gradient(180deg,#f4faf7 0%,#ffffff 100%)"
TEXT = "#1a1a1a"
BORDER = "#c9ded6"

# 校內網址前綴 — 規格第 12 條
SSSH_PREFIX = "https://www.sssh.tp.edu.tw"

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BARE_URL_RE = re.compile(r"(?<![\"'>=])(https?://[^\s<>　，。、）)]+)")
# 小標三種寫法都吃:
#   ## 公告內容
#   【公告內容】
#   📝【公告內容】     ← 承辦人慣用,emoji 保留在小標上
# group1=## 式標題, group2=emoji 前綴, group3=【】內文字, group4=同行剩餘內容
_HEADING_RE = re.compile(r"^(?:##\s*(.+)|([^\w\s【]{0,3})\s*【(.+?)】\s*(.*))$")
_ITEM_RE = re.compile(r"^(?:\d+[.、)]|[一二三四五六七八九十]+[、.]|[-•*])\s*(.+)$")

_S = {  # 共用 inline style 片語
    "card": ("background:#ffffff;border-radius:14px;"
             "box-shadow:0 2px 10px rgba(10,92,68,0.12);"
             "padding:20px 24px;margin:0 auto 20px auto;max-width:900px;"
             "text-align:left;line-height:1.9;"),
    "h2": (f"color:{THEME};font-size:1.5em;font-weight:700;text-align:center;"
           "margin:0 0 16px 0;line-height:1.6;"),
    "h3": (f"color:{THEME};font-size:1.15em;font-weight:700;text-align:center;"
           "margin:0 0 12px 0;line-height:1.6;"),
    "p": f"color:{TEXT};margin:0 0 10px 0;line-height:1.9;text-align:left;",
    "ul": f"color:{TEXT};margin:0 0 10px 0;padding-left:1.6em;line-height:1.9;",
    "li": "margin:0 0 6px 0;text-align:left;",
    "table": (f"width:100%;border-collapse:collapse;margin:0 0 10px 0;"
              f"border:1px solid {BORDER};line-height:1.9;"),
    "th": (f"background:{TABLE_HEAD_BG};color:{THEME};font-weight:700;"
           f"text-align:center;padding:10px 12px;border:1px solid {BORDER};"),
    "td": (f"color:{TEXT};text-align:left;padding:10px 12px;"
           f"border:1px solid {BORDER};vertical-align:top;"),
    # 規格 11:連結加粗、顏色同標題。hover 樣式做不到(見模組說明),改為常駐底線 —
    # 無障礙規範本來就要求連結不可只靠顏色與內文區分。
    "a": (f"color:{THEME};font-weight:700;text-decoration:underline;"),
    "sr": ("position:absolute;width:1px;height:1px;padding:0;margin:-1px;"
           "overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;"),
}


def _esc(s):
    return html.escape(s, quote=True)


def _anchor(url, text, sssh_to_hash=False):
    """產一個 <a>。規格 12/13:校內連結可改 #、一律另開新視窗並標示。

    「另開新視窗」不用 <a alt="">(HTML 沒有這個屬性、讀屏軟體不會唸),改用
    title + 視覺隱藏文字,這才是無障礙檢測認可的寫法。
    """
    href = "#" if (sssh_to_hash and url.startswith(SSSH_PREFIX)) else url
    return (f'<a href="{_esc(href)}" target="_blank" rel="noopener noreferrer" '
            f'title="另開新視窗" style="{_S["a"]}">{_esc(text)}'
            f'<span style="{_S["sr"]}">（另開新視窗）</span></a>')


def _linkify(line, sssh_to_hash=False):
    """把一行純文字轉成 HTML:先吃 markdown 連結,再吃裸網址,其餘 escape。"""
    out = []
    pos = 0
    marks = []
    for m in _MD_LINK_RE.finditer(line):
        marks.append((m.start(), m.end(), m.group(1), m.group(2)))
    for m in _BARE_URL_RE.finditer(line):
        if any(s <= m.start() < e for s, e, _, _ in marks):
            continue
        marks.append((m.start(), m.end(), m.group(1), m.group(1)))
    for start, end, text, url in sorted(marks):
        if start < pos:
            continue
        out.append(_esc(line[pos:start]))
        out.append(_anchor(url, text, sssh_to_hash))
        pos = end
    out.append(_esc(line[pos:]))
    return "".join(out)


def _split_blocks(body):
    """把 body 切成 [(小標 or None, [內容行, ...]), ...]。"""
    blocks = [(None, [])]
    for raw in body.split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = _HEADING_RE.match(line)
        if m:
            if m.group(1):
                head, rest = m.group(1).strip(), ""
            else:
                head = f"{(m.group(2) or '').strip()} {m.group(3).strip()}".strip()
                rest = (m.group(4) or "").strip()
            blocks.append((head, []))
            if rest:
                blocks[-1][1].append(rest)
            continue
        blocks[-1][1].append(line)
    return [(h, ls) for h, ls in blocks if ls or h]


def _has_link(line):
    return bool(_MD_LINK_RE.search(line) or _BARE_URL_RE.search(line))


def _split_item(line):
    """條列行拆成 (項目文字, 內容)。用全形／半形冒號或第一個空白切;切不出來回 (None, line)。"""
    m = _ITEM_RE.match(line)
    text = m.group(1) if m else line
    for sep in ("：", ":"):
        if sep in text:
            left, right = text.split(sep, 1)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
    return None, text


def _render_table(lines, sssh_to_hash):
    """規格 6/8:條列式帶超連結 → 兩欄表格(項目 / 連結),有內格線、首列柔和底色。"""
    rows = []
    labelled = 0
    for ln in lines:
        label, content = _split_item(ln)
        if label:
            labelled += 1
        rows.append((label, content))
    # 拆不出「項目：內容」的比例太高就退回單欄表格,免得整欄空白。
    two_col = labelled >= max(1, len(rows) // 2)
    out = [f'<table style="{_S["table"]}">']
    if two_col:
        out.append(f'<thead><tr><th scope="col" style="{_S["th"]};width:30%;">項目</th>'
                   f'<th scope="col" style="{_S["th"]}">內容</th></tr></thead><tbody>')
        for label, content in rows:
            out.append(f'<tr><td style="{_S["td"]}">{_esc(label or "")}</td>'
                       f'<td style="{_S["td"]}">{_linkify(content, sssh_to_hash)}</td></tr>')
    else:
        out.append(f'<thead><tr><th scope="col" style="{_S["th"]}">內容</th></tr></thead><tbody>')
        for _, content in rows:
            out.append(f'<tr><td style="{_S["td"]}">{_linkify(content, sssh_to_hash)}</td></tr>')
    out.append("</tbody></table>")
    return "".join(out)


def _render_block(head, lines, sssh_to_hash):
    inner = []
    if head:
        inner.append(f'<h3 style="{_S["h3"]}">{_esc(head)}</h3>')

    items = [ln for ln in lines if _ITEM_RE.match(ln)]
    # 規格 6:條列式且「有超連結」→ 改成表格;否則維持原樣式(<ul>)。
    if len(items) >= 2 and sum(1 for ln in items if _has_link(ln)) >= 2:
        inner.append(_render_table(items, sssh_to_hash))
        rest = [ln for ln in lines if ln not in items]
    else:
        rest = lines
        if items:
            lis = "".join(f'<li style="{_S["li"]}">'
                          f'{_linkify(_ITEM_RE.match(ln).group(1), sssh_to_hash)}</li>'
                          for ln in items)
            inner.append(f'<ul style="{_S["ul"]}">{lis}</ul>')
            rest = [ln for ln in lines if ln not in items]
    for ln in rest:
        inner.append(f'<p style="{_S["p"]}">{_linkify(ln, sssh_to_hash)}</p>')
    return f'<div style="{_S["card"]}">' + "".join(inner) + "</div>"


def render_fragment(title, body, sssh_to_hash=False, include_title=True):
    """回「松高風格表格卡片」HTML fragment。

    title  — 公告大標題(通常是公文主旨),置中、主題色、加大。
    body   — 總結條列文字(可人工潤過)。
    sssh_to_hash — True 時把 https://www.sssh.tp.edu.tw 開頭的連結換成 #(規格 12)。
                   預設 False,因為換成 # 會讓校內連結失效,交由呼叫端決定。
    include_title — 是否在內容區再放一次大標題。校網布告欄本身就有標題列,
                    從發佈流程呼叫時傳 False 免得標題出現兩次。
    """
    blocks = _split_blocks(body or "")
    cards = "".join(_render_block(h, ls, sssh_to_hash) for h, ls in blocks)
    head = f'<h2 style="{_S["h2"]}">{_esc(title)}</h2>' if include_title and title else ""
    return (f'<section style="background:{GRAD_BG};padding:24px 16px;'
            f'font-family:\'Microsoft JhengHei\',\'Noto Sans TC\',sans-serif;">'
            f'{head}{cards}</section>')


if __name__ == "__main__":
    import sys, io, glob, os
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from document_closure.document_closure_post_web import _parse_summary

    target = sys.argv[1] if len(sys.argv) > 1 else \
        "document_download/MWAA1156007093"
    s = _parse_summary(target)
    if not s:
        print(f"[ERROR] {target} 沒有可解析的總結"); sys.exit(1)
    frag = render_fragment(s["title"], s["body"])
    out = os.path.join(os.environ.get("TEMP", "."), "sssh_style_preview.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write('<!doctype html><meta charset="utf-8">'
                '<title>松高風格預覽</title>' + frag)
    print(frag)
    print(f"\n[預覽檔] {out}")
