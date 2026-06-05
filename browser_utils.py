"""
browser_utils.py
Chrome 視窗操作共用工具庫。

提供以下四類功能，供自動化腳本 import 使用：
  1. 視窗列舉  — 找出目前開啟的 Chrome 視窗（hwnd）
  2. 視窗操作  — 最大化、置頂、取得座標、截圖
  3. 滑鼠鍵盤  — 在視窗內點擊指定位置、輸入文字、按鍵
  4. 高階工具  — 開啟新 Chrome 視窗並等待頁面載入、像素顏色搜尋

相依套件（需先安裝）：
    C:\\Python314\\python.exe -m pip install pillow pyautogui

注意：所有座標均為「螢幕絕對座標」（DPI 縮放 100% 假設），
      若 Windows 顯示縮放非 100%，截圖與點擊位置可能偏移。
"""

import ctypes
import ctypes.wintypes
import subprocess
import time

import pyautogui
from PIL import ImageGrab

from ime_utils import ensure_english_ime

pyautogui.FAILSAFE = False  # 關閉「滑鼠移到角落中止」安全機制，避免自動化中途被打斷

# ── Win32 常數與初始化 ────────────────────────────────────────────────────────
user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(
    ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
)

SWP_NOMOVE     = 0x0002   # SetWindowPos：不移動視窗位置
SWP_NOSIZE     = 0x0001   # SetWindowPos：不改變視窗大小
HWND_TOPMOST   = -1       # 設為永遠置頂
HWND_NOTOPMOST = -2       # 取消永遠置頂（恢復正常層級）

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


# ── 視窗列舉 ──────────────────────────────────────────────────────────────────

def get_all_chrome_windows():
    """
    列舉目前所有可見的 Chrome 視窗。

    回傳：
        set[int]  包含所有 Chrome 視窗 hwnd 的集合；無視窗時回傳空集合。
    """
    hwnds = set()

    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if "Chrome" in buf.value:
                hwnds.add(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(callback), None)
    return hwnds


def find_window_from(candidates, title_keywords):
    """
    在指定的 hwnd 集合中，找出標題含有任一關鍵字的視窗。

    參數：
        candidates      要搜尋的 hwnd 集合（通常來自 get_all_chrome_windows）
        title_keywords  標題關鍵字列表，只要符合其中一個即算找到

    回傳：
        (hwnd, left, top, width, height)  視窗資訊；找不到回傳 None。
    """
    for hwnd in candidates:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        if any(k in title for k in title_keywords):
            r = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            return (hwnd, r.left, r.top, r.right - r.left, r.bottom - r.top)
    return None


# ── 視窗操作 ──────────────────────────────────────────────────────────────────

def maximize_and_focus(hwnd):
    """
    最大化視窗並確保它出現在最上層。

    流程：最大化 → 暫時置頂（確保蓋過其他視窗）→ 設為前景 → 取消置頂（恢復正常層級）。
    中間等待 1.5 秒讓視窗完成動畫再繼續操作。
    """
    user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.5)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)


def get_window_rect(hwnd):
    """
    取得視窗在螢幕上的邊界矩形。

    回傳：
        ctypes.wintypes.RECT  含 left、top、right、bottom 四個屬性（螢幕像素座標）。
    """
    r = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def grab_window(hwnd):
    """
    對指定視窗截圖。

    回傳：
        (PIL.Image, RECT)  截圖影像與視窗邊界；可用 img.save() 存檔或直接分析像素。
    """
    r = get_window_rect(hwnd)
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
    return img, r


def click_at(hwnd, offset_x, offset_y):
    """
    在視窗內的相對座標點擊滑鼠左鍵。

    點擊前會暫時將視窗置頂，確保點擊不被其他視窗遮擋；點擊後恢復正常層級。

    參數：
        hwnd      目標視窗的 handle
        offset_x  相對於視窗左上角的水平偏移（像素）
        offset_y  相對於視窗左上角的垂直偏移（像素）

    回傳：
        (screen_x, screen_y)  實際點擊的螢幕絕對座標。
    """
    r = get_window_rect(hwnd)
    screen_x = r.left + offset_x
    screen_y = r.top + offset_y
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    time.sleep(0.3)
    pyautogui.moveTo(screen_x, screen_y, duration=0.4)
    time.sleep(0.2)
    pyautogui.click(screen_x, screen_y)
    time.sleep(0.5)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    return screen_x, screen_y


def type_text(text, interval=0.05):
    """
    在目前焦點的輸入框逐字輸入文字。

    參數：
        text      要輸入的字串（僅支援 ASCII；中文需改用剪貼簿方式）
        interval  每個字元之間的間隔秒數（預設 0.05 秒）
    """
    ensure_english_ime()  # 先把輸入法切回英文，避免按鍵被中文 IME 攔截組字
    pyautogui.typewrite(text, interval=interval)


def press_key(key):
    """
    按下單一鍵盤按鍵。

    參數：
        key  pyautogui 鍵名字串，例如 'enter'、'tab'、'escape'、'f5'。
    """
    pyautogui.press(key)


# ── 高階工具 ──────────────────────────────────────────────────────────────────

def launch_chrome_and_wait(profile, url, title_keywords, timeout=30):
    """
    以指定 Chrome Profile 開啟新視窗，並等待目標頁面載入完成。

    作法：記錄啟動前已存在的 Chrome 視窗，啟動後持續輪詢新出現的視窗，
    直到找到標題符合 title_keywords 的視窗為止。

    參數：
        profile        Chrome Profile 目錄名稱，例如 "Profile 2"
        url            要開啟的網址
        title_keywords 頁面標題需含有的關鍵字列表（符合任一即視為載入成功）
        timeout        最多等待秒數（預設 30 秒）

    回傳：
        (hwnd, left, top, width, height)  目標視窗資訊；超時或失敗回傳 None。
    """
    existing = get_all_chrome_windows()
    subprocess.Popen([
        CHROME_EXE,
        f"--profile-directory={profile}",
        "--new-window",
        url,
    ])

    for i in range(timeout):
        time.sleep(1)
        current = get_all_chrome_windows()
        new_hwnds = current - existing
        if new_hwnds:
            result = find_window_from(new_hwnds, title_keywords)
            if result:
                return result
            if i % 5 == 0:
                print(f"      新視窗出現但頁面未載入... ({i + 1}s)")
        elif i % 5 == 0:
            print(f"      等待新視窗... ({i + 1}s)")

    return None


def find_color_pixels(img_strip, condition):
    """
    掃描 PIL 圖像，找出所有符合顏色條件的像素座標。

    參數：
        img_strip  要掃描的 PIL Image（通常是從 grab_window 截取的局部區域）
        condition  callable(r, g, b) -> bool，判斷像素是否符合條件

    回傳：
        list[(x, y)]  所有符合條件的像素座標清單；無符合時回傳空清單。
    """
    pixels = img_strip.load()
    nw, nh = img_strip.size
    pts = []
    for y in range(nh):
        for x in range(nw):
            rv, gv, bv = pixels[x, y][:3]
            if condition(rv, gv, bv):
                pts.append((x, y))
    return pts


def center_of(pts):
    """
    計算一組像素座標的幾何中心。

    參數：
        pts  [(x, y), ...] 座標清單

    回傳：
        (cx, cy)  整數中心座標；pts 為空時回傳 None。
    """
    if not pts:
        return None
    cx = int(sum(p[0] for p in pts) / len(pts))
    cy = int(sum(p[1] for p in pts) / len(pts))
    return cx, cy


def find_rightmost_cluster(img_strip, condition, min_pixels=15, gap=40):
    """
    在圖像中找出最右側的顏色像素群集，並回傳其中心座標。

    用途：當畫面中有多個同色區塊時，取最右邊那個（例如定位最右側的按鈕）。

    參數：
        img_strip   要掃描的 PIL Image
        condition   (r, g, b) -> bool，像素篩選條件
        min_pixels  群集最少需包含的像素數，低於此值視為雜訊忽略（預設 15）
        gap         水平距離超過此值（像素）的像素視為不同群集（預設 40）

    回傳：
        (cx, cy)  最右側群集的中心座標（相對於 img_strip 左上角）；找不到回傳 None。
    """
    pts = find_color_pixels(img_strip, condition)
    if not pts:
        return None

    pts.sort(key=lambda p: p[0])

    clusters = []
    current = [pts[0]]
    for p in pts[1:]:
        if p[0] - current[-1][0] <= gap:
            current.append(p)
        else:
            if len(current) >= min_pixels:
                clusters.append(current)
            current = [p]
    if len(current) >= min_pixels:
        clusters.append(current)

    if not clusters:
        return None

    rightmost = max(clusters, key=lambda c: max(p[0] for p in c))
    return center_of(rightmost)
