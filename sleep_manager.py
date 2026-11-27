import ctypes
import threading
import time
from dataclasses import dataclass

# Windows SetThreadExecutionState flags
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


@dataclass
class SleepStatus:
    active: bool
    keep_display_on: bool
    timeout_seconds: int
    remaining_seconds: int


class SleepManager:
    """Keeps Windows awake for a sliding duration that resets on every interaction."""

    def __init__(self, default_timeout_seconds: int = 7 * 3600, keep_display_on: bool = False):
        self.default_timeout_seconds = default_timeout_seconds
        self.keep_display_on = keep_display_on
        self._lock = threading.Lock()
        self._expires_at: float = 0.0
        self._active: bool = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.touch()
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._watchdog_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            self._active = False
            self._expires_at = 0.0
            self._apply_execution_state(active=False, keep_display=False)

    def touch(self, timeout_seconds: int | None = None) -> None:
        """Reset the awake timer and immediately enforce execution state."""
        duration = timeout_seconds if timeout_seconds is not None else self.default_timeout_seconds
        with self._lock:
            self._expires_at = time.monotonic() + duration
            self._active = True
            self._apply_execution_state(active=True, keep_display=self.keep_display_on)

    def set_keep_display(self, keep_display_on: bool) -> None:
        with self._lock:
            self.keep_display_on = keep_display_on
            if self._active:
                self._apply_execution_state(active=True, keep_display=self.keep_display_on)

    def status(self) -> SleepStatus:
        with self._lock:
            remaining = max(0, int(self._expires_at - time.monotonic())) if self._active else 0
            return SleepStatus(
                active=self._active and remaining > 0,
                keep_display_on=self.keep_display_on,
                timeout_seconds=self.default_timeout_seconds,
                remaining_seconds=remaining,
            )

    def _watchdog_loop(self) -> None:
        while not self._stop_event.wait(15):
            with self._lock:
                if not self._active:
                    continue
                if time.monotonic() >= self._expires_at:
                    self._active = False
                    self._apply_execution_state(active=False, keep_display=False)
                else:
                    # Re-assert periodically so Windows never drops the state
                    self._apply_execution_state(active=True, keep_display=self.keep_display_on)

    @staticmethod
    def _apply_execution_state(active: bool, keep_display: bool) -> None:
        if not hasattr(ctypes, "windll"):
            return
        flags = ES_CONTINUOUS
        if active:
            flags |= ES_SYSTEM_REQUIRED
            if keep_display:
                flags |= ES_DISPLAY_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
