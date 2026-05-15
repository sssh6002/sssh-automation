"""找出學校網站 nav bar 中所有綠色群集位置，輸出到 login_pos.txt。"""
from PIL import Image
import time
from browser_utils import (
    launch_chrome_and_wait, maximize_and_focus, grab_window
)

CHROME_PROFILE = "Profile 2"
URL            = "https://www.sssh.tp.edu.tw"
TITLE_KEYWORDS = ["sssh", "松山", "首頁"]

print("開啟學校網站...")
win = launch_chrome_and_wait(CHROME_PROFILE, URL, TITLE_KEYWORDS)
if not win:
    print("找不到學校視窗（超時）"); exit()

hwnd = win[0]
print(f"視窗 HWND={hwnd}，size={win[3]}x{win[4]}")
time.sleep(2)

print("最大化並截圖...")
maximize_and_focus(hwnd)
img, r = grab_window(hwnd)
ww = r.right - r.left
wh = r.bottom - r.top

nav_y0, nav_y1 = 90, min(210, int(wh * 0.25))
strip = img.crop((0, nav_y0, ww, nav_y1))
strip.save("nav_strip_now.png")
strip.resize((strip.width * 2, strip.height * 2), Image.NEAREST).save("nav_strip_now_2x.png")

# 分析綠色群集（每 20px 一個 bucket）
px = strip.load()
nw, nh = strip.size
buckets = {}
for y in range(nh):
    for x in range(nw):
        rv, gv, bv = px[x, y][:3]
        if gv > rv + 30 and gv > bv + 30 and 50 < gv < 200:
            b = x // 20
            buckets[b] = buckets.get(b, 0) + 1

lines = [
    f"視窗大小: {ww}x{wh}（最大化後）",
    f"Nav strip: y={nav_y0}~{nav_y1}，寬={nw}px",
    "綠色群集（strip_x = 視窗相對 x）：",
]
for b in sorted(buckets.keys()):
    x0 = b * 20
    lines.append(f"  strip_x={x0:5d}~{x0+19}  像素數={buckets[b]}")

output = "\n".join(lines)
with open("login_pos.txt", "w", encoding="utf-8") as f:
    f.write(output)

print(output)
print("\n已儲存 nav_strip_now.png 和 login_pos.txt")
