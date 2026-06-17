"""_sweep_empty_pending_dirs:只刪「空的 MW* 殘留目錄」,不動其他。"""
import pending_doc_handler as p


def test_sweep_removes_only_empty_mw_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "_close_explorer_windows_in", lambda f: 0)  # 不碰 COM
    empty_mw = tmp_path / "MWAA111"; empty_mw.mkdir()
    full_mw = tmp_path / "MWAA222"; full_mw.mkdir()
    (full_mw / "a.pdf").write_text("x", encoding="utf-8")
    empty_other = tmp_path / "training_data"; empty_other.mkdir()
    (tmp_path / "MWAA333.zip").write_text("z", encoding="utf-8")  # 同名檔非目錄

    n = p._sweep_empty_pending_dirs(str(tmp_path))

    assert n == 1
    assert not empty_mw.exists()                 # 空 MW* → 刪
    assert full_mw.exists()                       # 非空 MW* → 留
    assert empty_other.exists()                   # 非 MW* → 留
    assert (tmp_path / "MWAA333.zip").exists()    # 檔案(非目錄)→ 不動


def test_sweep_missing_dir_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "_close_explorer_windows_in", lambda f: 0)
    assert p._sweep_empty_pending_dirs(str(tmp_path / "不存在")) == 0
