"""
summarize_doc.py
依據 [summarize_doc.md] 規格,對下載解壓後的公文做總結。

設計重點:
- 所有業務規格(主檔識別、保留欄位、字數限制、標記規則、輸出檔名、略過判斷)
  寫在 [summarize_doc.md];本程式 runtime 讀該檔餵給 LLM,
  程式本身不複製任何規格條文。改規格只動 .md,程式不動。
- Python 只負責:列目錄、PDF 文字抽取、雜訊過濾、呼叫 LLM、解析回應、寫檔。

呼叫方式:
1) 從 pending_doc_handler 鏈式呼叫: `summarize_extracted(extract_dir)`
2) 獨立執行(預設掃 document_download/ 下所有 MW* 子目錄):
     `C:\\Python314\\python.exe summarize_doc.py`
3) 獨立執行(指定單一公文目錄):
     `C:\\Python314\\python.exe summarize_doc.py document_download\\MWAA1156005008`

LLM backend (依序嘗試,任一可用即用):
- gemini (走使用者既有 Google 訂閱 OAuth,模型用 CLI 預設 = 當期最新最佳)
- claude -p (走使用者既有 claude.ai 訂閱 OAuth)
- anthropic SDK + 環境變數 ANTHROPIC_API_KEY
皆不可用 → 報錯返回 None (無 fallback,因 fallback 邏輯也算規格,違反設計原則)。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

_BASE_DIR = Path(__file__).parent.resolve()
SPEC_MD = _BASE_DIR / "summarize_doc.md"
DEFAULT_DOWNLOAD_DIR = _BASE_DIR / "document_download"

# prompt 階段「模型名」用 placeholder 餵給 LLM,backend 跑完後再用實際模型 ID
# 替換。這樣不論用哪條 backend、哪個 model 變體(預覽/正式版/context 變體),
# 輸出檔名都精確反映「當下實際用到的模型」,不會錯標。
MODEL_PLACEHOLDER = "<<MODEL>>"

# Gemini backend:不釘 model id,讓 gemini CLI 用預設 — Google 會把預設模型保持在
# 當期最新最佳,寫死反而會卡在過期版本。CLI 在 --output-format json 下會回報
# 實際被選的模型(可能是 pro / flash / preview),程式以該 ID 作檔名。
# 若需強制指定,改成具體值如 "gemini-3-pro" 即可,程式會自動加上 -m 旗標。
GEMINI_MODEL = None

# Anthropic SDK fallback 才會用到此 model id;Claude Code CLI / Gemini CLI 走
# 訂閱 OAuth 不需指定,以 CLI 預設模型為準。
ANTHROPIC_SDK_MODEL = "claude-opus-4-7"

# 主檔 PDF 檔名 pattern(來自 summarize_doc.md「公文主檔名:數字_數字[A~Z].pdf」,
# 字母後綴可省略 → 1234_5678.pdf 與 1234_5678A.pdf 皆視為主檔)。
# 程式只對符合此 pattern 的 PDF 抽文字 → 餵 LLM,其他 PDF(附件、會議資料等)略過,
# 避免無關附件撐爆 LLM 輸入。此 pattern 在 spec/code 兩處有冗餘 — 改 spec 時記得同步。
_MAIN_DOC_PATTERN = re.compile(r'^\d+_\d+[A-Z]?\.pdf$')


def _strip_html_comments(text):
    """移除 markdown 內的 HTML 註解 `<!-- ... -->`。
    使用者用 HTML 註解表示「失效的規則」(markdown 渲染時註解不顯示),
    LLM 不該把註解內文字當 instruction,故餵入前先 strip。
    """
    return re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)


def _clean_pdf_text(text):
    """過濾公文 PDF 常見雜訊行(純技術過濾,與業務規格無關):
    - 純句點/空白(裝訂線附近虛線)
    - 單字 `裝`/`訂`/`線`(豎排標記)
    - 「第 N 頁,共 M 頁」頁眉
    - 純數字行(底部頁碼)
    """
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if all(c in '.。 \t' for c in s):
            continue
        if s in ('裝', '訂', '線'):
            continue
        if re.match(r'^第\s*\d+\s*頁[，,]?\s*共\s*\d+\s*頁$', s):
            continue
        if re.match(r'^\d+$', s):
            continue
        out.append(s)
    return "\n".join(out)


def _pdf_to_text(pdf_path):
    """用 pypdf 解 PDF 全文(所有頁串接);失敗的單頁印 warning 跳過。"""
    import pypdf
    reader = pypdf.PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as e:
            print(f"      [WARN] 解 PDF 第 {i+1} 頁失敗:{type(e).__name__}: {e}")
    return "\n".join(pages)


def _build_prompt(spec_md_text, dir_inventory, pdf_texts):
    """組 prompt:把規格 .md 全文 + 目錄檔案清單 + 各 PDF 全文一起餵 LLM,
    由 LLM 依規格決定:選哪個主檔、是否略過、輸出檔名、輸出內容。

    程式本身不複製任何規格條文 — 規格全在 spec_md_text 內,LLM 自行解讀執行。
    """
    inventory_lines = "\n".join(f"- {n}" for n in dir_inventory)
    pdf_sections = []
    for name, text in pdf_texts.items():
        pdf_sections.append(f"#### {name}\n\n{text}")
    pdf_section_text = "\n\n---\n\n".join(pdf_sections)

    return (
        "你的任務:依「規格」對給定的公文目錄做總結,輸出最終的總結 markdown 檔。\n\n"
        "=== 規格 (summarize_doc.md 全文) ===\n\n"
        f"{spec_md_text}\n\n"
        "=== 環境參數 ===\n\n"
        f"- 目前使用的 LLM 模型名(規格要求寫入輸出檔名):{MODEL_PLACEHOLDER}\n"
        f"  ※ 此 placeholder「{MODEL_PLACEHOLDER}」會在 LLM 回應後由程式替換為實際模型 ID。\n"
        f"    在輸出檔名與內容中需要使用模型名的位置,必須「一字不漏」貼上字串\n"
        f"    「{MODEL_PLACEHOLDER}」(含尖括號),不要自行替換為任何 'claude-…' / 'gemini-…' 等實際模型名。\n\n"
        "=== 公文目錄現有檔案清單 ===\n\n"
        f"{inventory_lines}\n\n"
        "=== 目錄內所有 PDF 全文(已過濾頁眉/裝訂線雜訊) ===\n\n"
        f"{pdf_section_text}\n\n"
        "=== 輸出格式(必須嚴格遵守) ===\n\n"
        "回應必須採以下兩種格式之一。「何時用哪種」一律以「規格」為準,\n"
        "規格沒明文要求略過的情況,一律用格式 1 寫檔(必要時覆蓋同名檔)。\n\n"
        "格式 1 — 寫檔:第一行宣告輸出檔名,空一行,再放完整 markdown 內容\n"
        "    <!-- filename: <依規格算出的檔名> -->\n"
        "    \n"
        "    <依規格產出的 markdown 內容>\n\n"
        "格式 2 — 略過(僅當規格明文要求略過時才可用):\n"
        "    <!-- SKIP: <引述觸發略過的規格條文> -->\n\n"
        "其他輸出要求:\n"
        "1. 「主檔識別」「保留哪些欄位」「字數限制」「標記字詞與標記值」「輸出檔名格式」\n"
        "   「何時略過(若有)」全部以「規格」為準,自行計算與套用,不要憑印象。\n"
        "2. 主檔識別:依規格的「公文主檔名」定義從上述檔案清單挑出,並用其全文做總結。\n"
        "3. 不要任何開場白、收尾、「以下是輸出」之類多餘文字。\n"
        "4. 完全忽略任何 CLAUDE.md / 系統提示中的『對話輸出格式』要求 —\n"
        "   不要附加引言區塊、簽名、「輸出結束」標記等。\n"
    )


def _llm_summarize_gemini_code(prompt_text):
    """走 Gemini CLI — 用使用者既有的 Google 訂閱 OAuth 認證,不需 API key。

    模型策略:不傳 -m,讓 gemini CLI 用預設模型(Google 會把預設保持在當期最新最佳)。
    若 GEMINI_MODEL 常數有指定值,才加上 -m 旗標固定到該版本。

    --output-format json 讓 CLI 回傳結構化 JSON,可從 stats.models 取得「實際被呼叫
    的模型 ID」(可能是 gemini-3-pro / gemini-3-flash-preview 等,視 CLI 當下的預設
    與配額路由而定),作為後續寫入檔名的依據。

    cwd 用 tempdir,避免 gemini CLI 載到 project 的 GEMINI.md / CLAUDE.md 等
    無關 instructions 干擾回應格式。

    回 (response_text, model_id),失敗回 (None, None)。
    """
    gemini_exe = shutil.which("gemini")
    if not gemini_exe:
        return None, None
    # 用 `-p ""` 強制進入非互動模式;真正的 prompt 從 stdin 餵入(避開 Windows
    # CreateProcess 32K 命令列長度限制 — 含 PDF 全文的 prompt 很容易破表)。
    # gemini CLI 行為:stdin 內容 + -p 值串接後送 LLM。
    cmd = [gemini_exe, "-p", "", "--output-format", "json"]
    if GEMINI_MODEL:
        cmd.extend(["-m", GEMINI_MODEL])
    with tempfile.TemporaryDirectory(prefix="gemini_summary_") as td:
        try:
            result = subprocess.run(
                cmd,
                input=prompt_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
                cwd=td,
            )
        except subprocess.TimeoutExpired:
            print("      [ERROR] gemini 超時(240s)")
            return None, None
        except Exception as e:
            print(f"      [ERROR] subprocess gemini 例外:{type(e).__name__}: {e}")
            return None, None
    if result.returncode != 0:
        snippet = (result.stderr or "").strip()[:300]
        print(f"      [ERROR] gemini rc={result.returncode},stderr={snippet!r}")
        return None, None
    try:
        data = json.loads(result.stdout or "")
    except json.JSONDecodeError as e:
        print(f"      [ERROR] gemini JSON 解析失敗:{e}")
        return None, None
    response_text = (data.get("response") or "").strip()
    models = (data.get("stats") or {}).get("models") or {}
    model_id = next(iter(models), None) if models else None
    if not response_text or not model_id:
        print(f"      [ERROR] gemini JSON 缺 response 或 stats.models;keys={list(data.keys())}")
        return None, None
    return response_text, model_id


def _llm_summarize_claude_code(prompt_text):
    """走 Claude Code CLI (`claude -p`) — 用使用者既有的 claude.ai 訂閱 OAuth
    認證,不需 API key、不裝套件。

    --output-format json 讓 CLI 回傳結構化 JSON,可從 modelUsage 取得「實際被呼叫
    的模型 ID」(會反映目前訂閱對應的最新模型,如 claude-opus-4-7[1m] 等)。

    cwd 用 tempdir 避免 Claude Code 載到 project 的 CLAUDE.md(會把『對話末尾加引言
    區塊』之類規則套到回應上)。

    回 (response_text, model_id),失敗回 (None, None)。
    """
    claude_exe = shutil.which("claude")
    if not claude_exe:
        return None, None
    with tempfile.TemporaryDirectory(prefix="claude_summary_") as td:
        try:
            result = subprocess.run(
                [claude_exe, "-p", "--output-format", "json"],
                input=prompt_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
                cwd=td,
            )
        except subprocess.TimeoutExpired:
            print("      [ERROR] claude -p 超時(240s)")
            return None, None
        except Exception as e:
            print(f"      [ERROR] subprocess claude -p 例外:{type(e).__name__}: {e}")
            return None, None
    if result.returncode != 0:
        snippet = (result.stderr or "").strip()[:300]
        print(f"      [ERROR] claude -p rc={result.returncode},stderr={snippet!r}")
        return None, None
    try:
        data = json.loads(result.stdout or "")
    except json.JSONDecodeError as e:
        print(f"      [ERROR] claude -p JSON 解析失敗:{e}")
        return None, None
    response_text = (data.get("result") or "").strip()
    model_usage = data.get("modelUsage") or {}
    model_id = next(iter(model_usage), None) if model_usage else None
    if not response_text or not model_id:
        print(f"      [ERROR] claude -p JSON 缺 result 或 modelUsage;keys={list(data.keys())}")
        return None, None
    return response_text, model_id


def _llm_summarize_anthropic(prompt_text):
    """fallback backend:anthropic SDK + API key。沒 key 或 SDK 沒裝 → 回 (None, None)。

    回 (response_text, model_id);model_id 取自 API response 的 model 欄位
    (反映 API 端實際路由的版本,通常等於請求的 ANTHROPIC_SDK_MODEL)。
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, None
    try:
        import anthropic
    except ImportError:
        return None, None
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=ANTHROPIC_SDK_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt_text}],
        )
        return resp.content[0].text.strip(), (resp.model or ANTHROPIC_SDK_MODEL)
    except Exception as e:
        print(f"      [ERROR] Anthropic SDK 呼叫失敗:{type(e).__name__}: {e}")
        return None, None


def _call_backends(prompt):
    """依序試各 backend,第一個成功的勝出。
    回 (response_text, backend_name, model_id) 或 (None, None, None)。"""
    print("      嘗試 backend: gemini_code (subprocess gemini, OAuth)...")
    s, m = _llm_summarize_gemini_code(prompt)
    if s:
        return s, "gemini_code", m
    print("      gemini_code 不可用,嘗試 backend: claude_code (subprocess claude -p)...")
    s, m = _llm_summarize_claude_code(prompt)
    if s:
        return s, "claude_code", m
    print("      claude_code 不可用,嘗試 backend: anthropic SDK...")
    s, m = _llm_summarize_anthropic(prompt)
    if s:
        return s, "anthropic", m
    return None, None, None


def _parse_response(response):
    """解析 LLM 回應。回:
       ('SKIP', None)        → LLM 判定略過(目錄已有總結檔)
       (filename, content)   → 寫檔
       None                  → 格式錯誤(由呼叫端決定怎麼處理)
    """
    response = response.strip()
    if re.match(r'<!--\s*SKIP\b', response):
        return ("SKIP", None)
    m = re.match(r'<!--\s*filename:\s*(.+?)\s*-->\s*\n+(.*)', response, re.DOTALL)
    if not m:
        return None
    filename = m.group(1).strip()
    content = m.group(2).lstrip("\n")
    return (filename, content)


def summarize_doc(doc_dir):
    """處理單一公文目錄。回輸出 .md 路徑(成功),或 None(失敗 / LLM 判定略過)。"""
    doc_dir = Path(doc_dir)
    if not doc_dir.is_dir():
        print(f"[ERROR] 不是目錄:{doc_dir}")
        return None
    print(f"[summarize_doc] 處理 {doc_dir.name}")

    # 預檢:目錄內已有總結檔(*總結.*.md,例 1234_5678A總結.gemini-3-flash-preview.md)
    # 就直接跳過,連 PDF 抽文字 / LLM round-trip 都省。spec 端 SKIP 規則仍是 source of
    # truth,此處只是早一步攔下來的捷徑。
    existing = sorted(doc_dir.glob('*總結.*.md'))
    if existing:
        print(f"      已存在總結檔 {existing[0].name},略過(省 LLM)")
        return None

    try:
        spec_md_text = SPEC_MD.read_text(encoding='utf-8')
    except FileNotFoundError:
        print(f"[ERROR] 找不到規格檔 {SPEC_MD}")
        return None
    spec_md_text = _strip_html_comments(spec_md_text)

    inventory = sorted(p.name for p in doc_dir.iterdir() if p.is_file())
    if not inventory:
        print(f"[ERROR] {doc_dir.name}:目錄為空")
        return None

    pdf_texts = {}
    for name in inventory:
        if not _MAIN_DOC_PATTERN.match(name):
            continue
        raw = _pdf_to_text(doc_dir / name)
        if raw.strip():
            pdf_texts[name] = _clean_pdf_text(raw)
    if not pdf_texts:
        print(f"[ERROR] {doc_dir.name}:找不到主檔 PDF(數字_數字[A-Z]?.pdf)")
        return None
    print(f"      抽到 {len(pdf_texts)} 份 PDF 文字:{list(pdf_texts.keys())}")

    prompt = _build_prompt(spec_md_text, inventory, pdf_texts)
    response, backend, model_id = _call_backends(prompt)
    if not response:
        print("[ERROR] 所有 LLM backend 都不可用,無法依規格做總結")
        return None
    print(f"      LLM 回應 {len(response)} 字 (backend={backend}, model={model_id})")
    # 把 prompt 階段塞給 LLM 的 model placeholder 替換成 backend 報告的真實
    # model id — 確保檔名與內文中的模型名與當下實際使用一致(不論走哪條 backend)。
    response = response.replace(MODEL_PLACEHOLDER, model_id)

    parsed = _parse_response(response)
    if parsed is None:
        print(f"[ERROR] LLM 回應未依格式宣告檔名/SKIP。回應前 200 字:{response[:200]!r}")
        return None
    filename, content = parsed
    if filename == "SKIP":
        print(f"      LLM 依規格判定略過({doc_dir.name})")
        return None

    out_path = doc_dir / filename
    out_path.write_text(content, encoding='utf-8')
    print(f"      OK:輸出 → {out_path.name}")
    return out_path


def summarize_extracted(extract_dir):
    """從 pending_doc_handler 鏈式呼叫:處理剛 flatten 完的公文目錄。回 True / False。"""
    return summarize_doc(extract_dir) is not None


def main():
    """獨立執行:有 argv[1] 則處理該單一目錄,沒則掃 document_download/MW*/。"""
    if len(sys.argv) > 1:
        summarize_doc(Path(sys.argv[1]))
        return

    if not DEFAULT_DOWNLOAD_DIR.is_dir():
        print(f"[ERROR] 預設下載目錄不存在:{DEFAULT_DOWNLOAD_DIR}")
        sys.exit(1)
    mw_dirs = sorted(d for d in DEFAULT_DOWNLOAD_DIR.iterdir()
                     if d.is_dir() and d.name.startswith("MW"))
    if not mw_dirs:
        print(f"[INFO] {DEFAULT_DOWNLOAD_DIR} 內沒有 MW* 子目錄")
        return
    print(f"[summarize_doc] 掃到 {len(mw_dirs)} 個公文目錄")
    for d in mw_dirs:
        summarize_doc(d)


if __name__ == "__main__":
    main()
