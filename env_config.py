# -*- coding: utf-8 -*-
"""
env_config.py
`env.env` 的讀寫（fork 新檔，給「設定」頁與 `doctor.py` 用）。

⚠️ **最重要的一條規則:密鑰是個人的，這支程式只幫人填，不幫人保管、也不幫人看。**
承辦人 2026-08-12 的原話:「KEY 不是應該個人填個人的」。這套工具會交給下一個人，
而密鑰跟著人不跟著工具。所以:

  · `state()` **永遠不回傳密鑰的值**，只回「有沒有填」。看得到就會被截圖、被貼進
    交接檔 —— 而這個 repo 是 public。
  · 寫入時**不印值**（連長度都不印）。
  · 最敏感的是 `pin`（自然人憑證）:**連續輸錯會鎖卡，要跑戶政事務所解卡**。
    所以介面改它要多問一次，而程式在跑的時候只填一次、絕不重試（見坑 #20）。

讀取規則跟既有那兩支 `_read_config` 完全一樣（`key=value` 一行一筆、`#` 開頭整行
為註解、值 strip 過）—— **不另立一套**，不然設定頁顯示的跟程式讀到的會不一樣。
寫入時**逐行保留原檔**（註解、空行、順序、不認識的鍵都不動），只換掉那一行的值:
那些註解是說明書，`env_example.env` 的內容就靠它們傳給接手的人。
"""

import os
import shutil

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(_BASE_DIR, "env.env")
EXAMPLE_FILE = os.path.join(_BASE_DIR, "env_example.env")

# 個人／共用的分界。**個人 = 跟著人走，交接時要留空**;共用 = 跟著這個學校、
# 這個職務走，換人接手照用。
PERSONAL, SHARED = "個人", "共用"

# 這張表就是設定頁的內容。`祕密` 的值一律不出畫面。
# `哪裡拿` 是給接手者看的 —— 沒有它，「google_ai_studio_api_key」對新人是天書。
FIELDS = [
    {"鍵": "pin", "區": PERSONAL, "祕密": True, "標題": "自然人憑證 PIN",
     "說明": "登入 edoc 要用。⚠️ 連續輸錯會鎖卡，要跑戶政事務所解卡 —— "
             "改之前請確定。程式只會填一次，絕不重試。",
     "哪裡拿": "你自己的憑證 PIN（不是校網密碼）"},
    {"鍵": "sssh_account", "區": PERSONAL, "祕密": True, "標題": "校網帳號",
     "說明": "貼校網公告時登入用。", "哪裡拿": "松高校網的後台帳號"},
    {"鍵": "sssh_password", "區": PERSONAL, "祕密": True, "標題": "校網密碼",
     "說明": "同上。", "哪裡拿": "松高校網的後台密碼"},
    # ⚠️ 這兩格**多數人不用填**。2026-08-14 同事（老師）看到就問「API 是什麼」——
    # 那是我把它們擺太顯眼、又用了對非工程同仁沒有意義的詞。
    # 摘要預設走本機 claude 的登入，一支 key 都不用申請。
    {"鍵": "google_ai_studio_api_key", "區": PERSONAL, "祕密": True,
     "進階": True, "標題": "Google AI 的金鑰",
     "說明": "**多數人不用填，留空就好。** 這是向 Google 的 AI 服務借用額度的"
             "一組密碼。只有把上面「摘要要叫哪些 AI」改成用 aistudio 時才需要。",
     "哪裡拿": "要用才申請:aistudio.google.com/apikey（有免費額度）"},
    {"鍵": "anthropic_api_key", "區": PERSONAL, "祕密": True,
     "進階": True, "標題": "Anthropic 的金鑰",
     "說明": "**多數人不用填，留空就好。** 同上，只有把「摘要要叫哪些 AI」改成用 "
             "anthropic 時才需要（那個要付費）。",
     "哪裡拿": "要用才申請:console.anthropic.com"},

    # 這一格是**這台機器**的路徑，不是學校設定 —— 但它不是密鑰，畫面上要看得到
    # （看不到就沒辦法確認自己改對地方了）。放共用區，說明裡講清楚。
    {"鍵": "review_sheet_path", "區": SHARED, "祕密": False,
     "標題": "審核表放哪裡",
     "說明": "留空 = 桌面的「公告彙整.xlsx」。想把它跟公文放在同一顆磁碟就填"
             "完整路徑。**這是這台電腦的路徑**，換一台要重填。"
             "⚠️ 改完之後舊的那份不會自動搬過去，要自己複製。",
     "哪裡拿": "例:D:\\公文\\公告彙整.xlsx"},
    {"鍵": "sssh_publisher", "區": SHARED, "祕密": False, "標題": "發布者",
     "說明": "校網公告表單「發布者」欄要填的名字。",
     "哪裡拿": "例:資媒組管理員"},
    {"鍵": "sssh_publish_unit", "區": SHARED, "祕密": False, "標題": "發布單位",
     "說明": "校網公告表單「發布單位」下拉要選的單位。**必須跟站上的選項文字"
             "一模一樣**，對不上程式會停下不發（不會亂貼）。",
     "哪裡拿": "例:資訊媒體組／圖書館／系管師群組"},
    {"鍵": "summarize_llm_order", "區": SHARED, "祕密": False,
     "標題": "摘要要叫哪些 AI（依序）",
     "說明": "逗號分隔，依序嘗試，第一個「可用且成功」的就採用。"
             "可用:claude／anthropic／aistudio／antigravity。",
     "哪裡拿": "建議 claude,anthropic —— claude 那一棒走本機 claude CLI 的登入，"
               "**不需要任何 key**"},
    {"鍵": "summarize_claude_model", "區": SHARED, "祕密": False,
     "標題": "claude 用哪個模型", "說明": "留空 = 用訂閱的預設。例:sonnet",
     "哪裡拿": ""},
    {"鍵": "summarize_claude_effort", "區": SHARED, "祕密": False,
     "標題": "claude 的思考程度",
     "說明": "留空 = 全域預設。總結是萃取+格式化，不需要深推理。例:medium",
     "哪裡拿": ""},
    {"鍵": "summarize_aistudio_model", "區": SHARED, "祕密": False,
     "標題": "aistudio 用哪個模型", "說明": "留空 = gemini-2.5-flash。",
     "哪裡拿": ""},
    {"鍵": "summarize_agy_model", "區": SHARED, "祕密": False,
     "標題": "antigravity 用哪個模型", "說明": "留空 = agy 預設。",
     "哪裡拿": ""},
]

BY_KEY = {f["鍵"]: f for f in FIELDS}

# 摘要那條路認得的 backend 名稱。從 summarize_doc 拿，**不要在這裡另抄一份** ——
# 抄了就會有「設定頁說可以、程式其實不認」這種分岔。
def known_backends():
    try:
        import summarize_doc as sd
        return list(sd.DEFAULT_LLM_ORDER)
    except Exception:
        return ["antigravity", "aistudio", "claude", "anthropic"]


# ── 讀 ─────────────────────────────────────────────────────────────────────

def _lines():
    """回原檔每一行（含換行）。檔案不存在回 []。"""
    if not os.path.isfile(ENV_FILE):
        return []
    with open(ENV_FILE, encoding="utf-8") as f:
        return f.readlines()


def read_values():
    """回 {鍵: 值}。解析規則與 `_read_config` 一致。

    ⚠️ 這支會回**密鑰的值**（寫檔時要比對有沒有變）。給畫面的一律走 `state()`。
    """
    out = {}
    for line in _lines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip()
    return out


def state():
    """設定頁要的東西。**密鑰只回「有沒有填」，不回值。**"""
    vals = read_values()
    fields = []
    for f in FIELDS:
        item = {k: f[k] for k in ("鍵", "區", "祕密", "標題", "說明", "哪裡拿")}
        # 進階 = 多數人不用填的。畫面上收進摺頁，免得嚇到人。
        item["進階"] = bool(f.get("進階"))
        if f["祕密"]:
            item["已填"] = bool((vals.get(f["鍵"]) or "").strip())
        else:
            item["值"] = vals.get(f["鍵"]) or ""
        fields.append(item)
    return {
        "檔案": ENV_FILE,
        "存在": os.path.isfile(ENV_FILE),
        "欄位": fields,
        "認得的棒次": known_backends(),
        # 不認識的鍵照樣留在檔案裡（寫入時不動它們），但要講出來 ——
        # 免得有人以為設定頁沒列到的東西就是沒有。
        "其他鍵": sorted(k for k in vals if k not in BY_KEY),
        "提醒": warnings(vals),
    }


def example_values():
    """`env_example.env` 裡的值。用來認出「還沒填、只是複製過來的範例值」。"""
    out = {}
    if not os.path.isfile(EXAMPLE_FILE):
        return out
    try:
        with open(EXAMPLE_FILE, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, _, v = s.partition("=")
                if v.strip():
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


# 只有這幾個「照抄範例值」才算沒填。其餘（摘要順序、模型設定）的範例值就是
# **出廠預設**，本來就該照用 —— 把它們一起報成「還沒填」是假警告，
# 而假警告會讓真警告一起被當成裝飾（坑 #13）。
MUST_BE_YOURS = ("pin", "sssh_account", "sssh_password",
                 "sssh_publisher", "sssh_publish_unit")


def placeholders(vals=None):
    """回「值跟範例檔一模一樣」的鍵 —— 那代表還沒填自己的。

    為什麼需要這一支（2026-08-12 實跑發現）:安裝流程會把 `env_example.env`
    複製成 `env.env`，而範例檔裡是**有值的**（`pin=000000`、
    `sssh_account=abcdefg12345`）。於是「有沒有填」這個檢查會說「✅ 已填」，
    接手的人以為好了，實際上要到收文插卡那一刻才發現 PIN 是錯的。
    這就是這個 fork 最常見的坑型:**有人回報成功，但沒有人回頭確認那是真的。**
    """
    vals = read_values() if vals is None else vals
    ex = example_values()
    return [k for k in MUST_BE_YOURS
            if k in ex and (vals.get(k) or "").strip() == ex[k]]


def warnings(vals=None):
    """回一串「值本身沒錯，但值得講一句」的提醒。**不阻止任何事。**"""
    vals = read_values() if vals is None else vals
    out = []
    ph = placeholders(vals)
    if ph:
        names = "、".join(BY_KEY[k]["標題"] for k in ph)
        out.append(f"這幾格還是 env_example.env 的**範例值**，不是你自己的:{names}"
                   f" —— 收文或貼校網會失敗（PIN 是範例值的話會拿錯的去登入）。"
                   f"請在這一頁填自己的。")
    order = [s.strip().lower() for s in (vals.get("summarize_llm_order") or "").split(",")]
    order = [s for s in order if s]
    if "antigravity" in order:
        out.append("摘要順序裡有 antigravity —— 那個帳號沒資格（永久性錯誤），"
                   "每份公文要白等 10～13 秒才換下一棒。建議拿掉。")
    unknown = [s for s in order if s not in known_backends()]
    if unknown:
        out.append(f"摘要順序裡有程式不認得的名字:{'、'.join(unknown)} —— "
                   f"那幾個會被略過。認得的是:{'／'.join(known_backends())}")
    if order and order[0] == "claude" and not vals.get("anthropic_api_key"):
        pass                                # 正常狀態，不用講
    if not (vals.get("sssh_publish_unit") or "").strip():
        out.append("發布單位是空的 —— 張貼那頁會直接擋下來不讓貼（那一欄對不上"
                   "校網選項，程式會停下不發）。")
    if not order:
        out.append("摘要順序是空的 —— 會走程式的預設順序。")
    return out


# ── 寫 ─────────────────────────────────────────────────────────────────────

class Rejected(Exception):
    """不合格的輸入。訊息是給人看的中文。"""


def _check(key, value):
    """寫進去之前的把關。**只擋「一定是錯的」**，不擋「看起來怪」。"""
    f = BY_KEY.get(key)
    if f is None:
        raise Rejected(f"設定頁不認得「{key}」這個鍵，沒有寫入。")
    v = (value or "").strip()
    if "\n" in v or "\r" in v:
        raise Rejected(f"「{f['標題']}」不能有換行。")
    if key == "pin" and v and not v.isdigit():
        # PIN 打錯的代價是鎖卡（要跑戶政事務所），所以這裡寧可嚴一點。
        raise Rejected("PIN 只能是數字。打錯會在下次收文時鎖卡，"
                       "所以這裡不接受其他字元。")
    if key == "summarize_llm_order" and v:
        names = [s.strip().lower() for s in v.split(",") if s.strip()]
        if not any(n in known_backends() for n in names):
            raise Rejected(f"摘要順序裡沒有一個程式認得的名字。"
                           f"認得的是:{'／'.join(known_backends())}")
    return v


def write(updates):
    """把 updates（{鍵: 值}）寫回 `env.env`。回實際改動的鍵名 list。

    **逐行保留原檔** —— 註解、空行、順序、不認識的鍵一律不動;只換掉那一行
    `key=` 後面的值。那些註解是給接手者看的說明書，不能被程式吃掉。
    檔案不存在時先從 `env_example.env` 複製一份（那份的註解最完整）。

    ⚠️ 回傳與 log **只提鍵名，不提值** —— 密鑰不進 log。
    """
    clean = {k: _check(k, v) for k, v in (updates or {}).items()}
    if not clean:
        return []

    if not os.path.isfile(ENV_FILE):
        if os.path.isfile(EXAMPLE_FILE):
            shutil.copy(EXAMPLE_FILE, ENV_FILE)
        else:
            with open(ENV_FILE, "w", encoding="utf-8") as f:
                f.write("# 由設定頁建立\n")

    lines = _lines()
    nl = "\r\n" if any(ln.endswith("\r\n") for ln in lines) else "\n"
    before = read_values()
    changed, seen = [], set()

    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k = s.partition("=")[0].strip()
        if k not in clean or k in seen:
            continue
        seen.add(k)
        if before.get(k, "") != clean[k]:
            lines[i] = f"{k}={clean[k]}{nl}"
            changed.append(k)

    # 原檔沒有那一行的鍵 → 補在最後。**帶著說明一起補**，下一個人才看得懂。
    missing = [k for k in clean if k not in seen]
    if missing:
        if lines and not lines[-1].endswith(("\n", "\r\n")):
            lines[-1] += nl
        lines.append(f"{nl}# ===== 由設定頁補上 ====={nl}")
        for k in missing:
            f = BY_KEY[k]
            lines.append(f"# {f['標題']}:{f['說明']}{nl}")
            lines.append(f"{k}={clean[k]}{nl}")
            if before.get(k, "") != clean[k]:
                changed.append(k)

    tmp = ENV_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)
    os.replace(tmp, ENV_FILE)           # 換檔是原子的 —— 寫壞不會留半個 env.env
    return changed
