"""
taipeion_login.py
臺北市單一帳號認證平台（TAIPEION）自然人憑證登入模組。

直接執行：
    C:\\Python314\\python.exe taipeion_login.py

從其他腳本呼叫：
    from taipeion_login import login_taipeion
    hwnd = login_taipeion()   # 回傳視窗 hwnd，供後續操作使用
"""

import time

from browser_utils import (
    launch_chrome_and_wait,
    maximize_and_focus,
    grab_window,
    click_at,
    find_color_pixels,
    center_of,
)

CHROME_PROFILE = "Profile 2"
URL            = "https://login.gov.taipei/login.php"
TITLE_KEYWORDS = ["單一帳號", "臺北", "登入"]


# ── 內部工具 ──────────────────────────────────────────────────────────────────

def _is_teal(rv, gv, bv):
    """判斷像素是否為 TAIPEION 的青綠色（分頁底線與登入按鈕共用色）。"""
    return gv > 150 and bv > 150 and rv < 80 and abs(gv - bv) < 60


def _cluster_by_x(pts, gap=15, min_pixels=5):
    """將像素點按 x 方向分群，回傳所有群集的清單。"""
    if not pts:
        return []
    pts.sort(key=lambda p: p[0])
    clusters, current = [], [pts[0]]
    for p in pts[1:]:
        if p[0] - current[-1][0] <= gap:
            current.append(p)
        else:
            if len(current) >= min_pixels:
                clusters.append(current)
            current = [p]
    if len(current) >= min_pixels:
        clusters.append(current)
    return clusters


def _find_cert_tab_offset(hwnd):
    """
    找「自然人憑證」分頁的點擊座標。
    優先偵測青綠底線；找不到時使用比例座標備用。
    """
    img, r = grab_window(hwnd)
    ww, wh = r.right - r.left, r.bottom - r.top
    y0, y1 = int(wh * 0.48), int(wh * 0.56)
    strip = img.crop((0, y0, ww, y1))

    clusters = _cluster_by_x(find_color_pixels(strip, _is_teal), gap=10, min_pixels=3)
    print(f"      分頁底線青綠群集數：{len(clusters)}")

    if clusters:
        clusters.sort(key=lambda c: max(p[0] for p in c))
        target = clusters[-2] if len(clusters) >= 2 else clusters[-1]
        c = center_of(target)
        return c[0], y0 + c[1] - 12

    print("      [備用] 使用比例座標定位分頁")
    return int(ww * 0.576), int(wh * 0.508)


def _find_login_button_offset(hwnd):
    """找「登入」按鈕的點擊座標（頁面中最大的青綠色區塊）。"""
    img, r = grab_window(hwnd)
    ww, wh = r.right - r.left, r.bottom - r.top
    y0, y1 = int(wh * 0.60), int(wh * 0.82)
    strip = img.crop((0, y0, ww, y1))

    clusters = _cluster_by_x(find_color_pixels(strip, _is_teal), gap=20, min_pixels=20)
    print(f"      登入按鈕青綠群集數：{len(clusters)}")

    if not clusters:
        return None
    c = center_of(max(clusters, key=len))
    return c[0], y0 + c[1]


# ── 主要可呼叫函式 ────────────────────────────────────────────────────────────

def login_taipeion(save_screenshots=False):
    """
    開啟臺北市單一帳號認證平台，點選「自然人憑證」分頁並點選「登入」。

    參數：
        save_screenshots  True 時儲存各步驟截圖（預設 False）

    回傳：
        hwnd  成功時回傳 Chrome 視窗 handle，供後續操作使用。
        None  若無法開啟視窗或找不到登入按鈕。
    """
    print("[1/3] 開啟臺北市單一帳號認證平台（Profile 2）...")
    win = launch_chrome_and_wait(CHROME_PROFILE, URL, TITLE_KEYWORDS)
    if not win:
        print("[ERROR] 無法找到登入頁面視窗。")
        return None
    hwnd = win[0]
    print(f"      HWND={hwnd}，size={win[3]}x{win[4]}")

    time.sleep(2)
    maximize_and_focus(hwnd)
    time.sleep(1)

    if save_screenshots:
        img, _ = grab_window(hwnd)
        img.save("taipeion_step1.png")
        print("      截圖 → taipeion_step1.png")

    print("[2/3] 點選「自然人憑證」分頁...")
    tab = _find_cert_tab_offset(hwnd)
    sx, sy = click_at(hwnd, tab[0], tab[1])
    print(f"      點擊分頁：螢幕座標 ({sx}, {sy})")
    time.sleep(1.5)

    if save_screenshots:
        img, _ = grab_window(hwnd)
        img.save("taipeion_step2.png")
        print("      截圖 → taipeion_step2.png")

    print("[3/3] 點選「登入」按鈕...")
    btn = _find_login_button_offset(hwnd)
    if not btn:
        print("[WARN] 找不到登入按鈕（請確認自然人憑證卡片已插入讀卡機）")
        return None
    sx, sy = click_at(hwnd, btn[0], btn[1])
    print(f"      點擊登入：螢幕座標 ({sx}, {sy})")

    time.sleep(2.5)
    print("[完成] 登入動作執行完畢，視窗 HWND={hwnd}")
    return hwnd


# ── 直接執行時的進入點 ────────────────────────────────────────────────────────

if __name__ == "__main__":
    hwnd = login_taipeion(save_screenshots=True)
    if hwnd:
        img, _ = grab_window(hwnd)
        img.save("taipeion_result.png")
        print("結果截圖 → taipeion_result.png")
