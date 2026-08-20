# -*- coding: utf-8 -*-
"""
handover.py
把整套工具打包成**一個 zip**，可以直接給別人（別的處室、接手的人）。

    python handover.py

產出放在桌面:`自動辦文工具_交接_YYYYMMDD.zip`。對方解壓縮 → 點 `安裝.bat` → 完成。

⚠️ **這支的重點不是打包，是「不要把不該給的東西一起送出去」。**
這個資料夾裡混著三種絕對不能外流的東西:

  1. `env.env` —— 自然人憑證 PIN、校網帳密（PIN 外流比密碼更嚴重）
  2. `document_download*/` 與桌面審核表 —— **公文內容**（含他人業務、個資）
  3. `python-path.txt`、log、`.claude/settings.local.json` —— 這台機器的私事

判斷「什麼可以送」不自己另立一套:**用 git 的 `.gitignore`**。
理由是那份清單早就為了「這個 repo 是 public」而寫得很嚴（`env.env`、
所有 `*.xlsx`、`document_download*/` 全擋），而且承辦人與系管師都在維護它。
自己再寫一份判斷 = 兩套規則，遲早分岔，而分岔的方向可能是「以為擋了其實沒擋」。

而且 git 說可以之後**還要再過一次自己的黑名單**，打包完**再打開 zip 檢查一次**
—— 這一步不是多餘的:寫錯一次的代價是 PIN 或公文躺在別人的隨身碟裡，收不回來。
"""

import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 就算 git 說可以，符合這些的一律不打包。**兩道獨立的關卡**。
DENY = [
    "env.env", "id.txt", "python-path.txt",
    "*.xlsx", "*.xlsm", "*.xls", "*.csv",
    "*.log", "*.log.*", "*.png",
    "document_download/*", "document_download_closure/*",
    ".claude/settings.local.json",
    "*.pyc", "__pycache__/*",
    "*.zip",
]
# 上面那條 document_download/* 會連 .gitkeep 一起擋掉，但那個要留 ——
# 目錄不存在的話第一次收文會找不到地方放。
KEEP = ["document_download/.gitkeep", "document_download_closure/.gitkeep",
        "doc_classifier/training_data/.gitkeep"]

# 少了這幾個，這個 zip 就是廢的 —— 對方解開之後根本無從下手。
# 「多送了不該送的」與「少送了必要的」一樣要擋:2026-08-12 第一版就少了前三個
# （中文檔名被 git 轉義掉），而打包器什麼都沒說。
MUST = ["安裝.bat", "啟動.bat", "安裝說明.md", "install.py", "doctor.py",
        "requirements.txt", "ui.py", "ui_page.html", "env_example.env",
        "review_sheet.py", "main.py"]


# 這幾種檔**會被別的程式用「系統語系編碼」去讀**，放非 ASCII 就是在別人的機器上
# 埋地雷（自己這台常常看不出來）。打包前一律擋。
_ASCII_ONLY = (".bat",)
_ASCII_ONLY_NAMES = ("requirements.txt",)


def bat_problems(rels):
    """該純 ASCII 的檔裡有非 ASCII 就回問題清單。有問題**不准打包**。

    兩個都是 2026-08-14 當天實際炸過的:

    · `.bat` —— cmd 用系統 ANSI 字碼頁（這台是 Big5）讀，中文位元組會把後面的
      字元吃掉，整個檔案解析壞掉。症狀是**點兩下完全沒反應**。
    · `requirements.txt` —— **pip 用 `locale.getpreferredencoding()` 解這個檔**。
      同事那台（pip 24.2）直接 `UnicodeDecodeError: 'cp950' codec can't decode`，
      而這台（pip 26.1）好好的 —— 典型的「只在別人機器上壞」。

    這一道跟「少了必要的檔」同一個道理:寧可包不出來，也不要寄一個點不動的東西。
    """
    bad = []
    for rel in rels:
        base = os.path.basename(rel).lower()
        if not (rel.lower().endswith(_ASCII_ONLY) or base in _ASCII_ONLY_NAMES):
            continue
        raw = open(os.path.join(_BASE_DIR, rel), "rb").read()
        if raw.startswith(b"\xef\xbb\xbf"):
            bad.append(f"{rel}:有 UTF-8 BOM（cmd 會把它當成第一行的一部分）")
        for i, line in enumerate(raw.split(b"\n"), 1):
            try:
                line.decode("ascii")
            except UnicodeDecodeError:
                bad.append(f"{rel} 第 {i} 行有非 ASCII:"
                           f"{line.decode('utf-8', 'replace').strip()[:60]}")
    return bad


def denied(rel):
    r = rel.replace("\\", "/")
    if r in KEEP:
        return None
    for pat in DENY:
        if fnmatch.fnmatch(r, pat) or fnmatch.fnmatch(os.path.basename(r), pat):
            return pat
    return None


def listed_by_git():
    """git 眼中「這個 public repo 收得下的檔案」= 已追蹤 ＋ 沒被 ignore 的新檔。

    用 `--others --exclude-standard` 才會包含**還沒 commit 的新檔** ——
    不然剛做好還沒提的東西會打不進去（設定頁、安裝檔就是這種）。

    ⚠️ `-c core.quotepath=false` 不能拿掉。git 預設會把非 ASCII 檔名轉義成
    `"\\345\\256\\211..."`，於是 `os.path.isfile()` 對不到 → **默默跳過**。
    2026-08-12 實測:第一版打出來的 zip 少了 `安裝.bat`、`啟動.bat`、
    `安裝說明.md` —— 剛好就是對方最需要的三個檔，而打包器一句話都沒說。
    """
    try:
        out = subprocess.run(
            ["git", "-c", "core.quotepath=false",
             "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=_BASE_DIR, capture_output=True, text=True, encoding="utf-8",
            timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def listed_by_walk():
    """沒有 git 時的退路。**只收白名單副檔名**，寧可漏也不要多送。"""
    ok_ext = (".py", ".md", ".bat", ".txt", ".yaml", ".yml", ".env", ".html",
              ".json", ".gitkeep")
    skip_dir = {".git", "__pycache__", ".pytest_cache", "document_download",
                "document_download_closure", ".claude"}
    out = []
    for dp, dirs, names in os.walk(_BASE_DIR):
        dirs[:] = [d for d in dirs if d not in skip_dir]
        for n in names:
            if n.endswith(ok_ext) or n == ".gitkeep":
                out.append(os.path.relpath(os.path.join(dp, n), _BASE_DIR))
    return out + KEEP


SEVENZIP = [r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe"]


def _find_7z():
    for p in SEVENZIP:
        if os.path.isfile(p):
            sfx = os.path.join(os.path.dirname(p), "7z.sfx")
            if os.path.isfile(sfx):
                return p, sfx
    return None, None


def build_exe(keep, root, outdir, stamp):
    """做成**點兩下就能解開的 exe**（7-Zip 的自解模組）。回路徑或 None。

    刻意用 `7z.sfx`（純解壓縮）而**不是**那種「解到暫存資料夾再自己跑起來」的
    安裝式模組:這套工具的資料夾要長期留著（`env.env`、下載的公文、審核表設定
    都在裡面），解到暫存跑完就沒了正好是最糟的。
    所以流程是:點 exe → 選位置 → 解出「自動辦文工具」資料夾 → 進去點 安裝.bat。
    """
    exe7z, sfx = _find_7z()
    if not exe7z:
        print("\n ⚠️ 找不到 7-Zip（找 7z.exe 與 7z.sfx），跳過 exe，只給 zip。")
        return None
    stage = tempfile.mkdtemp(prefix="handover-")
    try:
        for rel in keep:
            dst = os.path.join(stage, root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(_BASE_DIR, rel), dst)
        archive = os.path.join(stage, "payload.7z")
        r = subprocess.run([exe7z, "a", "-t7z", "-mx=7", archive, root],
                           cwd=stage, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 or not os.path.isfile(archive):
            print(f"\n ⚠️ 7z 壓縮失敗，跳過 exe:{(r.stderr or r.stdout)[:200]}")
            return None
        # 檢查**壓進去的那份實物**（暫存目錄），而不是去解析 7z 的畫面輸出:
        # 7z 的輸出走 OEM 字碼頁，中文路徑用 UTF-8 解會變亂碼，於是每個檔都
        # 被判成「少了」（2026-08-12 第一版就是這樣自己騙自己）。
        inside = set()
        base = os.path.join(stage, root)
        for dp, _, names_ in os.walk(base):
            for n in names_:
                inside.add(os.path.relpath(os.path.join(dp, n), base)
                           .replace("\\", "/"))
        leak = [(n, denied(n)) for n in sorted(inside) if denied(n)]
        miss = [m for m in MUST if m not in inside]
        if leak or miss:
            print("\n ❌ exe 的內容檢查沒過，不產生 exe。")
            for n, pat in leak[:5]:
                print(f"   · 不該有:{n}（中 {pat}）")
            for m in miss[:5]:
                print(f"   · 少了:{m}")
            return None

        exe_path = os.path.join(outdir, f"自動辦文工具_交接_{stamp}.exe")
        with open(exe_path, "wb") as out:
            for part in (sfx, archive):
                with open(part, "rb") as f:
                    shutil.copyfileobj(f, out)
        # 再確認做出來的 exe 真的是完整的封存（不是接壞的一坨）。
        t = subprocess.run([exe7z, "t", exe_path], capture_output=True)
        if t.returncode != 0:
            os.remove(exe_path)
            print("\n ❌ 做出來的 exe 驗不過（7z t 失敗），已刪掉。")
            return None
        return exe_path
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main():
    print("=" * 62)
    print(" 打包交接用的檔案")
    print("=" * 62)

    files = listed_by_git()
    if files is None:
        print(" ⚠️ 沒有 git（或這裡不是 git 資料夾）—— 改用白名單掃描。")
        files = listed_by_walk()
        source = "白名單掃描"
    else:
        source = "git（照 .gitignore 的規則）"
    print(f" 檔案來源:{source}，共 {len(files)} 個候選")

    keep, drop = [], {}
    for rel in sorted(set(files + KEEP)):
        full = os.path.join(_BASE_DIR, rel)
        if not os.path.isfile(full):
            continue
        pat = denied(rel)
        if pat:
            drop.setdefault(pat, []).append(rel)
        else:
            keep.append(rel)

    if drop:
        print("\n 這些**刻意不打包**（第二道黑名單擋下的）:")
        for pat, rels in sorted(drop.items()):
            print(f"   · {pat} → {len(rels)} 個")
    if not keep:
        print("\n ❌ 沒有東西可以打包，停手。")
        return 1

    # 壞掉的 .bat 送出去 = 對方點兩下完全沒反應，而且看不出原因。寧可包不出來。
    bats = bat_problems(keep)
    if bats:
        print("\n ❌ 有檔案的編碼會在別人機器上炸掉，**不打包**:")
        for b in bats:
            print(f"   · {b}")
        print("\n    修法:中文一律交給 Python 印，.bat 只留 ASCII。")
        return 1

    stamp = datetime.now().strftime("%Y%m%d")
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    outdir = desktop if os.path.isdir(desktop) else _BASE_DIR
    zip_path = os.path.join(outdir, f"自動辦文工具_交接_{stamp}.zip")
    root = "自動辦文工具"          # 解壓縮後是一個資料夾，不會散一地

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in keep:
            z.write(os.path.join(_BASE_DIR, rel),
                    arcname=f"{root}/{rel}".replace("\\", "/"))

    # ── 打包完再打開檢查一次 ───────────────────────────────────────────
    # 前面兩道都是「事前判斷」。這一道是「事後看實物」——
    # 寫錯一次的代價是 PIN 或公文躺在別人的隨身碟裡，收不回來。
    bad = []
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        for n in names:
            rel = n[len(root) + 1:] if n.startswith(root + "/") else n
            pat = denied(rel)
            if pat:
                bad.append((rel, pat))
    if bad:
        os.remove(zip_path)
        print("\n ❌ 檢查沒過，zip 已刪掉。以下不該在裡面:")
        for rel, pat in bad[:10]:
            print(f"   · {rel}（中 {pat}）")
        return 1

    inside = {n[len(root) + 1:] for n in names if n.startswith(root + "/")}
    missing = [m for m in MUST if m not in inside]
    if missing:
        os.remove(zip_path)
        print("\n ❌ 少了必要的檔，zip 已刪掉（對方解開會無從下手）:")
        for m in missing:
            print(f"   · {m}")
        print("    中文檔名沒進去的話，先確認 git 版本支援 core.quotepath=false。")
        return 1

    size = os.path.getsize(zip_path) / 1024
    print(f"\n ✅ 好了:{zip_path}")
    print(f"    {len(names)} 個檔，{size:.0f} KB，解壓縮後是「{root}」資料夾")

    exe_path = build_exe(keep, root, outdir, stamp)
    if exe_path:
        print(f"\n ✅ 也做了點兩下就能解開的版本:{exe_path}")
        print(f"    {os.path.getsize(exe_path) / 1024:.0f} KB。"
              f"對方不用會用解壓縮軟體 —— 點下去選位置就好。")
        print(f"    （它只解壓縮，不會自己安裝什麼;解出來還是要點「安裝.bat」。）")
    print("\n 已確認**沒有**打包進去:")
    print("   · env.env（PIN、校網帳密）—— 收到的人要填自己的")
    print("   · 公文內容（document_download*/）與審核表（*.xlsx）")
    print("   · 這台機器的私事（python-path.txt、log、個人設定）")
    print("\n 給對方的三句話:")
    print("   1. 解壓縮到任何位置（例如 D:\\ 或桌面）")
    print("   2. 點兩下「安裝.bat」，照它講的做")
    print("   3. 點兩下「啟動.bat」，到「設定」頁填自己的 PIN 與校網帳密")
    print("\n 對方電腦要自備:Windows、Chrome、讀卡機＋HiCOS、Python 3.10+")
    print(" 詳細的在 zip 裡的「安裝說明.md」。")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
