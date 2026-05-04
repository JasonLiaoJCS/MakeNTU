import cv2
import os
from datetime import datetime
from google import genai
from PIL import Image

from latest_frame_camera import LatestFrameCamera

# === 設定 ===
CAMERA_ID = "auto"
SAVE_DIR = "."
MODEL_NAME = "gemini-1.5-flash"

# === 初始化 Gemini ===
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("❌ 找不到 GEMINI_API_KEY")
    exit()

client = genai.Client(api_key=api_key)

# === 開相機 ===
camera = LatestFrameCamera(CAMERA_ID, width=640, height=480)

try:
    camera.start()
except RuntimeError as e:
    print(f"❌ {e}")
    exit()

print("✅ 相機已開啟")
print(f"使用相機 ID：{camera.active_camera_id}")
print("已啟用低延遲模式：背景會持續讀取最新畫面")
print("輸入 1 拍照 + 分析")
print("輸入 q 離開")

try:
    while True:
        cmd = input("指令：").strip()

        if cmd == "q":
            break

        elif cmd == "1":
            ret, frame, frame_age = camera.read_latest()

            if not ret:
                print("❌ 讀不到影像")
                continue

            # 存圖
            filename = datetime.now().strftime("photo_%Y%m%d_%H%M%S.jpg")
            filepath = os.path.join(SAVE_DIR, filename)
            cv2.imwrite(filepath, frame)

            latency_ms = frame_age * 1000
            print(f"📸 已拍照：{filepath}（畫面約 {latency_ms:.0f} ms 前）")

            # === 丟給 Gemini ===
            try:
                img = Image.open(filepath)

                response = client.models.generate_content(
                    model=MODEL_NAME,
                    contents=[
                        "請描述這張照片中的人的表情（例如：開心、驚訝、疲倦），用簡短中文回答",
                        img
                    ]
                )

                print("🤖 Gemini 判斷：")
                print(response.text)

            except Exception as e:
                print("❌ Gemini 分析失敗：", e)

        else:
            print("請輸入 1 或 q")

finally:
    camera.release()
    print("已關閉")
