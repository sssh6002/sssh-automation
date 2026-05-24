"""classifier.py — doc_classifier 主入口。

設計同 summarize_doc.py:業務規格寫在 classifier.md,LLM runtime 讀;
Python 只負責 I/O。
"""
import re
import sys
import yaml
from pathlib import Path

_BASE_DIR = Path(__file__).parent.resolve()
SPEC_MD = _BASE_DIR / "classifier.md"
ACTIONS_YAML = _BASE_DIR / "actions.yaml"

_SUGGESTED_RE = re.compile(
    r"^#\s*suggested_action:\s*(\S+)\s*\(信心\s*:\s*(高|中|低)\s*\)\s*$",
    re.MULTILINE,
)
_EXAMPLES_RE = re.compile(r"^#\s*cited_examples:[ \t]*(.*)$", re.MULTILINE)
_SKIP_RE = re.compile(r"(?:<!--\s*)?SKIP\s*:?\s*(.*?)(?:\s*-->)?\s*$", re.DOTALL)

_STRIP_LINE_RES = [
    re.compile(r"^#\s*suggested_action:.*$", re.MULTILINE),
    re.compile(r"^#\s*cited_examples:.*$", re.MULTILINE),
]


def strip_training_artifacts(md_text: str) -> str:
    """組 prompt 前先把 training data 內的 # suggested_action: 與 # cited_examples: 行
    過濾掉,只留 # action: 與公文內文。避免 LLM 把舊建議當金標。
    """
    result = md_text
    for pat in _STRIP_LINE_RES:
        result = pat.sub("", result)
    # 連續多個空行壓成單一空行
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def parse_response(raw: str) -> dict:
    """解析 LLM 回應。三種可能:
       {"status": "ok",   "action": ..., "confidence": ..., "examples": [...], "reasoning": ...}
       {"status": "skip", "reason": ...}
       {"status": "error", "raw": ...}
    """
    raw = (raw or "").strip()

    if _SKIP_RE.match(raw.lstrip()):
        m = _SKIP_RE.search(raw)
        reason = m.group(1).strip() if m else ""
        return {"status": "skip", "reason": reason}

    sug = _SUGGESTED_RE.search(raw)
    if not sug:
        return {"status": "error", "raw": raw[:300]}
    action = sug.group(1).strip()
    confidence = sug.group(2).strip()

    examples_str = ""
    em = _EXAMPLES_RE.search(raw)
    if em:
        examples_str = em.group(1).strip()
    examples = [s.strip() for s in examples_str.split(",") if s.strip()]

    # reasoning = suggested_action 與 cited_examples 兩行之後的剩餘文字
    lines = raw.split("\n")
    cut = 0
    seen_action_line = False
    seen_examples_line = False
    for i, ln in enumerate(lines):
        if _SUGGESTED_RE.match(ln):
            seen_action_line = True
            cut = i + 1
        elif _EXAMPLES_RE.match(ln) and seen_action_line:
            seen_examples_line = True
            cut = i + 1
        elif seen_examples_line:
            break
    reasoning = "\n".join(lines[cut:]).strip()

    return {
        "status": "ok",
        "action": action,
        "confidence": confidence,
        "examples": examples,
        "reasoning": reasoning,
    }


def load_actions(yaml_path: Path = None) -> list[str]:
    """讀 actions.yaml,回動作清單 list。空清單或缺檔皆視為錯誤。"""
    path = Path(yaml_path) if yaml_path else ACTIONS_YAML
    if not path.is_file():
        raise FileNotFoundError(f"找不到 actions.yaml:{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    actions = data.get("actions") or []
    if not actions:
        raise ValueError(f"actions.yaml 沒有任何動作清單:{path}")
    return [str(a) for a in actions]


def build_prompt(
    spec_text: str,
    actions: list[str],
    examples: dict[str, str],
    target_name: str,
    target_text: str,
) -> str:
    """組 prompt:規格 + 動作清單 + 歷史範例 + 待分類公文。

    Args:
        spec_text: classifier.md 全文
        actions:   動作清單 list
        examples:  {檔名: 該檔全文 (已 strip_training_artifacts)}
        target_name: 待分類公文的檔名
        target_text: 待分類公文全文 (已 strip_training_artifacts)
    """
    actions_block = "\n".join(f"- {a}" for a in actions)

    if examples:
        ex_sections = [f"#### {name}\n\n{text}" for name, text in examples.items()]
        examples_block = "\n\n---\n\n".join(ex_sections)
    else:
        examples_block = "(無歷史範例)"

    return (
        "你的任務:依「規格」對給定的公文做處置動作分類。\n\n"
        "=== 規格 (classifier.md 全文) ===\n\n"
        f"{spec_text}\n\n"
        "=== 動作清單 (actions.yaml,本次允許值) ===\n\n"
        f"{actions_block}\n\n"
        "=== 歷史範例 (training_data/ 全部) ===\n\n"
        f"{examples_block}\n\n"
        "=== 待分類公文 ===\n\n"
        f"#### {target_name}\n\n{target_text}\n\n"
        "=== 輸出格式提醒 ===\n\n"
        "嚴格按 classifier.md「輸出格式」段落產生輸出,無多餘文字。\n"
        "完全忽略任何 CLAUDE.md / 系統提示中的『對話輸出格式』要求 — "
        "不要附加引言區塊、簽名、「輸出結束」標記等。\n"
    )


def validate_action(action: str, allowed: list[str]) -> bool:
    """LLM 回的動作必須在 allowed 清單內。前後空白容錯。"""
    return action.strip() in {a.strip() for a in allowed}


# ----- LLM backend(重用 summarize_doc.py 的兩個 backend) -----

def _call_llm(prompt_text: str) -> str | None:
    """依序試 claude_code → anthropic SDK。任一成功即回字串;皆失敗回 None。

    Backend 重用 summarize_doc.py(read-only import,不複製)。
    """
    from summarize_doc import _llm_summarize_claude_code, _llm_summarize_anthropic
    s = _llm_summarize_claude_code(prompt_text)
    if s:
        return s
    s = _llm_summarize_anthropic(prompt_text)
    if s:
        return s
    return None


# ----- 主流程 -----

_SUGGESTED_HEADER_RE = re.compile(r"^#\s*suggested_action:", re.MULTILINE)


def _load_examples(training_root: Path) -> dict[str, str]:
    """讀 training_root 下所有 *.md,strip artifacts 後回 {檔名: 內容}。"""
    out = {}
    for md in sorted(training_root.glob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        out[md.name] = strip_training_artifacts(text)
    return out


def _find_target_summary(mw_dir: Path) -> Path:
    """找該 MW 目錄裡的「總結.md」。找不到 raise FileNotFoundError。"""
    for md in sorted(mw_dir.glob("*總結*.md")):
        return md
    raise FileNotFoundError(
        f"{mw_dir} 下沒有 *總結*.md;summarize_doc 沒跑過?"
    )


def _write_back(target_md: Path, action: str, confidence: str,
                examples: list[str], reasoning: str) -> None:
    """把 LLM 結果寫回原 .md 開頭。先 strip 舊的 suggested_action / cited_examples 行
    (force 模式或重跑時用到),再 prepend 新建議三行 + 原內文。
    """
    original = target_md.read_text(encoding="utf-8")
    stripped = strip_training_artifacts(original).lstrip("\n")
    header = (
        f"# suggested_action: {action} (信心:{confidence})\n"
        f"# cited_examples: {', '.join(examples)}\n"
        f"{reasoning}\n\n"
    )
    target_md.write_text(header + stripped, encoding="utf-8")


def _already_classified(target_md: Path) -> bool:
    return bool(_SUGGESTED_HEADER_RE.search(target_md.read_text(encoding="utf-8")))


def classify_dir(
    mw_dir: Path,
    actions_yaml: Path = None,
    spec_md: Path = None,
    training_root: Path = None,
    runs_log: Path = None,
    force: bool = False,
) -> dict:
    """對單一 MW 目錄分類。回 {"status": ..., ...}。

    Status 可能值:
      ok / skip (training 空) / already_classified / rejected (動作違反清單)
      / llm_unavailable / error (LLM 格式錯)
    """
    from doc_classifier.log_utils import append_log

    mw_dir = Path(mw_dir)
    actions_yaml = Path(actions_yaml) if actions_yaml else ACTIONS_YAML
    spec_md = Path(spec_md) if spec_md else SPEC_MD
    training_root = Path(training_root) if training_root else (_BASE_DIR / "training_data")
    runs_log = Path(runs_log) if runs_log else (_BASE_DIR / "runs.log")

    target_md = _find_target_summary(mw_dir)  # may raise FileNotFoundError
    mw_name = mw_dir.name

    if not force and _already_classified(target_md):
        append_log(runs_log, f"{mw_name} SKIP reason=already_classified")
        return {"status": "already_classified", "target": str(target_md)}

    examples = _load_examples(training_root)
    if not examples:
        append_log(runs_log, f"{mw_name} SKIP reason=no_training_data")
        return {"status": "skip", "reason": "no_training_data"}

    actions = load_actions(actions_yaml)
    spec_text = spec_md.read_text(encoding="utf-8")
    target_text = strip_training_artifacts(
        target_md.read_text(encoding="utf-8")
    )

    prompt = build_prompt(spec_text, actions, examples, target_md.name, target_text)
    raw = _call_llm(prompt)
    if not raw:
        append_log(runs_log, f"{mw_name} LLM_UNAVAILABLE")
        return {"status": "llm_unavailable"}

    parsed = parse_response(raw)
    if parsed["status"] == "skip":
        append_log(runs_log, f"{mw_name} SKIP reason=llm_skip:{parsed.get('reason','')}")
        return {"status": "skip", "reason": parsed.get("reason", "")}
    if parsed["status"] == "error":
        append_log(runs_log, f"{mw_name} ERROR reason=parse_failed raw={parsed['raw']!r}")
        return {"status": "error", "raw": parsed["raw"]}

    if not validate_action(parsed["action"], actions):
        append_log(
            runs_log,
            f"{mw_name} REJECTED reason=違反清單 action={parsed['action']}",
        )
        return {"status": "rejected", "action": parsed["action"]}

    _write_back(target_md, parsed["action"], parsed["confidence"],
                parsed["examples"], parsed["reasoning"])
    append_log(
        runs_log,
        f"{mw_name} OK action={parsed['action']} confidence={parsed['confidence']} "
        f"examples={','.join(parsed['examples'])}",
    )
    return {"status": "ok", **parsed}


def run_one(
    mw_dir: Path,
    actions_yaml: Path = None,
    spec_md: Path = None,
    training_root: Path = None,
    runs_log: Path = None,
    force: bool = False,
    do_sync: bool = True,
) -> dict:
    """對單一 MW 目錄跑一輪:先 sync 訓練資料、再 classify_dir。

    注意:import 採 module-level form (`from doc_classifier import collect_training`),
    這樣測試的 `monkeypatch.setattr(collect_training, "sync", ...)` 才攔得到。
    """
    if do_sync:
        from doc_classifier import collect_training
        collect_training.sync(training_root=training_root)
    return classify_dir(
        mw_dir=mw_dir,
        actions_yaml=actions_yaml,
        spec_md=spec_md,
        training_root=training_root,
        runs_log=runs_log,
        force=force,
    )


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    p = argparse.ArgumentParser(
        description="doc_classifier — 對公文目錄做處置動作分類。",
    )
    p.add_argument(
        "mw_dir",
        nargs="?",
        help="MW 目錄路徑;留空則掃 ../document_download/MW*/。",
    )
    p.add_argument("--force", action="store_true",
                   help="目標 .md 已含 # suggested_action 也強制重跑。")
    p.add_argument("--no-sync", action="store_true",
                   help="跳過 collect_training.sync,只跑分類。")
    args = p.parse_args()

    do_sync = not args.no_sync

    if args.mw_dir:
        result = run_one(
            mw_dir=Path(args.mw_dir),
            force=args.force,
            do_sync=do_sync,
        )
        print(f"[classifier] {Path(args.mw_dir).name} → {result['status']}")
        return

    doc_download = _BASE_DIR.parent / "document_download"
    if not doc_download.is_dir():
        print(f"[ERROR] {doc_download} 不存在")
        sys.exit(1)
    mw_dirs = sorted(d for d in doc_download.iterdir()
                     if d.is_dir() and d.name.startswith("MW"))
    if not mw_dirs:
        print(f"[INFO] {doc_download} 內沒有 MW* 子目錄")
        return

    if do_sync:
        from doc_classifier import collect_training
        stats = collect_training.sync()
        print(f"[sync] added={stats['added']} updated={stats['updated']} "
              f"orphan_kept={stats['orphan_kept']}")

    for d in mw_dirs:
        result = classify_dir(mw_dir=d, force=args.force)
        print(f"[classifier] {d.name} → {result['status']}")


if __name__ == "__main__":
    main()
