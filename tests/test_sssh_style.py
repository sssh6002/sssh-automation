# -*- coding: utf-8 -*-
"""松高風格表格卡片 renderer 的規格測試（對應資媒組長 2026-07-27 的模板提示詞）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from document_closure.sssh_style import THEME, render_fragment  # noqa: E402


def test_theme_color_and_gradient_present():
    out = render_fragment("標題", "1. 內容")
    assert THEME == "#0a5c44"
    assert THEME in out
    assert "linear-gradient" in out


def test_no_style_block_and_no_class_attribute():
    """規格 14:只能 inline style,不得有 <style> 區塊或 class。"""
    out = render_fragment("標題", "1. 甲\n2. 乙")
    assert "<style" not in out
    assert "class=" not in out


def test_heading_starts_new_card():
    out = render_fragment("標題", "【第一段】\n1. 甲\n【第二段】\n1. 乙")
    assert out.count("border-radius:14px") == 2


def test_linked_list_becomes_table():
    """規格 6:條列式且有超連結 → 改成表格。"""
    body = ("1. 中學生網站：https://www.shs.edu.tw\n"
            "2. 圖書館：https://example.edu.tw/lib")
    out = render_fragment("標題", body)
    assert "<table" in out
    assert "<th" in out


def test_plain_list_stays_a_list():
    """規格 6 的另一半:沒有超連結就維持原樣式,不要硬塞表格。"""
    out = render_fragment("標題", "1. 甲\n2. 乙\n3. 丙")
    assert "<table" not in out
    assert "<ul" in out


def test_links_open_new_window_and_are_announced():
    """規格 13:另開新視窗。用 title + 視覺隱藏文字,不用 <a> 不合法的 alt。"""
    out = render_fragment("標題", "1. 連結 https://example.org")
    assert 'target="_blank"' in out
    assert 'rel="noopener noreferrer"' in out
    assert 'title="另開新視窗"' in out
    assert "（另開新視窗）" in out
    assert " alt=" not in out


def test_sssh_link_to_hash_is_opt_in():
    """規格 12:預設保留原連結,開了才換成 #。"""
    body = "1. 校網 https://www.sssh.tp.edu.tw/library"
    assert "https://www.sssh.tp.edu.tw/library" in render_fragment("標題", body)
    assert 'href="#"' in render_fragment("標題", body, sssh_to_hash=True)


def test_text_is_not_modified():
    """規格 10:文字完全不修改 — 原字一個不少。"""
    body = "1. 請務必於中午12時前完成投稿（逾時不受理）"
    out = render_fragment("標題", body)
    assert "請務必於中午12時前完成投稿（逾時不受理）" in out


def test_html_chars_escaped():
    out = render_fragment("標<題", "1. a & b")
    assert "標&lt;題" in out
    assert "a &amp; b" in out


def test_include_title_toggle():
    assert "<h2" in render_fragment("我的標題", "1. 甲")
    assert "<h2" not in render_fragment("我的標題", "1. 甲", include_title=False)


def test_markdown_link_supported():
    out = render_fragment("標題", "1. [中學生網站](https://www.shs.edu.tw)")
    assert ">中學生網站<" in out
    assert 'href="https://www.shs.edu.tw"' in out


def test_empty_body_does_not_crash():
    out = render_fragment("只有標題", "")
    assert out.startswith("<section")
