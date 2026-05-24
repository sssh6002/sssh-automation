"""classifier.py — doc_classifier 主入口。

設計同 summarize_doc.py:業務規格寫在 classifier.md,LLM runtime 讀;
Python 只負責 I/O。
"""
import re
import sys
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


def parse_response(raw: str) -> dict:
    """解析 LLM 回應。三種可能:
       {"status": "ok",   "action": ..., "confidence": ..., "examples": [...], "reasoning": ...}
       {"status": "skip", "reason": ...}
       {"status": "error", "raw": ...}
    """
    raw = (raw or "").strip()

    if raw.lstrip().startswith("<!-- SKIP") or raw.lstrip().startswith("SKIP"):
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
