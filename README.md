# sssh-automation

臺北市松山高中資訊組自動化工具集。

## 專案結構

```
main.py                      # 主程式入口；預設執行 FEATURES[0]，跑完即退出回到 shell
taipeion_login_selenium.py   # 自然人憑證登入（Selenium DOM 版，主力）
taipeion_login.py            # 自然人憑證登入（pyautogui 像素點擊版，備援）
browser_utils.py             # pyautogui 版的共用工具庫（Chrome 視窗操作、像素偵測）
scripts/
  close-profile2-chrome.ps1  # 關閉所有 Selenium 相關 chrome.exe + 清 lock 檔
id.txt                       # 自然人憑證 PIN（gitignored，內容須使用者手動建立）
```

## 環境需求

- Windows 10（**必須在螢幕未鎖定狀態執行** — Windows 鎖屏會阻擋 HiCOS 讀卡）
- Python 3.14（路徑：`C:\Python314\`）
- Google Chrome
- HiCOS 自然人憑證跨平台元件 + 讀卡機 + 卡片

### 安裝相依套件

```powershell
C:\Python314\python.exe -m pip install pillow pyautogui selenium
```

Selenium Manager 會自動下載匹配的 ChromeDriver，不需手動安裝。

### 建立 id.txt（PIN 檔）

```powershell
"你的6位數PIN" | Out-File -FilePath id.txt -Encoding utf8 -NoNewline
```

> id.txt 已在 .gitignore，**絕對不會推上 GitHub**。

## 執行方式

```powershell
C:\Python314\python.exe main.py          # 預設跑 FEATURES[0]（Selenium 版）
C:\Python314\python.exe main.py 2        # 跑 FEATURES[1]（pyautogui 版備援）
```

執行前會 `taskkill /F /IM chrome.exe` 清掉所有 Chrome（含使用者個人 Chrome），跑完即退出回 shell。

## 功能說明

### 1. 自然人憑證登入（Selenium 版）

完整流程（約 15 秒）：
1. 啟動 Chrome 專用 profile（`%LOCALAPPDATA%\Chrome-Selenium`）
2. 開 `https://login.gov.taipei/login.php`
3. 預先點 Chrome 站台權限對話框「允許」（首次使用需此步，Chrome 會記住）
4. 點 自然人憑證 分頁
5. 若卡片偵測未完成（出現「重新檢測 / 重新偵測卡片」），自動點重試直到「登入」出現
6. 從 `id.txt` 讀 PIN 自動填入
7. 點「登入」送出
8. 跳轉到 `https://taipeion.gov.taipei/`（TAIPEION 入口網），登入完成

### 2. 自然人憑證登入（pyautogui 像素版，舊版）

保留作為備援。透過 Chrome 視窗截圖 + 青綠色像素群集偵測點擊位置。

## 故障排除

| 症狀 | 解法 |
|------|------|
| `session not created: Chrome instance exited` | 跑 `scripts\close-profile2-chrome.ps1` 清殘留與 lock 檔 |
| Chrome 跳「要儲存密碼嗎」對話框 | 按「不用了，謝謝」一次，Chrome 會永久記住 |
| 鎖屏下執行失敗 | **正常** — Windows 鎖屏會阻擋 HiCOS 讀卡，必須未鎖定 |
| PIN 一直填不進去 | 確認 id.txt 存在且內容為純 PIN 數字（無換行） |

## 新增功能

在 `main.py` 的 `FEATURES` 清單加入一列：

```python
FEATURES = [
    ("臺北市單一帳號認證平台 — 自然人憑證登入（Selenium 版）", login_taipeion_selenium),
    ("臺北市單一帳號認證平台 — 自然人憑證登入（pyautogui 像素版）", login_taipeion),
    ("新功能名稱", 新功能函式),   # ← 加在這裡
]
```
