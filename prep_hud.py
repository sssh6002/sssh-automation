# -*- coding: utf-8 -*-
"""
prep_hud.py
跑文進度小浮窗（右下角、永遠在最上層）。

    python prep_hud.py [http://127.0.0.1:8760] [起始行號]
    python prep_hud.py --demo      拿假訊息看長相（不連 ui.py，調版面用）

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
CLOSE_AFTER_MS = 8000   # 工作結束後幾毫秒自動關

BG = "#22272e"
FG = "#9fe1cb"
DIM = "#7d8590"
OK = "#7ee787"
BAD = "#ff7b72"


def _fetch(base, since):
    """抓 ui.py 的進度。回 dict 或 None（連不上）。"""
    url = f"{base}/api/prep/status?since={since}"
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


class Hud:
    def __init__(self, base, since=0, demo=False):
        import tkinter as tk
        self.tk = tk
        self.base = base
        self.since = since
        self.demo = demo
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

    def close(self):
        self.closing = True
        try:
            self.root.destroy()
        except Exception:
            pass

    def tick(self):
        if self.closing:
            return
        if self.demo:
            # 每秒多吐一行假訊息,吐完就停在原地讓人慢慢看版面。
            if self.demo_n < len(DEMO_LINES):
                self.lines.append(DEMO_LINES[self.demo_n])
                self.demo_n += 1
            self.state.config(text="收新公文中…請不要動滑鼠（展示模式）", fg=FG)
            self.body.config(text="\n".join(self.lines[-SHOW_LINES:]))
            return self.root.after(POLL_MS, self.tick)
        d = _fetch(self.base, self.since)
        if d is None:
            self.miss += 1
            # 連不上 5 次（約 5 秒）就收工 —— 多半是 ui.py 被關掉了
            if self.miss >= 5:
                return self.close()
            self.state.config(text="連不上介面…", fg=BAD)
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
            self.state.config(text="收新公文中…請不要動滑鼠", fg=FG)
        elif code is None:
            self.state.config(text="待命中", fg=DIM)
        elif code == 0:
            self.state.config(text="✓ 收文完成", fg=OK)
        else:
            self.state.config(text=f"已結束（代碼 {code}，請看網頁上的完整訊息）",
                              fg=BAD)

        self.body.config(text="\n".join(self.lines[-SHOW_LINES:]))

        # 跑完就別賴著 —— 完整 log 在網頁上,這裡只是跑的時候給個交代。
        if not running and code is not None:
            return self.root.after(CLOSE_AFTER_MS, self.close)
        self.root.after(POLL_MS, self.tick)

    def run(self):
        self.root.mainloop()


def main():
    args = [a for a in sys.argv[1:] if a != "--demo"]
    demo = "--demo" in sys.argv
    base = args[0] if args else DEFAULT_BASE
    since = int(args[1]) if len(args) > 1 else 0
    try:
        Hud(base.rstrip("/"), since, demo).run()
    except Exception as e:
        # 浮窗只是輔助 —— 壞了就安靜退場,絕不能影響正在跑的收文工作。
        print(f"[prep_hud] 啟動失敗:{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
