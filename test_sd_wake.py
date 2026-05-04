import os
import sys
import queue
import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly
from openwakeword.model import Model

# --- 參數設定 ---
MIC_KEYWORD    = os.getenv("MIC_DEVICE_KEYWORD", "UACDemo")
# 如果 48000 還是有問題，你可以試著把這個環境變數改成 16000 或 44100
HW_RATE        = int(os.getenv("HW_RATE", "48000")) 
CHUNK_HW       = int(HW_RATE * 0.033)
WAKE_THRESHOLD = 0.5

# --- 1. 尋找麥克風 ---
def find_mic_sd(keyword):
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if keyword.lower() in dev['name'].lower() and dev['max_input_channels'] > 0:
            return i, dev['name']
    
    # 如果找不到，印出所有設備清單幫你除錯
    print("\n目前的音訊設備清單：", file=sys.stderr)
    print(devices, file=sys.stderr)
    raise RuntimeError(f"找不到名稱包含 '{keyword}' 的麥克風，請檢查上方的設備清單")

# --- 2. 建立音訊緩衝佇列 ---
# 這個 Queue 是核心！麥克風不管三七二十一就是把聲音塞進來，不怕模型運算卡住
audio_queue = queue.Queue()

def audio_callback(indata, frames, time, status):
    """sounddevice 的背景回呼函式，只要有聲音就會自動觸發"""
    if status:
        print(f"音訊狀態警告: {status}", file=sys.stderr)
    # indata 已經是 numpy array 了，直接複製一份塞進佇列
    audio_queue.put(indata.copy())

# --- 3. 載入模型 (先做耗時工作) ---
print("1. 載入 openWakeWord 模型... (這需要幾秒鐘)", file=sys.stderr)
oww = Model(
    wakeword_models=["hey_jarvis"],
    inference_framework="onnx",
    vad_threshold=0.5,
)
print("-> oww 模型載入完成！\n", file=sys.stderr)

# --- 4. 初始化設備 ---
mic_idx, mic_name = find_mic_sd(MIC_KEYWORD)
print(f"2. 鎖定麥克風: [{mic_idx}] {mic_name}", file=sys.stderr)

print('=================================')
print('請對麥克風說 "Hey Jarvis"... (Ctrl+C 結束)')
print('=================================\n', file=sys.stderr)

# --- 5. 啟動非同步音訊流 ---
try:
    # sd.InputStream 會自動處理 ALSA 底層的 plughw 轉換，這點比 PyAudio 聰明太多
    with sd.InputStream(device=mic_idx, samplerate=HW_RATE, channels=1,
                        dtype='int16', blocksize=CHUNK_HW,
                        callback=audio_callback):
        while True:
            # 從佇列中拿出最新的音訊區塊
            raw_data = audio_queue.get()
            
            # sounddevice 給的是 2D array (frames, channels)，把它壓平變成 1D
            raw = raw_data.flatten()
            
            # 降採樣 48k -> 16k 給 oww 使用
            audio_16k = resample_poly(raw, 1, 3).astype(np.int16)

            # 丟給模型預測
            scores = oww.predict(audio_16k)
            score  = scores.get("hey_jarvis", 0.0)
            volume = int(np.abs(audio_16k).mean())

            # 顯示狀態
            if volume > 100:
                print(f"vol={volume:5d} | score={score:.4f}", file=sys.stderr)
            else:
                print(f"vol={volume:5d} | score={score:.2f} (靜音)", end='\r', file=sys.stderr)

            # 觸發判定
            if score > WAKE_THRESHOLD:
                print(f"\n\n🚀 觸發! Hey Jarvis (score={score:.2f})\n", file=sys.stderr)
                oww.reset()

except KeyboardInterrupt:
    print("\n結束程式", file=sys.stderr)
except Exception as e:
    print(f"\n崩潰啦: {e}", file=sys.stderr)