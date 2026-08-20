# -*- coding: utf-8 -*-
"""
doctor.py
搬到新機器、或交接給下一個人之後，先跑這一支:它把「跑不起來的原因」一次講完。

    python doctor.py

⚠️ **一定要用要跑這套工具的那支 Python 跑它。** 這台機器上有兩個 Python
（交接檔坑 #4）:`py` 是 3.14.4（**沒有 openpyxl**）、`python` 是 C:\\Python314
（有）。用錯那支的症狀是「審核表整個寫不進去」而且訊息看起來很無辜。
所以 doctor 檢查的是**跑它的那一支**，而 `啟動.bat` 會用同一支跑 ui.py ——
兩邊一致，這個坑才不會再踩。

輸出只有三種記號:
    ✅ 好了       ⚠️ 可以先不管（會少某個功能，但主流程跑得動）
    ❌ 一定要處理（跟著下面那行「怎麼修」做）
結束碼:有 ❌ 就是 1，其餘 0。
"""

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

BAD = []                                    # 累積 ❌，決定結束碼


def ok(t):
    print(f"  ✅ {t}")


def warn(t, how=""):
    print(f"  ⚠️ {t}")
    if how:
        print(f"      → {how}")


def bad(t, how=""):
    BAD.append(t)
    print(f"  ❌ {t}")
    if how:
        print(f"      → {how}")


def head(t):
    print(f"\n【{t}】")


# ── 1. Python 本身 ─────────────────────────────────────────────────────────

def check_python():
    head("Python")
    print(f"  跑這支 doctor 的是:{sys.executable}")
    print(f"  版本:{sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        bad(f"版本太舊（{sys.version.split()[0]}）",
            "請安裝 Python 3.10 以上（本專案開發時用 3.14）。")
    else:
        ok("版本可以")
    if os.name != "nt":
        warn("這不是 Windows", "這套工具要用 HiCOS 讀卡機與 KdApp，只能在 Windows 跑。")


# ── 2. 套件 ────────────────────────────────────────────────────────────────

# (import 名, 裝的名字, 一定要嗎, 少了會怎樣)
PACKAGES = [
    ("openpyxl", "openpyxl", True,
     "審核表（公告彙整.xlsx）讀不到也寫不進去 —— 整個介面等於廢掉。"
     "⚠️ 這一個最容易漏，因為 README 舊版沒列它。"),
    ("selenium", "selenium", True, "所有會動 Chrome 的事都做不了（收文、陳核、存查、張貼）。"),
    ("yaml", "pyyaml", True,
     "讀不到 routing_flags.yaml —— 「他人業務不自動送陳核」那道保護會失效。"),
    ("win32gui", "pywin32", True,
     "下載公文時接不到 KdApp 的「匯出公文資料」對話框，收文會停在那裡。"),
    ("PIL", "pillow", False, "少了備援的像素版登入（main.py 2）。主流程不用。"),
    ("pyautogui", "pyautogui", False, "同上，備援登入用。"),
    ("winpty", "pywinpty", False,
     "少了 antigravity 那一棒的摘要。反正建議把它從 summarize_llm_order 拿掉。"),
]


def check_packages():
    head("Python 套件")
    missing = []
    for mod, pkg, need, why in PACKAGES:
        try:
            importlib.import_module(mod)
            ok(f"{pkg}")
        except Exception:
            missing.append(pkg)
            (bad if need else warn)(f"沒有 {pkg} —— {why}",
                                    f'裝法:"{sys.executable}" -m pip install {pkg}')
    if missing:
        print(f"\n      一次裝完:\"{sys.executable}\" -m pip install "
              f"{' '.join(missing)}")


# ── 3. Chrome ──────────────────────────────────────────────────────────────

def check_chrome():
    head("Google Chrome")
    hits = [p for p in (
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ) if p and os.path.isfile(p)]
    if hits:
        ok(f"找到 Chrome:{hits[0]}")
        print("      （ChromeDriver 不用自己裝，Selenium 會自己抓對應版本。）")
    else:
        bad("找不到 Chrome", "去 google.com/chrome 裝一個。這套工具全靠它操作 edoc 與校網。")


# ── 4. 讀卡機／HiCOS ───────────────────────────────────────────────────────

def check_card():
    head("自然人憑證（讀卡機 + HiCOS）")
    dll = [p for p in (
        r"C:\Windows\System32\HiCOS_PKCS11.dll",
        r"C:\Program Files\HiCOS\HiCOSPKCS11.dll",
        r"C:\Program Files (x86)\HiCOS\HiCOSPKCS11.dll",
    ) if os.path.isfile(p)]
    hicos = bool(dll) or os.path.isdir(r"C:\Program Files (x86)\HiTRUST") \
        or os.path.isdir(r"C:\Program Files\HiTRUST")
    if hicos:
        ok("看起來有裝 HiCOS 元件")
    else:
        warn("找不到 HiCOS 元件的痕跡",
             "收文要插卡登入，沒有 HiCOS 會登不進去。"
             "到內政部憑證管理中心下載「HiCOS 卡片管理工具」。"
             "（找不到不一定代表沒裝 —— 版本不同路徑會不一樣，插卡試一次最準。）")
    print("      ⚠️ 兩件跟卡有關、程式幫不了的事:")
    print("         · **螢幕不能鎖** —— 鎖屏會擋住 HiCOS 讀卡。")
    print("         · PIN 連續輸錯會鎖卡，要跑戶政事務所解卡。設定頁只讓你填一次，"
          "程式絕不重試。")


# ── 5. env.env ─────────────────────────────────────────────────────────────

def check_env():
    head("設定檔 env.env")
    try:
        import env_config as ec
    except Exception as e:
        bad(f"讀不到 env_config:{type(e).__name__}: {e}")
        return
    if not os.path.isfile(ec.ENV_FILE):
        bad("還沒有 env.env",
            f"跑 安裝.bat 會自動從 env_example.env 複製一份;"
            f"或手動複製後用介面的「設定」頁填。")
        return
    ok(f"有 env.env（{ec.ENV_FILE}）")
    vals = ec.read_values()
    # 只講「有沒有填」，**絕不印值** —— 這支的輸出常常被貼進交接檔或截圖，
    # 而這個 repo 是 public（同設定頁的規矩）。
    must = {"pin": "收文要插卡登入，沒有 PIN 登不進去",
            "sssh_account": "貼校網要登入",
            "sssh_password": "貼校網要登入",
            "sssh_publish_unit": "貼校網的「發布單位」，對不上校網選項會停下不發"}
    # 「有填」不等於「填了自己的」。安裝流程會把 env_example.env 複製過來，
    # 而那份是**有值的**（pin=000000）—— 只看有沒有填會回報 ✅，然後在收文
    # 插卡那一刻才爆。這正是這個 fork 最常見的坑型:有人回報成功，沒人回頭確認。
    ph = set(ec.placeholders(vals))
    for k, why in must.items():
        if k in ph:
            bad(f"{k} 還是 env_example.env 的範例值，不是你自己的 —— {why}",
                "開介面的「設定」頁填自己的（那一頁不會顯示你填的內容）。")
        elif (vals.get(k) or "").strip():
            ok(f"{k} 已填")
        else:
            bad(f"{k} 沒填 —— {why}", "開介面的「設定」頁填（那一頁不會顯示你填的內容）。")
    order = [s.strip().lower() for s in (vals.get("summarize_llm_order") or "").split(",")
             if s.strip()]
    if not order:
        warn("summarize_llm_order 是空的", "會走程式預設順序。建議填 claude,anthropic。")
    else:
        ok(f"摘要順序:{','.join(order)}")
        if order[0] == "claude":
            print("      （claude 那一棒走本機 claude CLI 的登入，不需要任何 API key。）")
        if not shutil.which("claude") and "claude" in order:
            # 2026-08-14:同事那台就是這個狀況。原本只說「跳過那一棒」，
            # 但沒講「那接下來要怎麼辦」—— 而那才是他真正卡住的地方。
            # ⚠️ Claude Code 沒有免費方案（要 Pro 以上），所以不能只叫人去裝。
            warn("找不到 claude 這個命令 —— 摘要那一段會跳過它",
                 "三條路,挑一條:\n"
                 "         ① 這台電腦沒有 Claude Code。它要付費方案（Pro 以上），\n"
                 "            有的話裝好並登入就能用。\n"
                 "         ② **不想花錢**:去 aistudio.google.com/apikey 申請一把\n"
                 "            免費金鑰，填進介面「設定」頁的「AI 服務的金鑰」，\n"
                 "            再把「摘要要叫哪些 AI」改成 aistudio。免費額度對\n"
                 "            公文摘要夠用。\n"
                 "         ③ 都不要:那就只有摘要產不出來，收文、陳核、存查、\n"
                 "            張貼都照常能用，摘要欄自己填。")
    for w in ec.warnings(vals):
        warn(w)


# ── 6. 摘要那一段真的叫得動嗎 ──────────────────────────────────────────────

CLAUDE_PROBE_TIMEOUT = 60                   # 秒;登入的話通常 5～15 秒就回
_PROBE_PROMPT = "回四個字:可以用了"


def claude_login_state():
    """真的叫一次 `claude -p`，回 (狀態, 訊息)。
    狀態:"ok" / "nologin"（命令在但沒登入）/ "missing"（沒這個命令）/ "fail"。

    2026-08-18 加的。原本這裡只檢查「claude 這個命令**在不在**」，於是
    「命令在、但沒登入」的機器一路綠燈，直到收文跑到摘要那一段才全滅 ——
    那天早上真的踩到:公文其實下載好了，四個 backend 全滅，畫面顯示「摘要 0 筆」，
    看起來像沒收到文。這正是這個 fork 最常見的坑型（回報成功、其實沒做到）。

    ⚠️ 沒登入時 CLI 是**結束碼 1、stderr 空的**，真正的原因寫在 stdout 的 JSON:
    `is_error=true`、`result="Not logged in · Please run /login"`。只讀結束碼與
    stderr 會得到一句什麼都沒講的錯誤訊息（8/18 的 run.log 就長那樣），
    所以這裡兩邊都讀，以 stdout 的 JSON 為主。

    cwd 用暫存目錄:比照 summarize_doc，避免 claude 載到本專案的 CLAUDE.md
    （那裡有「回應末尾加引言區塊」之類規則，會混進總結內容）。
    """
    exe = shutil.which("claude")
    if not exe:
        return "missing", ""
    try:
        with tempfile.TemporaryDirectory(prefix="doctor_claude_") as td:
            r = subprocess.run(
                [exe, "-p", "--output-format", "json"],
                input=_PROBE_PROMPT, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=CLAUDE_PROBE_TIMEOUT, cwd=td)
    except subprocess.TimeoutExpired:
        return "fail", f"叫了 {CLAUDE_PROBE_TIMEOUT} 秒還沒回應"
    except Exception as e:
        return "fail", f"{type(e).__name__}: {e}"
    try:
        data = json.loads(r.stdout or "")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    msg = str(data.get("result") or "").strip() or (r.stderr or "").strip()
    low = msg.lower()
    if "not logged in" in low or "/login" in low:
        return "nologin", msg[:200]
    if r.returncode != 0 or data.get("is_error"):
        return "fail", msg[:200] or f"結束碼 {r.returncode}，而且沒講原因"
    return "ok", msg[:80]


def check_llm():
    head("摘要那一段（這一項會真的叫一次 AI，很短、幾乎不花錢）")
    try:
        import summarize_doc as sd
        order = sd._get_backend_order()
    except Exception as e:
        warn(f"讀不到摘要順序:{type(e).__name__}: {e}")
        return
    if "claude" not in order:
        print("      設定裡的摘要順序沒有 claude 那一棒，跳過這項檢查。")
        return
    state, msg = claude_login_state()
    if state == "missing":
        print("      這台沒有 claude 這個命令（上面 env.env 那節已經講怎麼辦）。")
    elif state == "ok":
        ok("claude 叫得動，而且是登入狀態 —— 摘要產得出來")
    elif state == "nologin":
        bad("claude 這個命令在，但**沒登入** —— 收文時摘要會全部失敗",
            "開一個終端機打 claude 進去，再打 /login 登入，登完可以關掉;"
            "然後跑 python prep_batch.py --summary-only 補摘要"
            "（公文不用重收，已經下載的都還在）。")
    else:
        bad(f"claude 叫不動:{msg}",
            "先自己在終端機打一次 claude 看它說什麼。"
            "不想處理就改用 aistudio 那一棒（見上面 env.env 那節）。")


# ── 7. 規格檔 ──────────────────────────────────────────────────────────────

def check_specs():
    head("判斷用的規格檔")
    for n in ("summarize_doc.md", "announce_doc.md", "routing_flags.yaml"):
        p = os.path.join(_BASE_DIR, n)
        (ok if os.path.isfile(p) else bad)(
            f"{n}{'' if os.path.isfile(p) else ' 不見了'}")
    try:
        import spec_forms as sf
        live = sf.routing_state().get("生效中") or {}
        if live.get("錯誤"):
            bad(f"routing_flags.yaml 讀不進去:{live['錯誤']}",
                "用介面「設定」頁的表單改（它存檔前會自己驗過），不要手改 YAML。")
        else:
            n = live.get("他人字別", 0) + live.get("關鍵字", 0)
            if n:
                ok(f"他人業務判斷讀到 {live.get('本人字別')}／"
                   f"{live.get('他人字別')}／{live.get('關鍵字')} 筆")
            else:
                bad("他人業務判斷讀到 0 筆 —— 那道保護等於沒有",
                    "YAML 可能改壞了（讀不到就當成空的，畫面不會說）。"
                    "用「設定」頁的表單修。")
        t = sf.table_state()
        if t.get("有這張表"):
            ok(f"存查分類對應表 {len(t['列'])} 列")
        else:
            bad("在 summarize_doc.md 裡找不到對應表",
                "那張表決定存查分類與同步分類，少了它每份公文都要人工判。")
    except Exception as e:
        warn(f"規格檔檢查跳過:{type(e).__name__}: {e}")


# ── 8. 審核表與工作區 ──────────────────────────────────────────────────────

def check_path_ascii():
    """這個工具**裝在哪裡** —— 路徑有中文會讓公文下載到錯的地方。

    2026-08-14 同事那台踩到:KdApp 的「匯出公文資料」對話框是用模擬實體鍵盤填
    路徑的（`pending_doc_handler._send_text_vk` → `VkKeyScanW`），而那支只認得
    ASCII，中文字**直接被跳過**。於是
        D:\\D_資訊系統\\自動辦文工具\\document_download
      → D:\\D_\\document_download        公文真的掉到這裡
    而且對話框照樣關掉、程式照樣往下跑 —— 又是「看起來成功」那一類。

    `prep_batch` 已經補了剪貼簿那條路，但這裡照樣要講:剪貼簿也可能被擋
    （遠端桌面、剪貼簿軟體、權限），而純英數路徑是零風險的。
    """
    head("這個工具裝在哪裡")
    print(f"  {_BASE_DIR}")
    bad_chars = [c for c in _BASE_DIR if ord(c) > 127]
    if not bad_chars:
        ok("路徑全部是英文數字 —— 最安全的狀態")
        return
    warn(f"路徑裡有非英文字元（{''.join(sorted(set(bad_chars))[:6])}…）",
         "公文下載那一步是用模擬鍵盤把路徑打進 KdApp 的對話框，而那個方式"
         "**打不出中文**。程式已經改成用剪貼簿貼上，所以多半會正常；"
         "但剪貼簿有可能被擋（遠端桌面、剪貼簿工具、權限），一被擋就會把公文"
         "下載到**別的資料夾**。\n"
         "         最保險:把整個資料夾搬到純英數路徑（例如 D:\\sssh-tool），"
         "搬完再點一次「安裝.bat」重建桌面捷徑。")
    print("      （怎麼確認有沒有中招:收完文之後看「摘要」頁有沒有出現新公文。"
          "沒出現就去找 D:\\ 底下有沒有多一個奇怪的 document_download。）")


def check_sheet():
    head("審核表與工作區")
    try:
        import review_sheet as rs
    except Exception as e:
        bad(f"讀不到 review_sheet:{type(e).__name__}: {e}")
        return
    p = rs.sheet_path()
    print(f"  審核表位置:{p}")
    if not os.path.isfile(p):
        warn("還沒有審核表", "第一次按「收新公文」或開介面時會自己建立。")
    else:
        ok("審核表在")
        try:
            with open(p, "r+b"):
                pass
            ok("寫得進去")
        except PermissionError:
            bad("寫不進去 —— Excel 正開著那個檔",
                "把 Excel 關掉。開著的話勾選、擬辦、公告文案全部存不進去。")
        except OSError as e:
            bad(f"開不起來:{e}")
    for d in rs.SCAN_DIRS:
        (ok if os.path.isdir(d) else bad)(
            f"工作區 {os.path.basename(d)}{'' if os.path.isdir(d) else ' 不見了'}")


# ── 9. 那個會咬人的細節 ────────────────────────────────────────────────────

def check_two_pythons():
    head("這台機器上有幾個 Python（坑 #4）")
    seen = []
    for name in ("python", "py"):
        exe = shutil.which(name)
        if exe:
            seen.append(f"{name} → {exe}")
    for s in seen:
        print(f"  {s}")
    try:
        importlib.import_module("openpyxl")
        ok("跑這支 doctor 的 Python 有 openpyxl —— 就用這一支跑 ui.py")
        print(f"      啟動指令:\"{sys.executable}\" ui.py（或直接用 啟動.bat）")
    except Exception:
        bad("跑這支 doctor 的 Python 沒有 openpyxl",
            "換一支有的來跑，或把 openpyxl 裝到這一支。"
            "⚠️ 別忘了 ui.py 會用「啟動它的那支 Python」去跑所有子程式，"
            "所以兩邊一定要是同一支。")


def main():
    print("=" * 62)
    print(" 自動辦文工具 — 環境檢查")
    print(" 這支只讀不寫,不會動任何公文、不會連 edoc、不會貼校網。")
    print(" （唯一的例外:檢查摘要那一段時會叫一次 AI 說一句話，確認它真的登入了。）")
    print("=" * 62)
    for fn in (check_python, check_two_pythons, check_packages, check_chrome,
               check_card, check_path_ascii, check_env, check_llm, check_specs,
               check_sheet):
        try:
            fn()
        except Exception as e:                   # 一項壞掉不該讓整支停下
            print(f"  ⚠️ 這一項檢查自己出錯了:{type(e).__name__}: {e}")
    print("\n" + "=" * 62)
    if BAD:
        print(f" 有 {len(BAD)} 件一定要處理:")
        for t in BAD:
            print(f"   ❌ {t}")
        print(" 照上面每一項下面那行「→」做完，再跑一次這支。")
    else:
        print(" 都好了 ✅　用 啟動.bat 開介面（或 "
              f"\"{sys.executable}\" ui.py）。")
    print("=" * 62)
    raise SystemExit(1 if BAD else 0)


if __name__ == "__main__":
    main()
