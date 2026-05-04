import pyaudio
import numpy as np
import time
import whisper

# 1. 參數設定
CHUNK = 1024               # 每次讀取的音訊片段大小
FORMAT = pyaudio.paInt16   # 16-bit 格式
CHANNELS = 1               # 單聲道
RATE = 16000               # Whisper 最佳取樣率 16000Hz
SILENCE_THRESHOLD = 800    # 音量閥值 (需要根據你的麥克風靈敏度調整！)
SILENCE_DURATION = 0.8     # 安靜多久算講完 (秒)

# 2. 載入模型 (這裡先維持用 CPU 測試)
print("載入模型中...")
model = whisper.load_model("base").to("cuda")
print("載入完成！")

def listen_and_transcribe():
    p = pyaudio.PyAudio()
    
    # 開啟麥克風串流
    stream = p.open(format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK)
    
    print("🎤 麥克風已開啟，請開始說話... (按 Ctrl+C 結束)")
    
    audio_buffer = []
    is_recording = False
    silence_start = None
    
    try:
        while True:
            # 讀取一小段聲音
            data = stream.read(CHUNK, exception_on_overflow=False)
            audio_data = np.frombuffer(data, dtype=np.int16)
            
            # 計算這段聲音的音量 (絕對值的平均)
            volume = np.abs(audio_data).mean()
            
            if volume > SILENCE_THRESHOLD:
                # 聲音夠大，開始/繼續錄音
                if not is_recording:
                    print("\n[偵測到聲音，開始錄音...]")
                    is_recording = True
                audio_buffer.append(audio_data)
                silence_start = None # 重置安靜計時器
                
            elif is_recording:
                # 聲音變小了，但還在錄音狀態
                audio_buffer.append(audio_data)
                
                if silence_start is None:
                    silence_start = time.time()
                elif time.time() - silence_start > SILENCE_DURATION:
                    # 已經安靜超過 0.8 秒，判定這句話講完了！
                    print("[安靜，斷句！開始辨識...]")
                    
                    # 把剛才錄到的碎片拼成一段完整的聲音給 Whisper
                    final_audio = np.concatenate(audio_buffer).astype(np.float32) / 32768.0
                    
                    # 丟給 Whisper 辨識
                    segments, info = model.transcribe(
                        final_audio, 
                        beam_size=5, 
                        language="zh",
                        initial_prompt="這是一段繁體中文的測試錄音，可能會夾雜英文。例如：Mic test, 測試, 喂喂喂。"
                    )
                    
                    text = "".join([s.text for s in segments])
                    print(f"🤖 辨識結果: {text}")
                    
                    # 清空緩衝區，準備聽下一句話
                    audio_buffer = []
                    is_recording = False
                    silence_start = None
                    print("\n🎤 繼續監聽...")
                    
    except KeyboardInterrupt:
        print("\n程式結束。")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

if __name__ == "__main__":
    listen_and_transcribe()