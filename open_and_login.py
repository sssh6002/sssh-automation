"""
open_and_login.py
以 Chrome Profile 2 (1504@sssh.tp.edu.tw) 開啟松山高中網站並點選登入。

執行方式：
    C:\\Python314\\python.exe open_and_login.py
"""

import time

from browser_utils import (
    launch_chrome_and_wait,
    maximize_and_focus,
    grab_window,
    click_at,
    find_rightmost_cluster,
)

CHROME_PROFILE = "Profile 2"                  # 1504@sssh.tp.edu.tw
URL            = "https://www.sssh.tp.edu.tw"
TITLE_KEYWORDS = ["sssh", "松山", "首頁"]


def find_login_offset(hwnd):
    """
    分析 nav bar 中最右側的綠色群集（即「登入」按鈕），
    回傳相對視窗左上角的偏移 (offset_x, offset_y)。
    """
    img, r = grab_window(hwnd)
    ww = r.right - r.left
    wh = r.bottom - r.top

    # Chrome UI 約 90px；網站 nav bar 在 y=90~210
    nav_y0 = 90
    nav_y1 = min(210, int(wh * 0.25))
    strip = img.crop((0, nav_y0, ww, nav_y1))

    def is_green(rv, gv, bv):
        return gv > rv + 30 and gv > bv + 30 and 50 < gv < 200

    c = find_rightmost_cluster(strip, is_green)
    if c:
        print(f"      找到最右側綠色群集：strip 座標 ({c[0]}, {c[1]})")
        return c[0], nav_y0 + c[1]

    print("[WARN] 找不到綠色群集，使用固定備用座標")
    return ww - 200, 142


def main():
    print("[1/3] 開啟學校網站（Profile 2）...")
    win = launch_chrome_and_wait(CHROME_PROFILE, URL, TITLE_KEYWORDS)
    if not win:
        print("[ERROR] 無法找到學校網站視窗。")
        return
    hwnd = win[0]
    print(f"      HWND={hwnd}，size={win[3]}x{win[4]}")

    # 等頁面完全渲染
    time.sleep(2)

    print("[2/3] 最大化並點選登入...")
    maximize_and_focus(hwnd)
    ox, oy = find_login_offset(hwnd)
    sx, sy = click_at(hwnd, ox, oy)
    print(f"      點擊螢幕座標：({sx}, {sy})")

    time.sleep(2.5)

    print("[3/3] 儲存結果截圖...")
    result_img, _ = grab_window(hwnd)
    result_img.save("result.png")
    print("[完成] result.png 已儲存")


if __name__ == "__main__":
    main()
