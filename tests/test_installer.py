# -*- coding: utf-8 -*-
"""安裝／啟動那兩支 `.bat` 的把關測試。

**只釘一件事，但那一件會讓整支檔案完全不動:`.bat` 裡不可以有任何非 ASCII。**

cmd.exe 讀 `.bat` 用的是**系統 ANSI 字碼頁**（這台是 Big5/cp950），不是 UTF-8。
中文的位元組會把後面那個字元吃掉，於是整個檔案的語法就散了。踩過兩次:

  · 2026-08-12:`安裝.bat` 裡放中文 → `@echo off` 被吃成 `cho`，整支跑不動。
  · 2026-08-14:`啟動.bat` 裡只是**在註解與錯誤訊息裡提到一個中文檔名**
    （`安裝.bat`），承辦人點桌面捷徑**完全沒反應**。
    當時測試用的是另外抄的 ASCII 副本 —— **沒有真的跑過那個檔本身**，
    所以測不出來。這條測試就是補那個洞:直接讀檔案的位元組。

檔名本身是中文沒關係（那是檔案系統的事），**內容**不行。
"""

import os

import pytest

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 這幾個檔會被**別的程式用系統語系編碼**去讀:
#   .bat            → cmd 用 ANSI 字碼頁（Big5）
#   requirements.txt → pip 用 locale.getpreferredencoding()
# 兩個都在 2026-08-14 實際炸過，而且**都是在別人的機器上才炸**。
BATS = ["安裝.bat", "啟動.bat", "requirements.txt"]


@pytest.mark.parametrize("name", BATS)
def test_bat_is_pure_ascii(name):
    p = os.path.join(_BASE, name)
    assert os.path.isfile(p), f"{name} 不見了 —— 交接包就是靠它"
    raw = open(p, "rb").read()
    bad = []
    for i, line in enumerate(raw.split(b"\n"), 1):
        try:
            line.decode("ascii")
        except UnicodeDecodeError:
            bad.append(f"  第 {i} 行:{line.decode('utf-8', 'replace').strip()[:70]}")
    assert not bad, (
        f"{name} 裡有非 ASCII —— cmd 用系統字碼頁讀 .bat，這會讓整支檔案"
        f"解析壞掉（點兩下沒反應）。中文訊息一律交給 Python 印:\n"
        + "\n".join(bad))


def test_requirements_lists_openpyxl():
    """openpyxl 少了整個介面等於廢掉，而 README 舊版就是漏了它。"""
    txt = open(os.path.join(_BASE, "requirements.txt"), encoding="ascii").read()
    for pkg in ("openpyxl", "selenium", "pyyaml", "pywin32"):
        assert pkg in txt


@pytest.mark.parametrize("name", BATS)
def test_bat_has_no_bom(name):
    """UTF-8 BOM 會被 cmd 當成第一行的一部分，第一個指令就壞掉。"""
    raw = open(os.path.join(_BASE, name), "rb").read()
    assert not raw.startswith(b"\xef\xbb\xbf"), f"{name} 有 BOM"


def test_launcher_points_at_ui():
    """啟動.bat 要真的去跑 ui.py，而且用記下來的那支 Python（坑 #4）。"""
    txt = open(os.path.join(_BASE, "啟動.bat"), encoding="ascii").read()
    assert "ui.py" in txt
    assert "python-path.txt" in txt


def test_setup_bat_refuses_to_run_alone():
    """單獨複製一個 .bat 過去是跑不動的 —— 那時要講人話，不是吐英文錯誤。

    2026-08-12 實測:單獨一個 bat 只會吐 `can't open file install.py`。
    """
    txt = open(os.path.join(_BASE, "安裝.bat"), encoding="ascii").read()
    assert 'if not exist "install.py"' in txt
    assert 'if not exist "ui.py"' in txt


# ── 點兩次捷徑 ─────────────────────────────────────────────────────────────

def test_second_launch_opens_the_existing_page(monkeypatch, capsys):
    """已經有一個在跑的時候再點一次 → 只把那一頁打開，不再起一個服務。

    ⚠️ 這裡**不能靠 bind 失敗**來偵測:`HTTPServer.allow_reuse_address = 1`，
    而 Windows 的 SO_REUSEADDR 允許兩個 socket 綁同一個埠（跟 Linux 相反）——
    2026-08-14 實測第二支就這樣默默起來了，兩個服務搶同一個埠。
    所以 ui.main() 是**啟動前先連連看**。
    """
    import ui
    opened = []
    monkeypatch.setattr(ui, "already_running", lambda: True)
    monkeypatch.setattr(ui.webbrowser, "open", lambda u: opened.append(u))
    started = []
    monkeypatch.setattr(ui, "ThreadingHTTPServer",
                        lambda *a, **k: started.append(1))
    with pytest.raises(SystemExit) as e:
        ui.main()
    assert e.value.code == 0                 # 不是錯誤,別讓啟動.bat 跳紅字
    assert opened == [f"http://{ui.HOST}:{ui.PORT}/"]
    assert started == []                     # 沒有再起一個服務
    assert "已經有一個介面在跑" in capsys.readouterr().out
