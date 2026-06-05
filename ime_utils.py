"""ime_utils.py
強制把輸入法切回英文(美式鍵盤),供所有 GUI/鍵盤自動化腳本共用。

為何需要:本機只裝了 zh-Hant-TW(注音 TSF IME)一種輸入法。若執行自動化時 IME 停在
中文模式,透過 SendInput(pending_doc_handler 打 JFileChooser 路徑)或 pyautogui
(browser_utils.type_text)模擬的按鍵會被 IME 攔截組字,導致路徑被打成中文/亂碼。
實測 2026-05-20:「匯出公文資料」對話框路徑被填錯。

做法:
  1. 載入並啟用美式鍵盤佈局(KLID 00000409) → 該佈局沒有 IME,按鍵直接是字面 ASCII。
  2. 對目標視窗送 WM_INPUTLANGCHANGEREQUEST,把它的輸入語言切到剛載入的英文佈局。
     (Windows 預設每個視窗各自記輸入法,故必須針對「正要打字的那個視窗」切,
      JFileChooser 那種他行程的視窗才吃得到。)
  3. 雙保險:找該視窗的 IME 視窗,送 IMC_SETOPENSTATUS=0 關閉組字(切英數模式)。

此模組自包含(自帶 user32/imm32 handle),不依賴專案其他模組,避免耦合。
"""
import ctypes

# ── Win32 常數 ────────────────────────────────────────────────────────────────
_ENGLISH_KLID = "00000409"          # English (United States)
_KLF_ACTIVATE = 0x00000001          # LoadKeyboardLayout:載入後立即啟用
_WM_INPUTLANGCHANGEREQUEST = 0x0050  # 要求視窗切換輸入語言
_WM_IME_CONTROL = 0x0283            # 操作 IME
_IMC_SETOPENSTATUS = 0x0006         # WM_IME_CONTROL 子命令:設定開/關(0=關=英數)

# ── Win32 handle 與型別宣告(64-bit 安全:HKL/HWND 是指標寬度) ──────────────────
_user32 = ctypes.windll.user32
_imm32 = ctypes.windll.imm32

_user32.GetForegroundWindow.restype = ctypes.c_void_p
_user32.LoadKeyboardLayoutW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
_user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
_user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
_user32.PostMessageW.restype = ctypes.c_int
_user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
_user32.SendMessageW.restype = ctypes.c_void_p
_imm32.ImmGetDefaultIMEWnd.argtypes = [ctypes.c_void_p]
_imm32.ImmGetDefaultIMEWnd.restype = ctypes.c_void_p


def ensure_english_ime(hwnd=None):
    """把指定視窗(預設為目前前景視窗)的輸入法強制切成英文(美式鍵盤)。

    在任何 SendInput / pyautogui 打字「之前」呼叫,確保打出的是字面 ASCII,
    不被中文 IME 攔截組字。

    參數:
        hwnd  目標視窗 handle;None 時用 GetForegroundWindow()。

    回傳:
        bool  成功 True,失敗 False。失敗一律靜默(只印 WARN),絕不丟例外中斷主流程。
    """
    try:
        if hwnd is None:
            hwnd = _user32.GetForegroundWindow()

        hkl = _user32.LoadKeyboardLayoutW(_ENGLISH_KLID, _KLF_ACTIVATE)
        _user32.PostMessageW(hwnd, _WM_INPUTLANGCHANGEREQUEST, 0, hkl)

        ime_hwnd = _imm32.ImmGetDefaultIMEWnd(hwnd)
        if ime_hwnd:
            _user32.SendMessageW(ime_hwnd, _WM_IME_CONTROL, _IMC_SETOPENSTATUS, 0)
        return True
    except Exception as e:
        print(f"      [WARN] 切換英文輸入法失敗:{type(e).__name__}: {e}")
        return False
