# document_closure_post_web 設計

日期：2026-06-03（2026-06-05 實機探索後更新：登入/發佈 selector、目標板、直接發佈）
分支：`feat/closure-post-web`

## 目標

結案存查流程下載並解壓公文後，若該公文目錄的 `*總結.*.md`「承辦文字」含「於官網公告」，
則自動把公文發佈到松高校網（www.sssh.tp.edu.tw）的**圖書館 → 公告文件**板：
以總結的**主旨**為標題、主旨下方的**條列摘要**為內容。

## 觸發鏈位置（含「如擬」閘門，第一道已現成）

```
document_closure.py  _process_one_pending_closure_doc()
  ├─ _has_approval_text()「如擬」判定                            ← 既有閘門(line ~1424)
  │     核決區無「如擬」→ 保守不動作(可能還在簽核)
  ├─ _download_and_extract() + _copy_meta_files_from_pending()   ← 既有
  ├─ 待結案勾選 + 存查表單 + PIN + 驗證歸檔成功                  ← 既有
  └─[新]─ maybe_post_announcement(driver, extract_dir)          ← 歸檔成功後呼叫
```

**「如擬」自動成立**：`_APPROVAL_KEYWORD = "如擬"` 已是結案存查的前置閘門
（核決區出現「如擬」才下載+存查）。`maybe_post_announcement` 掛在歸檔成功之後，
能跑到這的公文必為「如擬」核可 → 使用者要求的「等看到如擬再公告」自動滿足，**不需另寫判定**。

`maybe_post_announcement` **不 raise**；任何失敗只印 STOP banner、回 `False`，
**絕不影響已完成的存查歸檔**（與 `fill_in_draft` 容錯哲學一致）。觸發條件不符回 `False`（skipped，非錯誤）。

## 模組結構：document_closure/document_closure_post_web.py

```
maybe_post_announcement(driver, extract_dir)
  ├─[1] _parse_summary(extract_dir)        讀 *總結.*.md → {承辦文字, 主旨, body}
  ├─[2] 觸發判定：承辦文字 含「於官網公告」？否→return False(skipped)
  ├─[3] 防重複：extract_dir 已有 <主檔名>已公告.txt？是→skip 回 True
  ├─[4] title/body 取自 [1]
  ├─[5] _open_and_login_sssh(driver)       開新分頁 + 帳密登入（已登入則跳過）
  ├─[6] _submit_announcement(driver, title, body)  到 圖書館/公告文件(NetworkCenterAnnoucement) 填表「發布」
  ├─[7] 驗證發佈成功
  ├─[8] _write_posted_marker(extract_dir)  寫 <主檔名>已公告.txt（ISO 8601）
  └─[9] 關新分頁、切回 edoc 主分頁
```

### 單獨執行
`python document_closure/document_closure_post_web.py <公文目錄>`
只對**指定的那一個公文目錄**跑（讀該目錄 `*總結.*.md`、判觸發、發佈）；**不做全目錄 scan**。
未給目錄參數 → 印用法後結束。standalone 時自己 attach Chrome（沿用 `_standalone_open_chrome_at_edoc` 套路）。

## §2 總結.md 解析與觸發

實際格式（`document_download_closure/MWAA*/...總結.*.md`）：
```
#存查分類:研習 03750401      ← 第1行
##不參加                      ← ## 承辦文字（觸發判定看這行）
*發文日期：…
*發文字號：…
*主旨：本協會訂於…請查照。     ← 標題來源
1. …                          ← body 來源（主旨下方條列摘要）
2. …
```

- **承辦文字**：第一個 `^##`（非 `###`）開頭的行，去 `##` 與前後空白。
- **觸發**：承辦文字含子字串「於官網公告」(`in` 比對，容忍「於官網公告，並轉知各處室及師生」「於官網公告，不參加研習課程」等變體)。
- **標題 title**：找 `主旨[:：]` 的行，取冒號後文字 `strip()`（兼容有無 `*` 前綴、半/全形冒號）。
- **內容 body**：主旨行之後、檔尾之前，取 `^\s*\d+[.、]` 開頭的條列行，換行串接。
- **取不到時**：找不到 `*總結.*.md`、解析不到主旨/body → 印 STOP banner、回 False、不發佈（寧缺勿殘）。
  解析不到 `##` 承辦文字 → 視為不觸發（skipped）。

## §3 登入／發佈／帳密

### (a) 帳密檔：env.env（已完成）
`id.txt` 已改名為 `env.env`，升級為 key=value、`#` 註解：
```
# 帳密設定：# 開頭整行忽略
pin=自然人憑證PIN
sssh_account=校網帳號
sssh_password=校網密碼
sssh_publisher=系管師R   # 公告「發布者」欄(ct-person 必填,自動化開表單時為空)
```
- `taipeion_login_selenium._read_config(key)`：逐行 key=value 解析（# 註解、空行略過）。
- `_read_pin()`：先找 `pin=`；若整檔無 key=value（舊格式）則整檔當 PIN（向後相容）。
- `env.env` 已在 .gitignore。**真實 `sssh_account` / `sssh_password` 由使用者自行填入。**

### (b) 登入 `_open_and_login_sssh(driver)` — 帳密表單（2026-06-05 實機驗證 + 使用者指定流程）

校網登入是**單純帳密表單**，無 SSO 轉址、無自然人憑證、無 iframe、無 2FA。

**登入偵測依使用者指定**：從首頁 `https://www.sssh.tp.edu.tw/nss/p/index` 看右上角——
顯示「**登出**」= 已登入（跳過）；顯示「**登入**」= 未登入，點「登入」進登入頁。

1. 開新分頁、導到 `https://www.sssh.tp.edu.tw/nss/p/index`。
2. 偵測：頁面有「登出」連結 → 已登入，回 True（同 session 第 2 篇起跳過）。
3. 否則點「登入」連結（JS click）→ 轉到 `/passport/tpeEntrance` 登入頁，填表送出：
   - 帳號框 `#login-user-name`（`name=username`）← `sssh_account`
   - 密碼框 `#login-password`（`name=password`）← `sssh_password`
   - CSRF：`input[name=_csrf]` hidden，瀏覽器自動帶，不用處理
   - 登入鈕 `button.btn-primary[type=submit]`（text「登入」）→ **必須 JS click**
     （`element.click()` 會被覆蓋層攔截 `ElementClickInterceptedException`，比照既有 `_js_click`）
4. 驗證登入成功：送出後 `#login-user-name` 不再存在（仍在 = 帳密錯/2FA/驗證碼 → STOP banner 回 False）；
   著陸頁右上角「登入」變「登出」。

### (c) 發佈 `_submit_announcement(driver, title, body)` — 圖書館/公告文件（2026-06-05 實機驗證）

目標板：**圖書館 → 公告文件** = `https://www.sssh.tp.edu.tw/nss/s/main/p/NetworkCenterAnnoucement`
（`ANNO_URL`）。註：`/nss/s/main/p/library` 本頁的「新增公告」只在 NSS CMS 編輯模式才出現，
但 library 的「公告文件」連結就是此 URL，**直接導覽即有「新增公告」鈕、可自動化**（2026-06-06 實機驗證）。

1. 導到 `ANNO_URL` → **輪詢等**「新增公告」鈕出現（最多 ~10s）→ 點它（`button` text == 「新增公告」，JS click）。
2. 開出 CKEditor 5 表單，填：
   - **標題** `[id^="ct-title-"]`（required）← `title`
   - **內容** CKEditor 5 `.ck-editor__editable.ck-content`（**contenteditable DIV，非 iframe**）← `body`。
     用 `element.ckeditorInstance.setData(html)`（**實機驗證 api 模式成功**）；fallback `innerHTML`。
   - **發布者** `[id^="ct-person-"]`（required，**自動化開表單時為空**）← `env.env` 的 `sssh_publisher`。
   - ⚠️ 欄位 id 帶**每次載入變動的隨機後綴**（`ct-title-alro`、`ct-person-t2ib`…）→
     **一律用前綴比對 `[id^="ct-..."]`，絕不寫死完整 id**。
   - 其餘 required 欄實機已預填、不需動：`ct-etAnnoGroup-`發布單位（系管師群組）、
     `ct-pdate-`發布日期（今日）、`ct-ptime-`、`ct-sdate-`下架日期（+1月）、`ct-stime-`。
     `ct-topEndDate/Time`（置頂結束，未勾置頂不強制）、`ct-insertkey`（關鍵字）**實測留空仍可發布**。
3. 送出：點「**發布**」鈕（送出區三鈕：「取消」/「存為草稿」/「發布」；使用者選**直接發佈**）。
4. 驗證發佈成功：送出後標題框 `[id^="ct-title-"]` 消失（表單關閉）= 成功；仍在 = 必填未過/被擋 → STOP 回 False。

- **送出是對真實校網的不可逆外部動作**：送出前印 banner 列 title；
  **第一篇已於 2026-06-06 在使用者監看下實測發布、確認上架無誤**。

## §4 防重複／log／測試

- **防重複**：`_write_posted_marker(extract_dir)` 寫 `<主檔名>已公告.txt`，內容為 `ISO 8601_已公告`
  （比照 `_write_archive_marker` / `已存查.txt`）。`maybe_post_announcement` 開頭偵測到此檔即 skip。
- **log**：所有輸出走既有 run.log（stdout Tee）。每階段印 OK / STOP banner，遵守 ISO 8601 開頭規則由既有 logging 機制處理。
- **測試**：
  - 純函式單元測（不需瀏覽器）：`_parse_summary` 對多種 `*總結.*.md`（含/不含「於官網公告」、有無 `###`、半/全形冒號）解析出正確 title/body/觸發判定。用 `document_download_closure/` 既有檔 + 構造 fixture。
  - selector / 登入 / 發佈：實機跑（提交前依規則實測填字點擊驗證，不只 import）。

## 不做（YAGNI）
- 不做全目錄 scan 的批次發佈。
- 不做公告分類/標籤的智慧推斷（除非實機表單必填，屆時取最簡預設）。
- 不做圖片/附件上傳。
