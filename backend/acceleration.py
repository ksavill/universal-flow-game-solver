from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, Optional


def _requested_mode() -> str:
    value = os.getenv("FLOW_ACCELERATOR", "auto").strip().lower()
    return value if value in {"auto", "cpu", "cuda"} else "auto"


@lru_cache(maxsize=1)
def acceleration_capabilities() -> Dict[str, Any]:
    requested = _requested_mode()
    opencv_devices = 0
    opencv_cuda = False
    try:
        import cv2  # type: ignore

        opencv_devices = int(cv2.cuda.getCudaEnabledDeviceCount())
        build_info = cv2.getBuildInformation()
        opencv_cuda = opencv_devices > 0 and "NVIDIA CUDA:                   YES" in build_info
    except Exception:
        pass

    torch_available = False
    torch_cuda = False
    torch_version: Optional[str] = None
    cuda_version: Optional[str] = None
    device_name: Optional[str] = None
    if requested != "cpu":
        try:
            import torch

            torch_available = True
            torch_version = str(torch.__version__)
            cuda_version = str(torch.version.cuda) if torch.version.cuda else None
            torch_cuda = bool(torch.cuda.is_available() and torch.cuda.device_count() > 0)
            if torch_cuda:
                device_name = str(torch.cuda.get_device_name(0))
        except Exception:
            pass

    enabled = requested != "cpu" and (opencv_cuda or torch_cuda)
    image_backend = "opencv-cuda" if opencv_cuda else ("torch-cuda" if torch_cuda else "cpu")
    return {
        "requested": requested,
        "cuda_available": bool(opencv_cuda or torch_cuda),
        "cuda_enabled": enabled,
        "device": device_name,
        "cuda_version": cuda_version,
        "opencv_cuda": opencv_cuda,
        "opencv_cuda_devices": opencv_devices,
        "torch_available": torch_available,
        "torch_cuda": torch_cuda,
        "torch_version": torch_version,
        "image_backend": image_backend,
        "features": {
            "grid_detection": image_backend,
            "image_annotation": image_backend,
            # Exact SAT/SMT engines are native CPU solvers. GPU work is kept
            # out of their inner loop to preserve exactness and avoid transfer
            # overhead on the app's <=500-node graphs.
            "exact_solving": "native-cpu-sat",
        },
    }


def accelerated_gray_u8(rgb: Any, *, cv2: Any, np: Any) -> tuple[Any, str]:
    """Convert an RGB uint8 array to grayscale on the selected backend."""

    capabilities = acceleration_capabilities()
    if capabilities["cuda_enabled"] and capabilities["opencv_cuda"]:
        try:
            gpu_rgb = cv2.cuda_GpuMat()
            gpu_rgb.upload(rgb)
            gpu_gray = cv2.cuda.cvtColor(gpu_rgb, cv2.COLOR_RGB2GRAY)
            return gpu_gray.download(), "opencv-cuda"
        except Exception:
            pass

    if capabilities["cuda_enabled"] and capabilities["torch_cuda"] and int(rgb.size) >= 750_000:
        try:
            import torch

            tensor = torch.as_tensor(np.array(rgb, copy=True), device="cuda", dtype=torch.int32)
            # Integer coefficients closely match OpenCV's deterministic RGB
            # conversion and avoid reduced-precision CUDA math.
            gray = (
                tensor[..., 0] * 4899
                + tensor[..., 1] * 9617
                + tensor[..., 2] * 1868
                + 8192
            ) >> 14
            return gray.clamp_(0, 255).to(torch.uint8).cpu().numpy(), "torch-cuda"
        except Exception:
            pass

    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), "cpu"


__all__ = ["acceleration_capabilities", "accelerated_gray_u8"]
