"""Local webcam capture — single-frame grab for Claude vision.

Mirror of tools/computer.py screenshot pattern: returns (b64_jpeg, (w, h)).
The frame is wrapped as a Claude image content block in agent/core.py's
_make_tool_result_content so the model can see it.

Setup:
  pip install opencv-python
  Set CAMERA_ENABLED=true in .env (off by default).
  CAMERA_DEVICE_INDEX picks which camera (0 = default).
"""
import base64
import io
from typing import Tuple

import config


def is_enabled() -> bool:
    return bool(getattr(config, "CAMERA_ENABLED", False))


_tracker_frame_fn = None   # set by the hand tracker when it starts


def set_tracker_frame_source(fn) -> None:
    """Let the hand tracker offer its latest frame instead of the raw device.

    The webcam is exclusive. Without this, enabling hand tracking would make
    `camera_capture` fail with "could not open camera" — Apex blocked by Apex.
    """
    global _tracker_frame_fn
    _tracker_frame_fn = fn


def _frame_from_tracker():
    """The tracker's most recent frame, or None if it isn't running."""
    if _tracker_frame_fn is None:
        return None
    try:
        return _tracker_frame_fn()
    except Exception:
        return None


def capture(device_index: int | None = None, jpeg_quality: int = 85) -> Tuple[str, Tuple[int, int]]:
    """Grab one frame from the webcam. Returns (base64_jpeg, (width, height))."""
    if not is_enabled():
        raise RuntimeError("Camera is disabled. Set CAMERA_ENABLED=true in .env.")

    try:
        import cv2  # type: ignore
    except ImportError as e:
        raise RuntimeError("opencv-python not installed. Run: pip install opencv-python") from e

    idx = device_index if device_index is not None else getattr(config, "CAMERA_DEVICE_INDEX", 0)

    # If Apex's own hand tracker is running, it already holds this device and a
    # second VideoCapture on it fails. Take its frame instead of fighting it —
    # otherwise switching hand tracking on would silently break camera_capture,
    # which is two halves of the same program competing for one webcam.
    frame = _frame_from_tracker()
    if frame is not None:
        ok = True
    else:
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            raise RuntimeError(
                f"Could not open camera at index {idx}. If hand tracking is on, "
                f"the tracker holds the camera — release_camera hands it back."
            )
        try:
            # Discard a few frames — many webcams need warmup for proper exposure
            for _ in range(3):
                cap.read()
            ok, frame = cap.read()
        finally:
            cap.release()

    if not ok or frame is None:
        raise RuntimeError("Failed to capture frame from camera")

    # frame is BGR; encode as JPEG
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
    if not ok:
        raise RuntimeError("Failed to encode frame as JPEG")

    h, w = frame.shape[:2]
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return b64, (w, h)
