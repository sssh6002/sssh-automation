# -*- coding: utf-8 -*-
"""
install.py
安裝與環境檢查的本體。**由「安裝.bat」呼叫**，也可以直接跑:

    python install.py

⚠️ 為什麼安裝流程要拆成「一支 ASCII 的 .bat ＋ 這支 Python」:
cmd 讀 `.bat` 用的是**系統 ANSI 字碼頁（這台是 Big5/cp950）**，不是 UTF-8。
所以 `.bat` 裡只要有中文，整個檔就會被解析壞（2026-08-12 實測:`@echo off`
被吃成 `cho`，整支跑不動）。結論:**`.bat` 只留 ASCII，中文一律由 Python 印。**

這支做五件事，全部都是可以重跑的（跑第二次不會壞）:
  1. 把「跑這支的那個 Python」記到 python-path.txt —— 啟動.bat 會用同一支。
     這台機器上有兩個 Python，只有一支裝了 openpyxl（交接檔坑 #4），
     選錯的症狀是「審核表整個寫不進去」而且訊息很無辜。
  2. 裝 requirements.txt 的套件（裝到**這一支** Python 上）
  3. 沒有 env.env 就從 env_example.env 複製一份（有的話絕不覆蓋）
  4. 在桌面放一個捷徑（指向 啟動.bat）
  5. 跑 doctor.py 把還缺什麼一次講完
"""

import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYPATH_FILE = os.path.join(_BASE_DIR, "python-path.txt")
REQ = os.path.join(_BASE_DIR, "requirements.txt")
ENV = os.path.join(_BASE_DIR, "env.env")
EXAMPLE = os.path.join(_BASE_DIR, "env_example.env")

LINE = "=" * 62


def step(n, text):
    print(f"\n[{n}/5] {text}")


def make_desktop_shortcut():
    """在桌面放一個「自動辦文工具」捷徑，指向 啟動.bat。

    為什麼:不然每天要進資料夾找那個 bat。承辦人 2026-08-14 要的。

    用 PowerShell 的 WScript.Shell 建 .lnk —— **不靠 pywin32**:這一步跑在
    「套件裝好了沒」還不一定的位置，而且捷徑只是方便，不該因為少個套件就整支
    安裝失敗。**best-effort:失敗一律只警告。**
    """
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(desktop):
        print("      ⚠️ 找不到桌面資料夾，跳過捷徑。")
        return None
    lnk = os.path.join(desktop, "自動辦文工具.lnk")
    target = os.path.join(_BASE_DIR, "啟動.bat")
    if not os.path.isfile(target):
        print("      ⚠️ 找不到 啟動.bat，跳過捷徑。")
        return None
    ps = (
        "$s=(New-Object -COM WScript.Shell).CreateShortcut({lnk});"
        "$s.TargetPath={tgt};"
        "$s.WorkingDirectory={wd};"
        "$s.Description='自動辦文工具 — 點兩下開介面';"
        "$s.Save()"
    ).format(lnk=_ps_quote(lnk), tgt=_ps_quote(target), wd=_ps_quote(_BASE_DIR))
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"      ⚠️ 捷徑建不起來（{type(e).__name__}），不影響使用。")
        return None
    if r.returncode != 0 or not os.path.isfile(lnk):
        print(f"      ⚠️ 捷徑建不起來，不影響使用:"
              f"{(r.stderr or r.stdout or '').strip()[:150]}")
        return None
    return lnk


def _ps_quote(s):
    """PowerShell 單引號字串:內部的單引號要變兩個。"""
    return "'" + str(s).replace("'", "''") + "'"


def main():
    os.chdir(_BASE_DIR)
    print(LINE)
    print(" 自動辦文工具 — 安裝")
    print(LINE)
    print(" 這支不會動任何公文、不會連 edoc、不會貼校網。")
    print(" 跑第二次也不會壞，出問題就再跑一次。")

    # 1. 記下這支 Python
    step(1, "記下這台機器要用哪一支 Python")
    print(f"      {sys.executable}")
    try:
        with open(PYPATH_FILE, "w", encoding="utf-8") as f:
            f.write(sys.executable + "\n")
        print(f"      已記到 python-path.txt（啟動.bat 會用同一支）")
    except OSError as e:
        print(f"      ⚠️ 記不下來（{e}）—— 啟動.bat 會改用 PATH 上的 python。")
    if sys.version_info < (3, 10):
        print(f"      ❌ 版本太舊:{sys.version.split()[0]}，請裝 3.10 以上。")
        return 1

    # 2. 套件
    step(2, "安裝相依套件（第一次會跑一兩分鐘）")
    if not os.path.isfile(REQ):
        print("      ❌ 找不到 requirements.txt —— 這個資料夾是不是不完整？")
        return 1
    # 刻意不做 pip 自我升級 —— 那會動到這台機器上別的東西，不是裝這套必要的。
    rc = subprocess.call([sys.executable, "-m", "pip", "install", "-r", REQ])
    if rc != 0:
        print("\n      ❌ 套件沒裝完。常見原因:沒有網路、或單位網路擋了 pip。")
        print("      再跑一次這支;還是不行就把上面整段訊息給資訊人員看。")
        return 1

    # 3. env.env
    step(3, "設定檔 env.env")
    if os.path.isfile(ENV):
        print("      已經有了，不動它。（要改帳密請用介面的「設定」頁。）")
    elif os.path.isfile(EXAMPLE):
        shutil.copy(EXAMPLE, ENV)
        print("      已從 env_example.env 複製一份出來。")
        print("      ⚠️ 裡面是**範例值**，等一下一定要用介面的「設定」頁")
        print("         填自己的 PIN 與校網帳密 —— 那幾個是個人的，不跟著工具走。")
    else:
        print("      ⚠️ 找不到 env_example.env，跳過。第一次進「設定」頁存檔時會自己建。")

    # 4. 桌面捷徑
    step(4, "桌面捷徑")
    lnk = make_desktop_shortcut()
    if lnk:
        print(f"      已放在桌面:自動辦文工具")
        print("      （點兩下就開介面。黑色視窗不要關 —— 那是程式本體。）")

    # 5. 環境檢查
    step(5, "環境檢查")
    print()
    rc = subprocess.call([sys.executable, os.path.join(_BASE_DIR, "doctor.py")])
    print()
    print(LINE)
    if rc == 0:
        print(" 裝好了 ✅　點兩下桌面的「自動辦文工具」開介面")
        print("           （或這個資料夾裡的「啟動.bat」）。")
        print(" 第一次進去請先到「設定」頁填自己的 PIN 與校網帳密。")
    else:
        print(" 還有事情要處理 —— 看上面每個 ❌ 下面那行「→」怎麼修。")
        print(" 修好之後再跑一次「安裝.bat」就會重新檢查。")
    print(LINE)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
