"""
browser_utils.py
Chrome 視窗操作共用工具 — 供各自動化腳本 import 使用。

相依套件：
    C:\\Python314\\python.exe -m pip install pillow pyautogui
"""

import ctypes
import ctypes.wintypes
import subprocess
import time

import pyautogui
from PIL import ImageGrab

pyautogui.FAILSAFE = False

# ── Win32 設定 ────────────────────────────────────────────────────────────────
user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(
    ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
)

SWP_NOMOVE     = 0x0002
SWP_NOSIZE     = 0x0001
HWND_TOPMOST   = -1
HWND_NOTOPMOST = -2

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


# ── 視窗列舉 ──────────────────────────────────────────────────────────────────

def get_all_chrome_windows():
    """回傳所有可見 Chrome 視窗的 hwnd 集合。"""
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
    在給定 hwnd 集合中，找標題含任一 title_keywords 的視窗。
    回傳 (hwnd, left, top, width, height)，找不到回傳 None。
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
    """最大化並置頂視窗，確保蓋過其他視窗。"""
    user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.5)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)


def get_window_rect(hwnd):
    """回傳視窗的 RECT（含 left, top, right, bottom）。"""
    r = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def grab_window(hwnd):
    """
    截取視窗畫面。
    回傳 (PIL Image, RECT)。
    """
    r = get_window_rect(hwnd)
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
    return img, r


def click_at(hwnd, offset_x, offset_y):
    """
    在視窗內的相對座標點擊（自動置頂確保不被遮擋）。
    offset_x, offset_y：相對於視窗左上角的偏移量（像素）。
    回傳實際點擊的螢幕座標 (screen_x, screen_y)。
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
    """在目前焦點的輸入框輸入文字。"""
    pyautogui.typewrite(text, interval=interval)


def press_key(key):
    """按下單一按鍵，例如 'enter'、'tab'、'escape'。"""
    pyautogui.press(key)


# ── 高階工具 ──────────────────────────────────────────────────────────────────

def launch_chrome_and_wait(profile, url, title_keywords, timeout=30):
    """
    以指定 profile 開新 Chrome 視窗，等待頁面載入後回傳視窗資訊。

    參數：
        profile        Chrome profile 目錄名稱，例如 "Profile 2"
        url            要開啟的網址
        title_keywords 頁面標題需含有的關鍵字列表
        timeout        最多等待秒數（預設 30）

    回傳 (hwnd, left, top, width, height)，超時則回傳 None。
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
    在 PIL 圖像中找出符合條件的像素座標。
    condition(r, g, b) -> bool

    回傳 [(x, y), ...]。
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
    """回傳一組座標點的中心 (cx, cy)，清單為空時回傳 None。"""
    if not pts:
        return None
    cx = int(sum(p[0] for p in pts) / len(pts))
    cy = int(sum(p[1] for p in pts) / len(pts))
    return cx, cy


def find_rightmost_cluster(img_strip, condition, min_pixels=15, gap=40):
    """
    找出圖像中最右側的顏色像素群集中心。

    參數：
        img_strip   PIL Image
        condition   (r, g, b) -> bool，像素篩選條件
        min_pixels  群集最少像素數（過濾雜訊，預設 15）
        gap         超過此 x 距離視為不同群集（預設 40px）

    回傳 (cx, cy) 相對於 img_strip 左上角，找不到回傳 None。
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
