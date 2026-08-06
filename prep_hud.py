# -*- coding: utf-8 -*-
"""
prep_hud.py
跑文進度小浮窗（右下角、永遠在最上層）。

    python prep_hud.py [http://127.0.0.1:8760] [起始行號]
    python prep_hud.py --job=send --label="送陳核中…請不要動滑鼠"
    python prep_hud.py --demo      拿假訊息看長相（不連 ui.py，調版面用）

`--job` 決定看哪一支工作的進度:`prep`（收新公文，預設）或 `send`（送陳核）。
兩者都是 Chrome 一到前景就把 ui.py 的網頁整片蓋掉，所以都需要這個浮窗。

為什麼要這支:`main.py` 登入時會把 Chrome 最大化，整片蓋掉 ui.py 的網頁，
所以那個 #preplog 進度面板雖然一直在收訊息，承辦人卻看不到（2026-08-03 回報:
「其實我看不到終端機的訊息」）。這支就是把同一份訊息擺到蓋不住的地方。

刻意的設計 —— 都是為了不干擾自動化:

* **只讀不寫**。資料來源是 ui.py 既有的 /api/prep/status，本程式不碰 edoc、
  不碰公文、不下指令，關掉它不影響正在跑的工作。
* **視窗小、擺右下角**。跑文中間有一段要用 Win32 接管 KdApp 的「匯出公文資料」
  對話框（模擬鍵盤填路徑）。那對話框開在畫面中央，浮窗躲在角落才不會擋到。
* **絕不搶焦點**。建立之後不再呼叫 lift()／focus_force()。置頂只影響前後
  順序、不搶輸入焦點，SetForegroundWindow 那段才不會被打斷。
* 連不上或工作結束就自己關掉，不留殘影。

標準庫 tkinter 寫成，不裝任何套件（與 ui.py 同一個原則:之後打包給其他處室，
對方不需要安裝環境）。
"""

import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8760"
POLL_MS = 1000          # 每秒抓一次
KEEP_LINES = 300        # 記憶體裡最多留幾行
SHOW_LINES = 8          # 視窗裡看得到幾行

# 成功結束後幾毫秒自動關。**失敗一律不自動關**,訊息要留著給人看。
CLOSE_AFTER_MS = 20000
# 連不上介面幾次(秒)開始警告 / 幾次才真的關掉。
#
# 2026-08-06 承辦人回報:「小視窗不見了 我以為結束了我就碰了滑鼠」——
# 那次送陳核的第 3 筆就是這樣失敗的。舊版連不上 5 秒就自我關閉，而
# **浮窗消失被當成「跑完了」的信號**。工作還在跑的時候它絕對不能消失,
# 也不能讓人靠「有沒有視窗」去猜能不能動滑鼠 —— 要明講。
LOST_WARN = 3
LOST_GIVEUP = 60

BG = "#22272e"
FG = "#9fe1cb"
DIM = "#7d8590"
OK = "#7ee787"
BAD = "#ff7b72"


def _fetch(base, since, job="prep"):
    """抓 ui.py 的進度。回 dict 或 None（連不上）。"""
    url = f"{base}/api/{job}/status?since={since}"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


# 展示模式用的假訊息 —— 承辦人看不到腦中的畫面,要調版面就得先看到實物。
DEMO_LINES = [
    "▶ 執行:edoc 備料 — 登入 + 下載 + LLM 摘要（不擬辦/不陳會）",
    "[prep] sidebar 承辦中 = 4",
    "[prep] 承辦中清單共 4 筆",
    "[prep] (1/4) 處理 MWAA1156007629",
    "      OK:下載完成 — MWAA1156007629.zip",
    "[pending_doc_handler] 解壓縮...",
    "      嘗試 backend: claude...",
    "      LLM 回應 341 字 (backend=claude, model=claude-sonnet-4-6)",
    "[prep] (2/4) 處理 MWAA1156007635",
    "      OK:下載完成 — MWAA1156007635.zip",
]

# 送陳核的假訊息（--job=send --demo）。取自 2026-08-06 實跑的輸出。
SEND_DEMO_LINES = [
    "===== 開始送出 3 筆（送陳核收不回來）=====",
    "[1/3] MWAA1156007696 — 有關教育部委託本校辦理「115年至116年…",
    "      OK:點到公文「MWAA1156007696」",
    "      OK:已填入 PIN(策略: JS native setter)",
    "      OK:pinCode 視窗已關閉(系統完成簽章)",
    "      OK 已送陳核，寫入標記 MWAA1156007696已陳核.txt",
    "[2/3] MWAA1156007710 — 本校中國工業職業教育學會「115年度傑出…",
    "      OK 已送陳核，寫入標記 MWAA1156007710已陳核.txt",
]


class Hud:
    def __init__(self, base, since=0, demo=False, job="prep", label=None):
        import tkinter as tk
        self.tk = tk
        self.base = base
        self.since = since
        self.demo = demo
        self.job = job
        self.label = label or ("送陳核中…請不要動滑鼠" if job == "send"
                               else "收新公文中…請不要動滑鼠")
        # 結束時最重要的一句話是「可以動滑鼠了」,不是「完成」。
        self.done_label = ("✓ 送出完成 — 可以動滑鼠了" if job == "send"
                           else "✓ 收文完成 — 可以動滑鼠了")
        self.beeped = False
        self.lines = []
        self.miss = 0           # 連續連不上的次數
        self.closing = False
        self.demo_n = 0

        self.root = tk.Tk()
        self.root.title("跑文進度")
        self.root.configure(bg=BG)
        # 置頂。只設一次 —— 之後不再 lift／focus，避免搶走 KdApp 對話框的焦點。
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)

        w, h = 430, 190
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        # 右下角，留 24px 邊距避開工作列
        self.root.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 64}")

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=10, pady=(8, 2))
        self.state = tk.Label(head, text="連線中…", bg=BG, fg=FG,
                              font=("Microsoft JhengHei UI", 10, "bold"))
        self.state.pack(side="left")
        tk.Button(head, text="關閉", command=self.close, bg=BG, fg=DIM,
                  relief="flat", bd=0, highlightthickness=0,
                  activebackground=BG, activeforeground=FG,
                  font=("Microsoft JhengHei UI", 9)).pack(side="right")

        self.body = tk.Label(
            self.root, text="", bg=BG, fg=DIM, justify="left", anchor="nw",
            font=("Consolas", 9), wraplength=406)
        self.body.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self.root.after(200, self.tick)

    def beep(self, ok):
        """結束時響一聲。

        浮窗可能被 Chrome（尤其是簽章時跳出來的 pinCode 視窗）蓋住，聲音是
        唯一蓋不掉的信號 —— 不必盯著螢幕也知道可以動滑鼠了。
        """
        if self.beeped:
            return
        self.beeped = True
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_OK if ok else winsound.MB_ICONHAND)
        except Exception:
            try:
                self.root.bell()
            except Exception:
                pass

    def close(self):
        self.closing = True
        try:
            self.root.destroy()
        except Exception:
            pass

    def tick(self):
        if self.closing:
            return
        # 每輪重設置頂:Chrome 的彈出視窗（簽章用的 pinCode）會蓋過來。
        # 只設屬性、不 lift、不 focus —— 搶焦點會打斷 SetForegroundWindow
        # 那段（見檔頭「絕不搶焦點」）。
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        if self.demo:
            # 每秒吐一行假訊息,吐完切到「完成」狀態 —— 結束時長怎樣也要看得到,
            # 那才是最需要看清楚的一刻（可不可以動滑鼠就看那一行）。
            lines = SEND_DEMO_LINES if self.job == "send" else DEMO_LINES
            if self.demo_n < len(lines):
                self.lines.append(lines[self.demo_n])
                self.demo_n += 1
                self.state.config(text=self.label + "（展示）", fg=FG)
            else:
                self.state.config(text=self.done_label + "（展示）", fg=OK)
                self.beep(True)
            self.body.config(text="\n".join(self.lines[-SHOW_LINES:]))
            return self.root.after(POLL_MS, self.tick)
        d = _fetch(self.base, self.since, self.job)
        if d is None:
            self.miss += 1
            # **不可以因為短暫連不上就消失。** 浮窗不見了會被當成「跑完了」，
            # 人就去動滑鼠，而這時 Selenium 可能正在填字或簽章
            # （2026-08-06 送陳核第 3 筆就是這樣壞的）。撐到確定沒人在家才收。
            if self.miss >= LOST_GIVEUP:
                return self.close()
            if self.miss >= LOST_WARN:
                self.state.config(text="⚠ 連不上介面 — 工作可能還在跑，先別動滑鼠",
                                  fg=BAD)
            return self.root.after(POLL_MS, self.tick)

        self.miss = 0
        new = d.get("訊息") or []
        if new:
            self.lines.extend(new)
            del self.lines[:-KEEP_LINES]
            self.since = d.get("總行數", self.since + len(new))

        running = d.get("執行中")
        code = d.get("結束碼")
        if running:
            self.state.config(text=self.label, fg=FG)
        elif code is None:
            self.state.config(text="待命中", fg=DIM)
        elif code == 0:
            self.state.config(text=self.done_label, fg=OK)
        else:
            self.state.config(text="✗ 已停止 — 可以動滑鼠了，請看網頁上的訊息",
                              fg=BAD)

        self.body.config(text="\n".join(self.lines[-SHOW_LINES:]))

        if not running and code is not None:
            self.beep(code == 0)
            # 成功才自己收（留 20 秒讓人看到「可以動滑鼠了」）；
            # 失敗留著不關 —— 那句話跟訊息都要看得到。
            if code == 0:
                return self.root.after(CLOSE_AFTER_MS, self.close)
        self.root.after(POLL_MS, self.tick)

    def run(self):
        self.root.mainloop()


def _parse_args(argv):
    """拆命令列。回 (base, since, demo, job, label)。

    抽成函式是為了測得到 —— 這支平常一啟動就開視窗，塞在 main 裡沒法驗。
    """
    demo = "--demo" in argv
    job, label = "prep", None
    for a in argv:
        if a.startswith("--job="):
            job = a.split("=", 1)[1].strip() or "prep"
        elif a.startswith("--label="):
            label = a.split("=", 1)[1]
    if job not in ("prep", "send"):
        job = "prep"                        # 不認識的就當收文,不要因此開不起來
    rest = [a for a in argv if not a.startswith("--")]
    base = (rest[0] if rest else DEFAULT_BASE).rstrip("/")
    try:
        since = int(rest[1]) if len(rest) > 1 else 0
    except ValueError:
        since = 0
    return base, since, demo, job, label


def main():
    base, since, demo, job, label = _parse_args(sys.argv[1:])
    try:
        Hud(base, since, demo, job, label).run()
    except Exception as e:
        # 浮窗只是輔助 —— 壞了就安靜退場,絕不能影響正在跑的收文工作。
        print(f"[prep_hud] 啟動失敗:{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
