import base64
import io
import os
from datetime import datetime
from pathlib import Path

import cv2
import requests
from PIL import Image

from latest_frame_camera import LatestFrameCamera

OLLAMA_URL = "http://100.108.141.26:11434/api/chat"
MODEL = "qwen35-fast:latest"

CAMERA_ID = "auto"
CAMERA_WIDTH = 320
CAMERA_HEIGHT = 240
IMAGE_MAX_SIDE = 320
JPEG_QUALITY = 65
SAVE_DIR = "/home/asrlab-yian/MakeNTU/vision"

PROMPT = (
    "請用繁體中文用 1 到 2 句話描述照片中人的表情與狀態。"
    "如果臉部太少、太模糊、角度不對或沒有拍到臉，請不要說看不到圖片；"
    "請回答「表情無法判斷」和簡短原因。"
)


def image_to_base64_jpeg(image_path, max_side=IMAGE_MAX_SIDE, quality=JPEG_QUALITY):
    img = Image.open(image_path).convert("RGB")

    w, h = img.size
    scale = min(max_side / max(w, h), 1.0)

    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)

    return base64.b64encode(buf.getvalue()).decode("utf-8")


def ask_ollama(image_path):
    img_b64 = image_to_base64_jpeg(image_path)

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": "你是視覺理解助理。只輸出最終答案。不要輸出推理過程，不要輸出 thinking。",
            },
            {
                "role": "user",
                "content": PROMPT,
                "images": [img_b64],
            },
        ],
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {
            "num_ctx": 2048,
            "num_predict": 96,
            "temperature": 0.1,
        },
    }

    r = requests.post(OLLAMA_URL, json=payload, timeout=600)
    r.raise_for_status()

    data = r.json()
    return data.get("message", {}).get("content", "").strip()


def main():
    Path(SAVE_DIR).mkdir(parents=True, exist_ok=True)

    camera = LatestFrameCamera(CAMERA_ID, width=CAMERA_WIDTH, height=CAMERA_HEIGHT)

    try:
        camera.start()
    except RuntimeError as e:
        print(e)
        return

    print("相機已開啟")
    print(f"使用相機 ID：{camera.active_camera_id}")
    print(f"已啟用快速模式：{CAMERA_WIDTH}x{CAMERA_HEIGHT}、低延遲讀取")
    print("輸入 1：拍照並分析狀態")
    print("輸入 q：離開")

    try:
        while True:
            cmd = input("指令：").strip().lower()

            if cmd == "q":
                break

            if cmd != "1":
                print("請輸入 1 或 q")
                continue

            ret, frame, frame_age = camera.read_latest()

            if not ret:
                print("讀不到影像")
                continue

            filename = datetime.now().strftime("photo_%Y%m%d_%H%M%S.jpg")
            image_path = os.path.join(SAVE_DIR, filename)

            ok = cv2.imwrite(image_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 75])

            if not ok:
                print("照片儲存失敗")
                continue

            latency_ms = frame_age * 1000
            print(f"已拍照：{image_path}（畫面約 {latency_ms:.0f} ms 前）")
            print("正在分析...")

            try:
                answer = ask_ollama(image_path)
                print("AI 判斷：")
                print(answer if answer else "看不到圖片")
            except Exception as e:
                print("分析失敗：", e)

    finally:
        camera.release()
        print("相機已關閉")


if __name__ == "__main__":
    main()
