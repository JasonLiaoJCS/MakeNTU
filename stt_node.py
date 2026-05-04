"""
stt_node.py — Gemini Flash STT ROS2 Node
喚醒詞：hey_jarvis (openWakeWord)
錄音觸發：Silero VAD（區分人聲與背景噪音）
辨識：Gemini Flash API

環境變數（~/.bashrc）：
  必填：
    GEMINI_API_KEY
  選填：
    MIC_DEVICE_KEYWORD      預設 "UACDemo"
    HW_RATE                 預設 48000
    VAD_THRESHOLD           預設 0.6
    VAD_ROLLING_N           預設 2
    SILENCE_DURATION        預設 1.2
    MIN_SPEECH_SECONDS      預設 0.4
    MAX_SPEECH_SECONDS      預設 15.0
    WAKE_THRESHOLD          預設 0.5
    VOLUME_MIN              預設 14500

依賴：
  pip install google-generativeai pyaudio numpy openwakeword torch
"""

import os
import sys
import io
import wave
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import pyaudio
import numpy as np
import time
import threading
import json
import torch
import google.generativeai as genai
from openwakeword.model import Model as WakeModel


def _env(key, default, cast=str):
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return cast(val)
    except ValueError:
        return default


def pcm_to_wav_bytes(audio_int16: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()


def linear_resample(audio_int16: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return audio_int16
    duration = len(audio_int16) / src_rate
    num_out  = int(duration * dst_rate)
    return np.interp(
        np.linspace(0, duration, num_out),
        np.linspace(0, duration, len(audio_int16)),
        audio_int16
    ).astype(np.int16)


class STTNode(Node):
    def __init__(self):
        super().__init__('stt_node')

        # 環境變數
        GEMINI_API_KEY        = os.environ.get("GEMINI_API_KEY", "")
        MIC_KEYWORD           = _env("MIC_DEVICE_KEYWORD",  "UACDemo")
        self.HW_RATE          = _env("HW_RATE",             48000, int)
        self.VAD_THRESHOLD    = _env("VAD_THRESHOLD",       0.6,   float)
        self.VAD_ROLLING_N    = _env("VAD_ROLLING_N",       2,     int)
        self.SILENCE_DURATION = _env("SILENCE_DURATION",    1.2,   float)
        self.MIN_SPEECH_SEC   = _env("MIN_SPEECH_SECONDS",  0.4,   float)
        self.MAX_SPEECH_SEC   = _env("MAX_SPEECH_SECONDS",  15.0,  float)
        self.WAKE_THRESHOLD   = _env("WAKE_THRESHOLD",      0.5,   float)
        self.VOLUME_MIN       = _env("VOLUME_MIN",          14500, int)

        if not GEMINI_API_KEY:
            self.get_logger().error("找不到 GEMINI_API_KEY")
            raise RuntimeError("GEMINI_API_KEY not set")

        # Gemini Flash
        genai.configure(api_key=GEMINI_API_KEY)
        self.gemini = genai.GenerativeModel("gemini-2.0-flash")
        self.get_logger().info("Gemini Flash 連線完成")

        # 固定參數
        self.TARGET_RATE = 16000
        self.CHUNK       = int(self.HW_RATE * 0.033)
        self.FORMAT      = pyaudio.paInt16
        self.CHANNELS    = 1

        # 找麥克風
        self.DEVICE_INDEX = self._find_device_index(MIC_KEYWORD)
        self.get_logger().info(f"麥克風: index={self.DEVICE_INDEX} (keyword='{MIC_KEYWORD}')")

        # 載入 Silero VAD
        self.get_logger().info("載入 Silero VAD...")
        self.vad_model, _ = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            trust_repo=True
        )
        self.vad_model = self.vad_model.to("cuda")
        self.get_logger().info("Silero VAD 載入完成")

        # 載入 openWakeWord（必須在 rclpy.init() 之後）
        self.get_logger().info("載入 openWakeWord (hey_jarvis)...")
        self.oww = WakeModel(
            wakeword_models=["hey_jarvis"],
            inference_framework="onnx",
            vad_threshold=0.0,
        )
        self.get_logger().info("openWakeWord 載入完成")

        # 發布者
        self.publisher_     = self.create_publisher(String, '/user_speech',      10)
        self.publisher_meta = self.create_publisher(String, '/user_speech_meta', 10)

        # 啟動監聽執行緒
        self.listen_thread = threading.Thread(target=self.listen_loop, daemon=True)
        self.listen_thread.start()
        self.get_logger().info('STT Node 上線，說 "Hey Jarvis" 喚醒')

    def _find_device_index(self, keyword: str) -> int:
        p = pyaudio.PyAudio()
        found = 0
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if keyword.lower() in info['name'].lower() \
                    and info['maxInputChannels'] > 0:
                found = i
                break
        p.terminate()
        return found

    def _vad_prob(self, audio_int16: np.ndarray) -> float:
        chunk = audio_int16[:512] if len(audio_int16) >= 512 else \
                np.pad(audio_int16, (0, 512 - len(audio_int16)))
        tensor = torch.from_numpy(
            chunk.astype(np.float32) / 32768.0
        ).to("cuda")
        with torch.no_grad():
            prob = self.vad_model(tensor, self.TARGET_RATE).item()
        return prob

    def _gemini_transcribe(self, audio_int16: np.ndarray) -> str:
        try:
            wav_bytes = pcm_to_wav_bytes(audio_int16, self.TARGET_RATE)
            response  = self.gemini.generate_content([
                {"mime_type": "audio/wav", "data": wav_bytes},
                "請將這段音訊轉錄成文字。語言是繁體中文，可能夾雜英文單字。只輸出轉錄的文字內容，不要加任何說明。"
            ])
            return response.text.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower():
                self.get_logger().warn("Gemini rate limit，等待 15 秒...")
                time.sleep(15)
            else:
                self.get_logger().error(f"Gemini API 錯誤: {e}")
            return ""

    def listen_loop(self):
        p = pyaudio.PyAudio()

        try:
            stream = p.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.HW_RATE,
                input=True,
                input_device_index=self.DEVICE_INDEX,
                frames_per_buffer=self.CHUNK
            )
        except Exception as e:
            self.get_logger().error(f"開啟麥克風失敗: {e}")
            return

        # 暖機
        for _ in range(5):
            stream.read(self.CHUNK, exception_on_overflow=False)

        state         = "waiting_wake"
        audio_buffer  = []
        silence_start = None
        speech_start  = None
        rolling_probs = []

        self.get_logger().info('待機中，說 "Hey Jarvis" 喚醒...')

        while rclpy.ok():
            try:
                data      = stream.read(self.CHUNK, exception_on_overflow=False)
                raw       = np.frombuffer(data, dtype=np.int16).copy()
                resampled = linear_resample(raw, self.HW_RATE, self.TARGET_RATE)
                volume    = int(np.abs(resampled).mean())

                # 狀態一：等待喚醒詞（oww）
                if state == "waiting_wake":
                    scores     = self.oww.predict(resampled)
                    wake_score = scores.get("hey_jarvis", 0.0)

                    print(
                        f"vol={volume:5d} | wake={wake_score:.3f} | 待機",
                        end='\r', file=sys.stderr
                    )

                    if wake_score >= self.WAKE_THRESHOLD:
                        self.get_logger().info(
                            f'\n"Hey Jarvis" 偵測到 (score={wake_score:.2f})，開始監聽...'
                        )
                        state         = "recording"
                        audio_buffer  = []
                        silence_start = None
                        speech_start  = time.time()
                        rolling_probs = []
                        self.oww.reset()

                # 狀態二：錄音中（Silero VAD 判斷人聲）
                elif state == "recording":
                    vad_prob = self._vad_prob(resampled)

                    # Rolling VAD
                    rolling_probs.append(vad_prob)
                    if len(rolling_probs) > self.VAD_ROLLING_N:
                        rolling_probs.pop(0)
                    avg_prob = float(np.mean(rolling_probs))

                    print(
                        f"vol={volume:5d} | vad={avg_prob:.3f} | 錄音中",
                        end='\r', file=sys.stderr
                    )

                    if avg_prob > self.VAD_THRESHOLD and volume > self.VOLUME_MIN:
                        # 有人聲且音量夠大，繼續錄
                        audio_buffer.append(resampled)
                        silence_start = None

                        # 強制斷句
                        if time.time() - speech_start > self.MAX_SPEECH_SEC:
                            self.get_logger().info(f"\n[超過 {self.MAX_SPEECH_SEC}s，強制送辨識]")
                            self._process_and_publish(audio_buffer)
                            state         = "waiting_wake"
                            audio_buffer  = []
                            silence_start = None
                            speech_start  = None
                            rolling_probs = []
                            self.get_logger().info('回到待機，說 "Hey Jarvis" 再次喚醒')

                    else:
                        # 無人聲
                        if audio_buffer:
                            audio_buffer.append(resampled)

                        if silence_start is None and audio_buffer:
                            silence_start = time.time()
                        elif silence_start and \
                                time.time() - silence_start > self.SILENCE_DURATION:

                            total_sec = sum(len(c) for c in audio_buffer) / self.TARGET_RATE

                            if total_sec < self.MIN_SPEECH_SEC:
                                self.get_logger().info(f"\n[音訊太短 ({total_sec:.2f}s)，丟棄]")
                            else:
                                self.get_logger().info(f"\n[斷句 ({total_sec:.2f}s)，送 Gemini 辨識...]")
                                self._process_and_publish(audio_buffer)

                            state         = "waiting_wake"
                            audio_buffer  = []
                            silence_start = None
                            speech_start  = None
                            rolling_probs = []
                            self.get_logger().info('回到待機，說 "Hey Jarvis" 再次喚醒')

            except Exception as e:
                self.get_logger().error(f"\n錄音迴圈錯誤: {e}")
                break

        stream.stop_stream()
        stream.close()
        p.terminate()

    def _process_and_publish(self, audio_buffer: list):
        combined = np.concatenate(audio_buffer)
        text     = self._gemini_transcribe(combined)

        if not text:
            self.get_logger().info("[辨識結果為空，略過]")
            return

        self.get_logger().info(f"辨識結果: {text}")

        msg      = String()
        msg.data = text
        self.publisher_.publish(msg)

        meta      = String()
        meta.data = json.dumps({"text": text}, ensure_ascii=False)
        self.publisher_meta.publish(meta)


def main(args=None):
    rclpy.init(args=args)
    node = STTNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()