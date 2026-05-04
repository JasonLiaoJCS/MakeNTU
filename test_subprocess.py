import sounddevice as sd
import numpy as np

def callback(indata, frames, time, status):
    # 計算音量大小
    volume = int(np.abs(indata).mean() * 10000)
    print(f"目前音量: {volume:5d}", end='\r')

print("🎤 測試系統預設麥克風...")
print("請對著麥克風講話、吹氣或拍手 (Ctrl+C 結束)")
print("⚠️ 警告：請至少讓它跑 5 秒鐘再關掉！")

try:
    # 這裡故意不放 device=...，讓它抓系統預設！
    with sd.InputStream(channels=1, samplerate=16000, callback=callback):
        sd.sleep(100000) # 讓程式活著
except KeyboardInterrupt:
    print("\n測試結束")