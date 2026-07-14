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
     `py summarize_doc.py`
3) 獨立執行(指定單一公文目錄):
     `py summarize_doc.py document_download\\MWAA1156005008`

LLM backend (順序由 env.env 的 summarize_llm_order 決定,預設見 DEFAULT_LLM_ORDER;
依序嘗試,第一個成功的勝出):
- antigravity : Google Antigravity 的 agy CLI (走 Google 帳號 OAuth、免費)。取代已被
                Google 於 2026-06-18 停用免費 OAuth 的舊 gemini CLI。agy 在 non-TTY 下
                會把回應從 stdout 丟掉,故用 pywinpty 把它包進 ConPTY 取回 (見 _run_agy_pty)。
- aistudio    : Google AI Studio (Gemini) REST API (免裝 SDK,stdlib urllib)。key 依序取自
                env.env google_ai_studio_api_key / 環境變數 GEMINI_API_KEY /
                ~/.claude/shared-credentials.md。
- claude      : claude -p CLI (走使用者既有 claude.ai 訂閱 OAuth)。
- anthropic   : anthropic SDK,key 取自 env.env anthropic_api_key 或環境變數 ANTHROPIC_API_KEY。
全部不可用 → 報錯返回 None。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

_BASE_DIR = Path(__file__).parent.resolve()
SPEC_MD = _BASE_DIR / "summarize_doc.md"
DEFAULT_DOWNLOAD_DIR = _BASE_DIR / "document_download"
ENV_FILE = _BASE_DIR / "env.env"
# 共用帳密集中檔 (機密、僅本機、絕不 commit/log)。aistudio backend 的 Gemini key 預設由此讀。
SHARED_CRED_FILE = Path(os.path.expanduser("~")) / ".claude" / "shared-credentials.md"

# prompt 階段「模型名」用 placeholder 餵給 LLM,backend 跑完後再用實際模型 ID
# 替換。這樣不論用哪條 backend、哪個 model 變體(預覽/正式版/context 變體),
# 輸出檔名都精確反映「當下實際用到的模型」,不會錯標。
MODEL_PLACEHOLDER = "<<MODEL>>"

# 公文總結 LLM backend 預設順序 (env.env summarize_llm_order 未設/無效時用)。
DEFAULT_LLM_ORDER = ("antigravity", "aistudio", "claude", "anthropic")
_KNOWN_BACKENDS = set(DEFAULT_LLM_ORDER)

# aistudio (Gemini REST) 未指定 summarize_aistudio_model 時的預設模型
# (2026-06-24 實測此免費層 key 可用 gemini-2.5-flash)。
AISTUDIO_DEFAULT_MODEL = "gemini-2.5-flash"

# aistudio 暫時性錯誤重試:Gemini 免費層偶爾回 503(模型過載)/429(限流)等,
# 屬暫時性,重試多半就過。非這些碼(如 400/403)直接放棄。
_AISTUDIO_RETRY_STATUSES = {429, 500, 502, 503, 504}
_AISTUDIO_MAX_TRIES = 3

# antigravity (agy) 無 JSON 輸出、無法回報實際模型 → 未設 summarize_agy_model 時
# 檔名 (總結.<模型>.md) 用此標籤。
AGY_DEFAULT_MODEL_LABEL = "antigravity"

# agy 的 prompt 只能走命令列參數 (-p 不吃 stdin),受 Windows ~32767 命令列長度限制;
# 超過就讓位給下一棒 (aistudio/claude 走 API/stdin 無此限)。實測公文 prompt 僅約 5K。
_AGY_MAX_PROMPT = 30000

# Anthropic SDK fallback 才會用到此 model id;Claude Code CLI / Gemini CLI 走
# 訂閱 OAuth 不需指定,以 CLI 預設模型為準。
ANTHROPIC_SDK_MODEL = "claude-opus-4-7"

# 主檔 PDF 檔名 pattern(來自 summarize_doc.md「公文主檔名:數字_數字[A~Z].pdf」,
# 字母後綴可省略 → 1234_5678.pdf 與 1234_5678A.pdf 皆視為主檔)。
# 程式只對符合此 pattern 的 PDF 抽文字 → 餵 LLM,其他 PDF(附件、會議資料等)略過,
# 避免無關附件撐爆 LLM 輸入。此 pattern 在 spec/code 兩處有冗餘 — 改 spec 時記得同步。
_MAIN_DOC_PATTERN = re.compile(r'^\d+_\d+[A-Z]?\.pdf$')

# LLM(尤其 gemini-flash 類)對「輸出格式」的遵從是機率性的,偶爾會吐出 agentic
# 前綴(如 update_topic{...})或漏宣告檔名,導致 _parse_response 解析失敗。這類失敗
# 重試通常就能拿到正常回應。每個目錄最多嘗試 _SUMMARIZE_MAX_ATTEMPTS 次(= 首次
# + 多做兩次),任一次解析成功即停;全部失敗才放棄該目錄、繼續處理下一個。
_SUMMARIZE_MAX_ATTEMPTS = 3


# ─────────────────────────────────────────────────────────────────────────────
# 設定讀取 / backend 順序 / 金鑰解析 / 終端機跳脫碼清理
# ─────────────────────────────────────────────────────────────────────────────

def _read_config(key):
    """從 env.env 讀 key=value (# 開頭整行為註解、空行略過)。
    找不到 / 值為空 / 讀檔失敗都回 None。"""
    if not ENV_FILE.is_file():
        return None
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip() or None
    except Exception as e:
        print(f"      [WARN] 讀取 env.env 失敗:{type(e).__name__}: {e}")
    return None


def _parse_order(raw):
    """把 'antigravity,aistudio,...' 解析成已知 backend 名稱清單 (strip+小寫、濾未知、去重保序)。
    空字串 / None / 全未知 → 回 []。"""
    if not raw:
        return []
    out = []
    for tok in raw.split(","):
        name = tok.strip().lower()
        if name in _KNOWN_BACKENDS and name not in out:
            out.append(name)
    return out


def _get_backend_order():
    """回 backend 嘗試順序;env.env summarize_llm_order 未設/無效時用 DEFAULT_LLM_ORDER。"""
    order = _parse_order(_read_config("summarize_llm_order"))
    return order if order else list(DEFAULT_LLM_ORDER)


# 終端機跳脫碼:OSC (\x1b]...BEL/ST)、CSI (\x1b[...字母)、charset/keypad (\x1b= 等)。
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ANSI_ESC = re.compile(r"\x1b[=>()][0-9A-Za-z]?")


def _strip_ansi(text):
    r"""移除終端機跳脫序列與 \r。agy 走 ConPTY 取回的回應前後會夾終端機控制碼,需清掉。"""
    text = _ANSI_OSC.sub("", text)
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_ESC.sub("", text)
    return text.replace("\r", "")


_GEMINI_KEY_RE = re.compile(r"Gemini API Key.*?Key\*\*[：:]\s*`([^`]+)`", re.DOTALL)


def _parse_gemini_key_from_text(text):
    """從 shared-credentials.md 文字解析 Gemini API key (『…Gemini API Key』段的 **Key**)。
    找不到回 None。"""
    m = _GEMINI_KEY_RE.search(text)
    return m.group(1).strip() if m else None


def _read_shared_gemini_key():
    """讀 ~/.claude/shared-credentials.md 取 Gemini key。讀不到回 None。絕不把 key 寫進 log。"""
    try:
        if SHARED_CRED_FILE.is_file():
            return _parse_gemini_key_from_text(SHARED_CRED_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"      [WARN] 讀 shared-credentials 失敗:{type(e).__name__}: {e}")
    return None


def _resolve_gemini_key():
    """aistudio 用的 Gemini key:env.env google_ai_studio_api_key → 環境變數 GEMINI_API_KEY
    → ~/.claude/shared-credentials.md,取第一個有值的。"""
    return (_read_config("google_ai_studio_api_key")
            or os.environ.get("GEMINI_API_KEY")
            or _read_shared_gemini_key())


def _resolve_anthropic_key():
    """anthropic 用的 key:env.env anthropic_api_key → 環境變數 ANTHROPIC_API_KEY。"""
    return _read_config("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")


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
        "格式 1 — 寫檔(可一次輸出多個檔):\n"
        "    每個要產出的檔以 `<!-- filename: <檔名> -->` 起頭、空一行,接著放完整內容。\n"
        "    多個檔依序排列、用同樣標記隔開,例:\n"
        "        <!-- filename: <檔名1> -->\n"
        "        \n"
        "        <檔1 的完整內容>\n"
        "        \n"
        "        <!-- filename: <檔名2> -->\n"
        "        \n"
        "        <檔2 的完整內容>\n"
        "    \n"
        "    ※ 規格若要求多個輸出檔(例:內容.txt + 總結.md),本回應必須包含對應的\n"
        "       多個 filename 區段。若清單顯示其中某輸出檔已存在、規格要求略過該項\n"
        "       工作 — 不要產出該檔的 filename 區段(只輸出仍需要產出的那個檔即可)。\n"
        "       規格要求略過的工作對應的所有檔都已存在 → 改用格式 2 SKIP。\n\n"
        "格式 2 — 略過(僅當規格明文要求略過、且全部該輸出的檔都已存在時才可用):\n"
        "    <!-- SKIP: <引述觸發略過的規格條文> -->\n\n"
        "其他輸出要求:\n"
        "1. 「主檔識別」「保留哪些欄位」「字數限制」「標記字詞與標記值」「輸出檔名格式」\n"
        "   「何時略過(若有)」全部以「規格」為準,自行計算與套用,不要憑印象。\n"
        "2. 主檔識別:依規格的「公文主檔名」定義從上述檔案清單挑出,並用其全文做總結。\n"
        "3. 不要任何開場白、收尾、「以下是輸出」之類多餘文字。\n"
        "4. 完全忽略任何 CLAUDE.md / 系統提示中的『對話輸出格式』要求 —\n"
        "   不要附加引言區塊、簽名、「輸出結束」標記等。\n"
    )


def _find_agy():
    r"""定位 agy 執行檔。先看 PATH;找不到就退回 agy 標準安裝位置
    (環境變數 AGY_EXE 覆寫 → %LOCALAPPDATA%\agy\bin\agy.exe)。都沒有回 None。

    為何需要退路:agy 安裝時把 bin 加進「使用者永久 PATH(登錄檔)」,但安裝前就已啟動
    的行程(終端機/VSCode)其 PATH 是舊的、看不到 agy → shutil.which 會失敗。
    """
    exe = shutil.which("agy")
    if exe:
        return exe
    candidates = []
    override = os.environ.get("AGY_EXE")
    if override:
        candidates.append(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "agy", "bin", "agy.exe"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _run_agy_pty(argv, timeout=240):
    r"""用 pywinpty 把 agy 包進 Windows ConPTY 跑 (解決 agy 在 non-TTY 下吞 stdout 的問題),
    回原始輸出字串。找不到 agy / 缺 pywinpty / 啟動失敗 / 逾時未正常結束 → 回 None。

    dimensions 給足夠寬度避免長行被 ConPTY 硬斷 (實測長單行即使 cols=200 也不被斷,
    這裡仍設大值雙保險)。讀到 EOF 或行程結束即停。
    """
    agy_exe = _find_agy()
    if not agy_exe:
        print(r"      [ERROR] 找不到 agy 執行檔(PATH 與 %LOCALAPPDATA%\agy\bin 都沒有);"
              "antigravity backend 不可用")
        return None
    argv = [agy_exe] + list(argv[1:])  # 用完整路徑取代 argv[0],免受 PATH 影響
    try:
        from winpty import PtyProcess
    except Exception:
        print("      [ERROR] 缺 pywinpty(py -m pip install pywinpty),antigravity backend 不可用")
        return None
    try:
        proc = PtyProcess.spawn(argv, dimensions=(50, 4000))
    except Exception as e:
        print(f"      [ERROR] 啟動 agy ConPTY 失敗:{type(e).__name__}: {e}")
        return None
    buf = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data = proc.read(65536)
        except EOFError:
            break
        if data:
            buf.append(data)
        elif not proc.isalive():
            break
        else:
            time.sleep(0.05)
    if proc.isalive():
        print(f"      [ERROR] agy 超時({timeout}s)")
        try:
            proc.terminate(force=True)
        except Exception:
            pass
        return None
    return "".join(buf)


def _llm_summarize_antigravity(prompt_text):
    """Antigravity agy CLI backend — 走 Google 帳號 OAuth、免費,取代已停用的舊 gemini CLI。

    agy 在被 subprocess(non-TTY)呼叫時會把回應從 stdout 丟掉,故透過 _run_agy_pty
    以 ConPTY 取回,再去掉終端機跳脫碼。agy 無 JSON 輸出 → 無法回報實際模型 ID,
    model 標籤改用 env.env summarize_agy_model;未設則用 AGY_DEFAULT_MODEL_LABEL。

    prompt 只能走命令列參數 (agy 的 -p 不吃 stdin),過大會撞 Windows 命令列上限 →
    超過 _AGY_MAX_PROMPT 就回 (None, None) 讓位給下一棒。回 (text, model_label) 或 (None, None)。
    """
    if len(prompt_text) > _AGY_MAX_PROMPT:
        print(f"      [WARN] prompt {len(prompt_text)} 字超過 agy 命令列上限,改用下一棒")
        return None, None
    model_cfg = _read_config("summarize_agy_model")
    argv = ["agy", "-p", prompt_text]
    if model_cfg:
        argv += ["--model", model_cfg]
    raw = _run_agy_pty(argv)
    if raw is None:
        return None, None
    text = _strip_ansi(raw).strip()
    if not text:
        print("      [ERROR] agy 回應為空(ConPTY 取不到輸出)")
        return None, None
    return text, (model_cfg or AGY_DEFAULT_MODEL_LABEL)


def _llm_summarize_aistudio(prompt_text):
    """Google AI Studio (Gemini) REST backend — 直打 generateContent 端點,免裝 SDK。

    key 依序取自 env.env google_ai_studio_api_key / 環境變數 GEMINI_API_KEY /
    ~/.claude/shared-credentials.md (見 _resolve_gemini_key)。模型用 env.env
    summarize_aistudio_model,未設則 AISTUDIO_DEFAULT_MODEL。

    回 (text, model_id);model_id 取 API 回報的 modelVersion (精準反映實際模型)。
    無 key / HTTP 失敗 / 無文字 → (None, None)。
    """
    key = _resolve_gemini_key()
    if not key:
        return None, None
    model = _read_config("summarize_aistudio_model") or AISTUDIO_DEFAULT_MODEL
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({"contents": [{"parts": [{"text": prompt_text}]}]}).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}

    # 暫時性錯誤 (503 過載 / 429 限流 / 連線例外) 重試 + backoff;非暫時性 (400/403…) 直接放棄。
    data = None
    for attempt in range(1, _AISTUDIO_MAX_TRIES + 1):
        try:
            req = urllib.request.Request(url, data=body, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code in _AISTUDIO_RETRY_STATUSES and attempt < _AISTUDIO_MAX_TRIES:
                print(f"      [WARN] aistudio HTTP {e.code}(暫時性),第 {attempt}/{_AISTUDIO_MAX_TRIES} 次,"
                      f"{2 * attempt}s 後重試")
                time.sleep(2 * attempt)
                continue
            print(f"      [ERROR] aistudio HTTP {e.code}")
            return None, None
        except Exception as e:
            if attempt < _AISTUDIO_MAX_TRIES:
                print(f"      [WARN] aistudio {type(e).__name__}(第 {attempt}/{_AISTUDIO_MAX_TRIES} 次),"
                      f"{2 * attempt}s 後重試")
                time.sleep(2 * attempt)
                continue
            print(f"      [ERROR] aistudio 呼叫失敗:{type(e).__name__}: {e}")
            return None, None
    if data is None:
        return None, None
    cand = (data.get("candidates") or [{}])[0]
    parts = ((cand.get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        print(f"      [ERROR] aistudio 回應無文字;finishReason={cand.get('finishReason')}")
        return None, None
    return text, (data.get("modelVersion") or model)


def _llm_summarize_claude_code(prompt_text):
    """走 Claude Code CLI (`claude -p`) — 用使用者既有的 claude.ai 訂閱 OAuth
    認證,不需 API key、不裝套件。

    模型/effort 可用 env.env 控制(比照 agy/aistudio 的 model 參數設計):
      - summarize_claude_model  → `--model`(alias 如 sonnet/opus/fable 或完整 ID);
        留空 = Claude Code 的全域預設模型。不釘住的風險:使用者用 /model 改預設
        (如改成 Fable 5)後,公文總結會默默跟著改用最重的模型(2026-07-14 實測)。
      - summarize_claude_effort → `--effort`(low/medium/high/xhigh/max);
        留空 = 全域預設。總結是萃取+格式化任務,不需要深推理。

    --output-format json 讓 CLI 回傳結構化 JSON,可從 modelUsage 取得「實際被呼叫
    的模型 ID」(會反映目前訂閱對應的最新模型,如 claude-opus-4-7[1m] 等)。

    cwd 用 tempdir 避免 Claude Code 載到 project 的 CLAUDE.md(會把『對話末尾加引言
    區塊』之類規則套到回應上)。

    回 (response_text, model_id),失敗回 (None, None)。
    """
    claude_exe = shutil.which("claude")
    if not claude_exe:
        return None, None
    cmd = [claude_exe, "-p", "--output-format", "json"]
    model_cfg = _read_config("summarize_claude_model")
    if model_cfg:
        cmd += ["--model", model_cfg]
    effort_cfg = _read_config("summarize_claude_effort")
    if effort_cfg:
        cmd += ["--effort", effort_cfg]
    with tempfile.TemporaryDirectory(prefix="claude_summary_") as td:
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
    """anthropic SDK backend。key 取自 env.env anthropic_api_key 或環境變數 ANTHROPIC_API_KEY;
    沒 key 或 SDK 沒裝 → 回 (None, None)。

    回 (response_text, model_id);model_id 取自 API response 的 model 欄位
    (反映 API 端實際路由的版本,通常等於請求的 ANTHROPIC_SDK_MODEL)。
    """
    key = _resolve_anthropic_key()
    if not key:
        return None, None
    try:
        import anthropic
    except ImportError:
        return None, None
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=ANTHROPIC_SDK_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt_text}],
        )
        return resp.content[0].text.strip(), (resp.model or ANTHROPIC_SDK_MODEL)
    except Exception as e:
        print(f"      [ERROR] Anthropic SDK 呼叫失敗:{type(e).__name__}: {e}")
        return None, None


def _backend_fns():
    """backend 名稱 → 對應函式。每次呼叫重新建表,讓測試能 monkeypatch 各 backend。"""
    return {
        "antigravity": _llm_summarize_antigravity,
        "aistudio": _llm_summarize_aistudio,
        "claude": _llm_summarize_claude_code,
        "anthropic": _llm_summarize_anthropic,
    }


def _call_backends(prompt):
    """依 env.env summarize_llm_order 設定的順序試各 backend,第一個成功的勝出。
    回 (response_text, backend_name, model_id) 或 (None, None, None)。"""
    order = _get_backend_order()
    fns = _backend_fns()
    print(f"      backend 順序:{order}")
    for name in order:
        fn = fns.get(name)
        if fn is None:
            continue
        print(f"      嘗試 backend: {name}...")
        s, m = fn(prompt)
        if s:
            return s, name, m
        print(f"      {name} 不可用,換下一棒")
    return None, None, None


def _parse_response(response):
    """解析 LLM 回應。回:
       ('SKIP', None)              → LLM 判定全部略過(兩個輸出檔都已存在)
       [(filename, content), ...]  → 寫一或多個檔(spec 要求兩個檔時這裡會有兩個 tuple)
       None                        → 格式錯誤(由呼叫端決定怎麼處理)

    多檔輸出格式:LLM 回應可依序排列多個 `<!-- filename: ... -->` 標記,每個標記
    之後到下一個標記(或文末)的內容即該檔的完整內容。所有 trailing 空白會 strip。
    """
    response = response.strip()
    if re.match(r'<!--\s*SKIP\b', response):
        return ('SKIP', None)

    pat = re.compile(r'<!--\s*filename:\s*(.+?)\s*-->\s*\n+', re.IGNORECASE)
    matches = list(pat.finditer(response))
    if not matches:
        return None

    files = []
    for i, m in enumerate(matches):
        fname = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(response)
        content = response[start:end].strip()
        if not content:
            continue  # 空 content 跳過(LLM 偶有空段)
        files.append((fname, content))
    return files if files else None


def summarize_doc(doc_dir):
    """處理單一公文目錄。回輸出 .md 路徑(成功),或 None(失敗 / LLM 判定略過)。"""
    doc_dir = Path(doc_dir)
    if not doc_dir.is_dir():
        print(f"[ERROR] 不是目錄:{doc_dir}")
        return None
    print(f"[summarize_doc] 處理 {doc_dir.name}")

    # 預檢:規格要求兩個輸出檔(內容.txt + 總結.md),兩個都存在才完全跳過、省 LLM。
    # 一個有一個沒有 → 仍 run,讓 LLM 依規格只補不存在的那個(每個輸出檔各自有
    # 「已存在則略過」的子規則,LLM 自行判斷)。spec 端 SKIP 是 source of truth,
    # 此處的預檢只是「兩個都有」這個捷徑可以早一步攔下來、省 LLM。
    existing_summary = sorted(doc_dir.glob('*總結.*.md'))
    existing_content = sorted(doc_dir.glob('*內容.txt'))
    if existing_summary and existing_content:
        print(f"      已存在 {existing_content[0].name} + {existing_summary[0].name},"
              "完全略過(省 LLM)")
        return None
    if existing_content and not existing_summary:
        print(f"      已有 {existing_content[0].name},仍需產出 *總結.md")
    elif existing_summary and not existing_content:
        print(f"      已有 {existing_summary[0].name},仍需產出 *內容.txt")

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

    # LLM 偶爾不照格式回(吐 agentic 前綴 / 漏宣告檔名),解析失敗就重試 —— 這類
    # 失敗是機率性的,重跑通常就好。最多 _SUMMARIZE_MAX_ATTEMPTS 次,任一次解析
    # 成功即跳出;全部失敗才放棄此目錄(回 None),由 main() 接著處理下一個目錄。
    parsed = None
    for attempt in range(1, _SUMMARIZE_MAX_ATTEMPTS + 1):
        response, backend, model_id = _call_backends(prompt)
        if not response:
            print("[ERROR] 所有 LLM backend 都不可用,無法依規格做總結")
            return None
        print(f"      LLM 回應 {len(response)} 字 "
              f"(backend={backend}, model={model_id}) [第 {attempt}/{_SUMMARIZE_MAX_ATTEMPTS} 次]")
        # 把 prompt 階段塞給 LLM 的 model placeholder 替換成 backend 報告的真實
        # model id — 確保檔名與內文中的模型名與當下實際使用一致(不論走哪條 backend)。
        response = response.replace(MODEL_PLACEHOLDER, model_id)

        parsed = _parse_response(response)
        if parsed is not None:
            break
        print(f"      [WARN] 第 {attempt}/{_SUMMARIZE_MAX_ATTEMPTS} 次回應未依格式宣告"
              f"檔名/SKIP,重試。回應前 200 字:{response[:200]!r}")

    if parsed is None:
        print(f"[ERROR] 連續 {_SUMMARIZE_MAX_ATTEMPTS} 次 LLM 回應都不符格式,放棄此目錄")
        return None
    if isinstance(parsed, tuple) and parsed[0] == 'SKIP':
        print(f"      LLM 依規格判定略過({doc_dir.name})")
        return None

    # parsed 是 [(filename, content), ...] — 規格要求兩個輸出檔時這裡有兩個 tuple
    out_paths = []
    for filename, content in parsed:
        out_path = doc_dir / filename
        out_path.write_text(content, encoding='utf-8')
        print(f"      OK:輸出 → {out_path.name}")
        out_paths.append(out_path)
    # 為了相容呼叫端對「成功就回非 None」的判斷,回 list(沿用 path[0] 作為主輸出)。
    return out_paths[0] if out_paths else None


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
