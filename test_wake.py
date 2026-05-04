"""
test_wake.py - 測試 openWakeWord，寫成 ROS2 Node 結構
"""
import os
import sys
import rclpy
from rclpy.node import Node
import pyaudio
import numpy as np
from scipy.signal import resample_poly
from openwakeword.model import Model
import threading

MIC_KEYWORD    = os.getenv("MIC_DEVICE_KEYWORD", "UACDemo")
HW_RATE        = int(os.getenv("HW_RATE", "48000"))
CHUNK_HW       = int(HW_RATE * 0.033)
WAKE_THRESHOLD = 0.5

def find_mic(p, keyword):
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if keyword.lower() in info['name'].lower() and info['maxInputChannels'] > 0:
            return i, info['name']
    raise RuntimeError(f"找不到含 '{keyword}' 的麥克風")


class WakeTestNode(Node):
    def __init__(self):
        super().__init__('wake_test_node')

        # 找麥克風
        p_temp = pyaudio.PyAudio()
        self.mic_idx, mic_name = find_mic(p_temp, MIC_KEYWORD)
        p_temp.terminate()
        self.get_logger().info(f"麥克風: [{self.mic_idx}] {mic_name}")

        # 載入 oww
        self.get_logger().info("載入 openWakeWord...")
        self.oww = Model(
            wakeword_models=["hey_jarvis"],
            inference_framework="onnx",
            vad_threshold=0.0,
        )
        self.get_logger().info('oww 載入完成，說 "Hey Jarvis"...')

        # 啟動執行緒
        self.thread = threading.Thread(target=self.listen_loop, daemon=True)
        self.thread.start()

    def listen_loop(self):
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=HW_RATE,
            input=True,
            input_device_index=self.mic_idx,
            frames_per_buffer=CHUNK_HW,
        )

        # 暖機
        for _ in range(5):
            stream.read(CHUNK_HW, exception_on_overflow=False)

        # 確認
        test = np.frombuffer(stream.read(CHUNK_HW, exception_on_overflow=False), dtype=np.int16).copy()
        self.get_logger().info(f"stream max={np.abs(test).max()}")

        while rclpy.ok():
            raw = np.frombuffer(
                stream.read(CHUNK_HW, exception_on_overflow=False),
                dtype=np.int16,
            ).copy()
            duration  = len(raw) / HW_RATE
            num_out   = int(duration * 16000)
            audio_16k = np.interp(
                np.linspace(0, duration, num_out),
                np.linspace(0, duration, len(raw)),
                raw
            ).astype(np.int16)

            scores    = self.oww.predict(audio_16k)
            score     = scores.get("hey_jarvis", 0.0)
            volume    = int(np.abs(audio_16k).mean())

            if volume > 100:
                print(f"vol={volume:5d} | score={score:.4f}", file=sys.stderr)
            else:
                print(f"vol={volume:5d} | score={score:.2f} (靜音)", end='\r', file=sys.stderr)

            if score >= WAKE_THRESHOLD:
                self.get_logger().info(f"\n觸發! score={score:.2f}")
                self.oww.reset()

        stream.stop_stream()
        stream.close()
        p.terminate()


def main():
    rclpy.init()
    node = WakeTestNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()