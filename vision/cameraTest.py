import cv2
import os
from datetime import datetime

from latest_frame_camera import LatestFrameCamera

# USB Camera 通常是 0
CAMERA_ID = 0

# 照片直接存到目前資料夾
# 你現在如果在 ~/MakeNTU/vision 執行，照片就會存在 vision 裡
SAVE_DIR = "."

camera = LatestFrameCamera(CAMERA_ID, width=640, height=480)

try:
    camera.start()
except RuntimeError as e:
    print(e)
    exit()

print("相機已開啟")
print("已啟用低延遲模式：背景會持續讀取最新畫面")
print("輸入 1 後按 Enter：拍照")
print("輸入 q 後按 Enter：離開")

try:
    while True:
        cmd = input("請輸入指令：").strip()

        if cmd == "q":
            print("離開程式")
            break

        elif cmd == "1":
            ret, frame, frame_age = camera.read_latest()

            if not ret:
                print("讀不到影像，拍照失敗")
                continue

            filename = datetime.now().strftime("photo_%Y%m%d_%H%M%S.jpg")
            filepath = os.path.join(SAVE_DIR, filename)

            ok = cv2.imwrite(filepath, frame)

            if ok:
                latency_ms = frame_age * 1000
                print(f"已拍照並儲存：{filepath}（畫面約 {latency_ms:.0f} ms 前）")
            else:
                print("照片儲存失敗")

        else:
            print("未知指令，請輸入 1 拍照，或 q 離開")

finally:
    camera.release()
    print("相機已關閉")
