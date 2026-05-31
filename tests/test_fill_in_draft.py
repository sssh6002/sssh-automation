import textwrap

import fill_in_draft


def _write_summary(extract_dir, filename, content):
    p = extract_dir / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_read_marks_parses_second_line(tmp_path):
    _write_summary(tmp_path, "123_456總結.gemini-2.5.md", """\
        #存查分類: 資安
        ## 不參加 研習
        1. 內容
        """)
    assert fill_in_draft._read_marks(tmp_path) == ["不參加", "研習"]


def test_read_marks_no_summary_file_returns_empty(tmp_path):
    assert fill_in_draft._read_marks(tmp_path) == []


def test_read_marks_no_mark_line_returns_empty(tmp_path):
    _write_summary(tmp_path, "123_456總結.gemini.md", """\
        #存查分類: 資安
        1. 只有分類沒有標記行
        """)
    assert fill_in_draft._read_marks(tmp_path) == []


def test_read_marks_single_mark(tmp_path):
    _write_summary(tmp_path, "9_9總結.claude.md", """\
        #存查分類: 設備
        ## 汰換
        """)
    assert fill_in_draft._read_marks(tmp_path) == ["汰換"]
