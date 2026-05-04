import base64
import io
import os
from datetime import datetime
from pathlib import Path

import cv2
import requests
from PIL import Image

from latest_frame_camera import LatestFrameCamera


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://100.108.141.26:11434/api/chat")
MODEL = os.environ.get("OLLAMA_VISION_MODEL", "qwen35-fast:latest")

CAMERA_ID = os.environ.get("CAMERA_ID", "auto")
CAMERA_WIDTH = 320
CAMERA_HEIGHT = 240

IMAGE_MAX_SIDE = 480
JPEG_QUALITY = 75
SAVE_DIR = Path(__file__).resolve().parent / "object_photos"

PROMPT = (
    "請只辨識照片中「人手上正在拿著的東西」，不要列出背景物品、桌上物品或沒有被手拿著的東西。"
    "如果有多個手持物，請列出最明顯的 1 到 3 個。"
    "如果看不到手、手上沒有東西、畫面模糊、太暗或無法確定物體，請直接說明不確定。"
    "請用以下格式回答：\n"
    "手持物：列出手上拿的物體，或回答「不確定」\n"
    "評論：1 到 2 句自然評論"
)


def image_to_base64_jpeg(image_path, max_side=IMAGE_MAX_SIDE, quality=JPEG_QUALITY):
    img = Image.open(image_path).convert("RGB")

    width, height = img.size
    scale = min(max_side / max(width, height), 1.0)
    if scale < 1.0:
        img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

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
                "content": (
                    "你是視覺理解助理。只輸出最終答案，不要輸出推理過程，"
                    "不要輸出 thinking。"
                ),
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
            "num_predict": 160,
            "temperature": 0.2,
        },
    }

    response = requests.post(OLLAMA_URL, json=payload, timeout=600)
    response.raise_for_status()

    data = response.json()
    return data.get("message", {}).get("content", "").strip()


def capture_latest(camera):
    ret, frame, frame_age = camera.read_latest()
    if not ret:
        print("讀不到影像，拍照失敗")
        return None

    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    filename = datetime.now().strftime("object_%Y%m%d_%H%M%S.jpg")
    image_path = SAVE_DIR / filename

    ok = cv2.imwrite(str(image_path), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        print("照片儲存失敗")
        return None

    latency_ms = frame_age * 1000
    print(f"已拍照：{image_path}（畫面約 {latency_ms:.0f} ms 前）")
    return image_path


def main():
    camera = LatestFrameCamera(CAMERA_ID, width=CAMERA_WIDTH, height=CAMERA_HEIGHT)

    try:
        camera.start()
    except RuntimeError as exc:
        print(exc)
        return

    print("相機已開啟")
    print(f"使用相機 ID：{camera.active_camera_id}")
    print(f"已啟用低延遲模式：背景持續讀取最新畫面（{CAMERA_WIDTH}x{CAMERA_HEIGHT}）")
    print("輸入 1：拍照、辨識手上拿的東西並請 AI 評論")
    print("輸入 q：離開")

    try:
        while True:
            try:
                cmd = input("指令：").strip().lower()
            except EOFError:
                print("\n輸入已結束，準備關閉")
                break

            if cmd == "q":
                break

            if cmd != "1":
                print("請輸入 1 或 q")
                continue

            image_path = capture_latest(camera)
            if image_path is None:
                continue

            print("正在分析...")
            try:
                answer = ask_ollama(image_path)
            except requests.exceptions.RequestException as exc:
                print(f"AI 連線或請求失敗：{exc}")
                continue
            except ValueError as exc:
                print(f"AI 回傳格式解析失敗：{exc}")
                continue

            print("AI 分析：")
            print(answer if answer else "AI 沒有回傳內容")

    except KeyboardInterrupt:
        print("\n收到中斷，準備關閉")
    finally:
        camera.release()
        print("相機已關閉")


if __name__ == "__main__":
    main()
