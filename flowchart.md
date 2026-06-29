# sssh-automation 流程圖

臺北市公文系統自動化 — 依 [README.md](README.md) 整理的 Mermaid 流程圖。

<!-- 本檔由 README.md 內容生成；改流程請同步更新 README 與此檔 -->

---

## 1. 整體入口與功能分派（main.py）

```mermaid
flowchart TD
    Start(["py main.py [n]"]) --> Kill["taskkill /F /IM chrome.exe<br/>清掉所有 Chrome"]
    Kill --> Select{"選擇 FEATURES[n]"}

    Select -->|"預設 / 0"| F0["FEATURES[0]<br/>登入 + 點公文 + 公文系統處理"]
    Select -->|"2"| F1["FEATURES[1]<br/>pyautogui 像素版登入（備援）"]
    Select -->|"3"| F2["FEATURES[2]<br/>登入 + 點公文 + 結案存查迴圈"]

    F0 --> Done(["跑完退出回 shell"])
    F1 --> Done
    F2 --> Done
```

---

## 2. FEATURES[0] — 登入 + 點公文 + 公文系統處理（主力流程）

```mermaid
flowchart TD
    subgraph A["(A) 登入階段　taipeion_login_selenium.py"]
        A1["啟動 Chrome 專用 profile<br/>%LOCALAPPDATA%\\Chrome-Selenium"] --> A2["開 login.gov.taipei/login.php"]
        A2 --> A3["點 Chrome 站台權限「允許」"]
        A3 --> A4["點『自然人憑證』分頁"]
        A4 --> A5{"卡片偵測完成？"}
        A5 -->|否| A6["自動點『重新偵測卡片』重試"]
        A6 --> A5
        A5 -->|是| A7["從 env.env 讀 PIN 自動填入"]
        A7 --> A8["點『登入』送出"]
        A8 --> A9["跳轉 TAIPEION 入口網"]
    end

    subgraph B["(B) 點公文　click_document.py"]
        B1["找『公文系統』方塊<br/>JS click 跳轉 edoc.gov.taipei"]
    end

    subgraph C["(C) 公文系統 cascade　document_system.py"]
        C1{"催辦訊息 > 0？"} -->|是| C2["進入催辦頁處理"]
        C1 -->|否| C3
        C2 --> C3{"待簽收(N) > 0？"}
        C3 -->|是| C4["點入 → 全選 checkbox → 點『簽收』"]
        C3 -->|否| C5
        C4 --> C5["依序檢查：承辦中 → 受會案件 → 待結案"]
        C5 --> C6["第一個有待辦的點入，呼叫對應 handler"]
        C6 --> C7["承辦中：點清單最上方公文<br/>新分頁開啟 → 交給 pending_doc_handler"]
    end

    A9 --> B1
    B1 --> C1
    C7 --> D1

    subgraph D["(D) 公文閱覽器內動作　pending_doc_handler.py"]
        D1["切到閱覽器新分頁"] --> D2["點下載按鈕 #packageBtn<br/>Ext.fireAction('tap')"]
        D2 --> D3["等 KdApp『匯出公文資料』對話框"]
        D3 --> D4["Win32 EnumWindows 找窗 →<br/>SetForegroundWindow → 模擬鍵盤填路徑 + Enter"]
        D4 --> D5["等 zip 落到 document_download/<br/>（size 穩定 1s+）"]
        D5 --> D6["zipfile(metadata_encoding='cp950') 解壓<br/>→ document_download/&lt;公文文號&gt;/"]
        D6 --> D7["Flatten 內層『公文』/『來文』殼層 → 刪 zip"]
    end
```

---

## 3. 承辦中公文後處理（summarize → fill_in_draft）

```mermaid
flowchart TD
    Z["解壓 + flatten 完成"] --> S["summarize_doc.py（4-1-1）"]
    S --> S1["讀 summarize_doc.md 規格<br/>對主檔 PDF 抽文字"]
    S1 --> S2{"summarize_llm_order 鏈式呼叫"}
    S2 -->|1| L1["Antigravity agy CLI<br/>（ConPTY 偽終端機取回 stdout）"]
    S2 -->|2| L2["Google AI Studio Gemini API"]
    S2 -->|3| L3["本機 claude -p"]
    S2 -->|4| L4["Anthropic API key"]
    L1 --> SOut
    L2 --> SOut
    L3 --> SOut
    L4 --> SOut
    SOut["產出『公文主檔名內容.txt』總結<br/>含存查分類 / 承辦文字 / 動作"] --> F["fill_in_draft.py（4-2）"]

    F --> F1["讀總結標記"]
    F1 --> F2["套 fill_in_draft.yaml 模板填辦理文字"]
    F2 --> F3["儲存"]
    F3 --> F4{"依標記決定動作"}
    F4 -->|none| F5["留在承辦中（不動作）"]
    F4 -->|陳會| F6["送陳會 ⚠️ 真送公文，不可復原"]
```

---

## 4. FEATURES[2] — 結案存查迴圈（document_closure.py）

(A)~(C) 同 FEATURES[0]，processor 換成 `process_document_closure`。

```mermaid
flowchart TD
    Loop{"待結案(N) > 0？<br/>（max_iterations=30 保險）"} -->|否，N=0| End(["結束"])
    Loop -->|是| P1["點 sidebar『待結案』+ 清單第一筆<br/>記下 doc_no"]
    P1 --> P2["切到閱覽器新分頁"]
    P2 --> P3{"核決區含『如擬』？"}
    P3 -->|否| Skip["保守跳過不下載<br/>（可能還在簽核）"]
    Skip --> End
    P3 -->|是| P4["下載 zip → 解壓 → flatten<br/>→ document_download_closure/&lt;doc_no&gt;/"]
    P4 --> P5["從 document_download/&lt;doc_no&gt;/<br/>複製 *總結*.md + *內容.txt"]
    P5 --> P6["複製成功則刪承辦中重複目錄"]
    P6 --> P7["關閉閱覽器分頁，切回主分頁"]
    P7 --> P8["回清單，JS column-index 精準勾選同 doc_no 列"]
    P8 --> P9["點『存查』→ 等表單載入<br/>（sentinel=『確定存檔』）"]
    P9 --> P10["讀 *總結*.md #存查分類 下 8 位檔號"]
    P10 --> P11["填『檔號』第二格"]
    P11 --> P12["選『案次號』第一個 option<br/>等系統自動填保存年限"]
    P12 --> P13["verify 檔號+案次號 → 點『確定存檔』"]
    P13 --> P14{"KdApp pinCode popup<br/>自動填 PIN 成功？"}
    P14 -->|成功| P15["點『確定』等 popup 關閉"]
    P14 -->|失敗| P16["印 WARN 不中止<br/>給使用者手動操作時間"]
    P15 --> P17{"doc_no 從可見 tr 消失？<br/>（timeout 30s）"}
    P16 --> P17
    P17 --> P18["寫存查標記檔<br/>&lt;主檔名&gt;已存查.txt"]
    P18 --> P19{"總結承辦文字含『於官網公告』？"}
    P19 -->|是| P20["上網公告 document_closure_post_web<br/>（失敗只印 STOP 不影響歸檔）"]
    P19 -->|否| Loop
    P20 --> Loop
```

---

## 5. 上網公告（document_closure_post_web.py，5-2）

```mermaid
flowchart TD
    W0["歸檔後總結含『於官網公告』"] --> W1["登入校網<br/>（env.env sssh_account / password）"]
    W1 --> W2["圖書館頁進『模組』編輯模式"]
    W2 --> W3["點『新增公告』"]
    W3 --> W4["填標題（主旨）"]
    W4 --> W5["填內容（條列摘要）"]
    W5 --> W6["填發布者 sssh_publisher<br/>選發布單位 sssh_publish_unit"]
    W6 --> W7["設置頂 + 附件 *ATTCH*"]
    W7 --> W8["依總結 #### 多選同步分類<br/>（如 課外活動+研習資訊）"]
    W8 --> W9["發布"]
    W9 --> W10["寫 &lt;主檔名&gt;已公告.txt 防重複"]
```

---

## 6. 模組依賴關係

```mermaid
flowchart LR
    Main["main.py<br/>FEATURES 清單"]

    Main --> T1["[1] taipeion_login_selenium.py<br/>（主力登入 + 工具庫）"]
    Main --> T2["[2] taipeion_login.py<br/>（pyautogui 備援）"]
    T2 --> T21["[2-1] browser_utils.py"]
    Main --> T3["[3] click_document.py"]
    Main --> T4["[4] document_system.py<br/>cascade 分派"]

    T4 --> T41["[4-1] pending_doc_handler.py<br/>下載/解壓/flatten"]
    T41 --> T411["[4-1-1] summarize_doc.py<br/>LLM 鏈式總結"]
    T41 --> T42["[4-2] fill_in_draft.py<br/>填擬辦+動作"]

    Main --> T5["[5] document_closure/document_closure.py<br/>結案存查迴圈"]
    T5 -.->|"重用 _download_and_extract"| T41
    T5 --> T52["[5-2] document_closure_post_web.py<br/>上網公告"]

    Cls["[不使用] doc_classifier/<br/>處置動作分類器"]

    T1 -.->|工具庫| T3
    T1 -.->|工具庫| T4
    T1 -.->|工具庫| T41
```

> **輸出結束 LDC**
