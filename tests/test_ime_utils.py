"""ime_utils.ensure_english_ime 的單元測試。

這是 Win32 副作用程式碼,實機行為(打字不被中文 IME 攔截)以 Notepad 端到端驗證;
此處用 mock 把「該呼叫哪些 Win32 API、用什麼參數」的契約釘住,當作 regression guard。
"""
import unittest.mock as mock

import ime_utils


def _patch_win32(monkeypatch, *, foreground=0xABCD, hkl=0x4090409, ime_wnd=0x1111):
    """把 ime_utils 的 _user32 / _imm32 換成可斷言的 mock。"""
    u32 = mock.MagicMock(name="user32")
    imm = mock.MagicMock(name="imm32")
    u32.GetForegroundWindow.return_value = foreground
    u32.LoadKeyboardLayoutW.return_value = hkl
    imm.ImmGetDefaultIMEWnd.return_value = ime_wnd
    monkeypatch.setattr(ime_utils, "_user32", u32)
    monkeypatch.setattr(ime_utils, "_imm32", imm)
    return u32, imm


def test_loads_and_activates_english_us_layout(monkeypatch):
    u32, _ = _patch_win32(monkeypatch)

    ime_utils.ensure_english_ime(hwnd=0x9999)

    # 美式鍵盤 KLID 00000409 + KLF_ACTIVATE(0x1)
    u32.LoadKeyboardLayoutW.assert_called_once_with("00000409", 0x00000001)


def test_posts_input_lang_change_to_target_window(monkeypatch):
    u32, _ = _patch_win32(monkeypatch, hkl=0x4090409)

    ime_utils.ensure_english_ime(hwnd=0x9999)

    # WM_INPUTLANGCHANGEREQUEST = 0x0050,lParam = 載入的 HKL
    u32.PostMessageW.assert_called_once_with(0x9999, 0x0050, 0, 0x4090409)


def test_closes_ime_open_status_on_target_window(monkeypatch):
    u32, imm = _patch_win32(monkeypatch, ime_wnd=0x1111)

    ime_utils.ensure_english_ime(hwnd=0x9999)

    # 找該視窗的 IME 視窗,送 WM_IME_CONTROL(0x0283) + IMC_SETOPENSTATUS(0x0006) + 0(關閉=英數)
    imm.ImmGetDefaultIMEWnd.assert_called_once_with(0x9999)
    u32.SendMessageW.assert_called_once_with(0x1111, 0x0283, 0x0006, 0)


def test_defaults_to_foreground_window_when_hwnd_none(monkeypatch):
    u32, imm = _patch_win32(monkeypatch, foreground=0xABCD)

    ime_utils.ensure_english_ime()  # 不給 hwnd

    u32.GetForegroundWindow.assert_called_once_with()
    # 後續所有操作都針對前景視窗
    u32.PostMessageW.assert_called_once_with(0xABCD, 0x0050, 0, mock.ANY)
    imm.ImmGetDefaultIMEWnd.assert_called_once_with(0xABCD)


def test_returns_true_on_success(monkeypatch):
    _patch_win32(monkeypatch)
    assert ime_utils.ensure_english_ime(hwnd=0x9999) is True


def test_swallows_exceptions_and_returns_false(monkeypatch):
    u32, _ = _patch_win32(monkeypatch)
    u32.LoadKeyboardLayoutW.side_effect = OSError("boom")

    # 絕不能讓 IME 切換失敗中斷自動化主流程
    assert ime_utils.ensure_english_ime(hwnd=0x9999) is False
