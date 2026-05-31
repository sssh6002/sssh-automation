"""4-2:承辦中公文擬寫辦理文字。

依 docs/superpowers/specs/2026-05-27-fill-in-draft-design.md。
讀 summarize_doc 產出的總結檔取標記 → 查 fill_in_draft.yaml 對應表得
「辦理文字 + 動作」→ 於公文閱覽器分頁填字、儲存、依動作決定不動作/陳會。
"""

import pathlib

import yaml

_BASE_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = _BASE_DIR / "fill_in_draft.yaml"


def _read_marks(extract_dir):
    """從 extract_dir 找 *總結*.md,解析 `## 標記1 標記2` 行,回標記 list。

    找不到總結檔、或沒有以 `##` 開頭的標記行 → 回 []。
    (存查分類行開頭是單一 `#`,不會被誤判為標記行。)
    """
    extract_dir = pathlib.Path(extract_dir)
    summaries = sorted(extract_dir.glob("*總結*.md"))
    if not summaries:
        return []
    for raw in summaries[0].read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("##"):
            return line.lstrip("#").split()
    return []


def _load_rules(config_path=CONFIG_PATH):
    """讀 yaml 設定,回 (rules, default)。

    rules:list of dict(標記/優先序/辦理文字/動作);default:dict(辦理文字/動作)。
    """
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    rules = cfg.get("rules") or []
    default = cfg.get("default") or {"辦理文字": "擬:", "動作": "none"}
    return rules, default


def _lookup(marks, rules, default):
    """依優先序由小到大掃描 rules,第一個 `標記 in marks` 命中的決定一切。

    全部沒命中 → 回 default 的 (辦理文字, 動作)。
    """
    for rule in sorted(rules, key=lambda r: r.get("優先序", 0)):
        if rule.get("標記") in marks:
            return rule.get("辦理文字", ""), rule.get("動作", "none")
    return default.get("辦理文字", ""), default.get("動作", "none")


def _fill_text(driver, text):
    """在公文閱覽器分頁定位辦理文字輸入框並填入 text。回 True/False。

    真實選擇器於 Task 4 實機探查後填入;在那之前回 False。
    """
    print("[fill_in_draft] _fill_text 尚未接上真實選擇器 (Task 4)")
    return False


def _save(driver):
    """點「儲存」鈕並確認成功。回 True/False。Task 4 填真實作。"""
    print("[fill_in_draft] _save 尚未接上真實選擇器 (Task 4)")
    return False


def _click_chen_hui(driver):
    """點「陳會」鈕。回 True/False。Task 4 填真實作。"""
    print("[fill_in_draft] _click_chen_hui 尚未接上真實選擇器 (Task 4)")
    return False


def fill_in_draft(driver, extract_dir, config_path=CONFIG_PATH):
    """4-2 進入點:讀標記→查表→填辦理文字→儲存→依動作不動作/陳會。

    全程不 raise:任何例外都記 log 並回 False,不影響 4-1 已完成的下載/總結。
    """
    try:
        marks = _read_marks(extract_dir)
        rules, default = _load_rules(config_path)
        text, action = _lookup(marks, rules, default)
        print(f"[fill_in_draft] 標記={marks} → 動作={action},辦理文字={text!r}")

        if not _fill_text(driver, text):
            print("[fill_in_draft] 填辦理文字失敗,中止(不儲存、不動作)。")
            return False
        if not _save(driver):
            print("[fill_in_draft] 儲存失敗,中止(不動作)。")
            return False

        if action == "陳會":
            if not _click_chen_hui(driver):
                print("[fill_in_draft] 陳會失敗;狀態停在『已儲存未送』,可人工接手。")
                return False
        elif action == "none":
            pass
        else:
            print(f"[fill_in_draft] 動作 {action!r} 目前未實作,僅儲存不執行後續。")
        return True
    except Exception as e:
        print(f"[fill_in_draft] 例外(不影響 4-1):{type(e).__name__}: {e}")
        return False


def _dry_run(extract_dir, config_path=CONFIG_PATH):
    """乾跑:只讀標記+查表,印出「會填什麼/會執行什麼動作」,不做 Selenium。

    用途:給某個既有 extract_dir(已有 *總結*.md),驗證 yaml 規則是否如預期。
    Selenium 真實操作交由 main.py 主流程跑到 pending_doc_handler 時自然觸發。
    """
    extract_dir = pathlib.Path(extract_dir)
    marks = _read_marks(extract_dir)
    rules, default = _load_rules(config_path)
    text, action = _lookup(marks, rules, default)
    print(f"[fill_in_draft] extract_dir={extract_dir}")
    print(f"[fill_in_draft] 標記={marks}")
    print(f"[fill_in_draft] → 動作={action}")
    print(f"[fill_in_draft] → 辦理文字={text!r}")
    return text, action


if __name__ == "__main__":
    # standalone:給一個既有 extract_dir(預設 document_download/<doc_no>/),
    # 跑乾跑模式印出「將填什麼/將執行什麼」。不開 Chrome、不做 Selenium 操作。
    # 真實 4-2 整合測試請跑 `python main.py`(cascade → pending_doc_handler →
    # 4-2 自然觸發),或在 Chrome 已停在公文閱覽器分頁時以 driver attach 接續。
    import sys

    if len(sys.argv) < 2:
        print("用法: python fill_in_draft.py <extract_dir>")
        print("  乾跑某筆已下載+總結的公文,印出 4-2 會填的辦理文字與動作。")
        print("  例: python fill_in_draft.py document_download\\MWAA1156005008")
        sys.exit(1)
    _dry_run(sys.argv[1])
