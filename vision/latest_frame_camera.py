import atexit
import glob
import os
import re
import signal
import subprocess
import threading
import time

import cv2


class LatestFrameCamera:
    """Continuously read frames so callers can grab the newest image instantly."""

    def __init__(self, camera_id=0, width=None, height=None):
        self.camera_id = camera_id
        self.active_camera_id = None
        self.width = width
        self.height = height
        self.cap = None
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest_frame = None
        self.latest_time = None
        self._atexit_registered = False
        self._signals_registered = False
        self._previous_signal_handlers = {}

    def start(self):
        self._register_cleanup_handlers()
        self.stop_event.clear()
        self.latest_frame = None
        self.latest_time = None

        camera_users = self._camera_users()
        if camera_users:
            raise RuntimeError(f"相機目前被其他程式占用，PID：{camera_users}")

        self.cap = self._open_capture()

        if self.width is not None:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height is not None:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        # Not every backend honors this, but when it does it prevents stale frames.
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.cap.isOpened():
            self.cap.release()
            self.cap = None
            camera_users = self._camera_users()
            if camera_users:
                raise RuntimeError(
                    f"無法開啟相機：{self.camera_id}。目前相機可能被占用：{camera_users}"
                )
            raise RuntimeError(f"無法開啟相機：{self.camera_id}")

        self.thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.thread.start()

    def read_latest(self, timeout=2.0):
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            with self.lock:
                if self.latest_frame is not None:
                    frame_age = time.monotonic() - self.latest_time
                    return True, self.latest_frame.copy(), frame_age

            time.sleep(0.01)

        return False, None, None

    def release(self):
        if self.cap is None and self.thread is None:
            return

        self.stop_event.set()

        thread = self.thread
        cap = self.cap

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)

        if cap is not None:
            cap.release()

        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)

        self.thread = None
        self.cap = None
        self.active_camera_id = None

        with self.lock:
            self.latest_frame = None
            self.latest_time = None

    def _reader_loop(self):
        while not self.stop_event.is_set():
            cap = self.cap
            if cap is None:
                break

            ret, frame = cap.read()

            if not ret:
                time.sleep(0.01)
                continue

            with self.lock:
                self.latest_frame = frame
                self.latest_time = time.monotonic()

    def _open_capture(self):
        for camera_id in self._candidate_camera_ids():
            cap = self._open_single_capture(camera_id)
            if cap.isOpened():
                self.active_camera_id = camera_id
                return cap
            cap.release()

        return cv2.VideoCapture()

    def _candidate_camera_ids(self):
        if self.camera_id in (None, "auto"):
            camera_ids = []
            for path in glob.glob("/dev/video*"):
                match = re.search(r"\d+$", path)
                if match:
                    camera_ids.append(int(match.group()))

            return sorted(camera_ids)

        return [self.camera_id]

    def _open_single_capture(self, camera_id):
        if hasattr(cv2, "CAP_V4L2"):
            cap = cv2.VideoCapture(camera_id, cv2.CAP_V4L2)
            if cap.isOpened():
                return cap
            cap.release()

        return cv2.VideoCapture(camera_id)

    def _camera_users(self):
        video_paths = glob.glob("/dev/video*")
        if not video_paths:
            return ""

        try:
            result = subprocess.run(
                ["fuser", *video_paths],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return ""

        pids = [pid for pid in re.findall(r"\d+", result.stdout) if int(pid) != os.getpid()]
        if not pids:
            return ""

        return ", ".join(sorted(set(pids), key=int))

    def _register_cleanup_handlers(self):
        if not self._atexit_registered:
            atexit.register(self.release)
            self._atexit_registered = True

        if self._signals_registered:
            return

        if threading.current_thread() is not threading.main_thread():
            return

        for signal_name in (
            "SIGINT",
            "SIGTERM",
            "SIGHUP",
            "SIGTSTP",
            "SIGTTIN",
            "SIGTTOU",
        ):
            shutdown_signal = getattr(signal, signal_name, None)
            if shutdown_signal is None:
                continue

            self._previous_signal_handlers[shutdown_signal] = signal.getsignal(shutdown_signal)
            signal.signal(shutdown_signal, self._handle_shutdown_signal)

        self._signals_registered = True

    def _handle_shutdown_signal(self, signum, frame):
        self.release()

        previous_handler = self._previous_signal_handlers.get(signum)
        if callable(previous_handler):
            previous_handler(signum, frame)
            return

        if previous_handler == signal.SIG_IGN:
            return

        raise SystemExit(128 + signum)
