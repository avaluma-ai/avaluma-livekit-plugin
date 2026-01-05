"""
Auto-configure library paths for avaluma_runtime C++ module
"""

import ctypes
import logging
import os
import sys

logger = logging.getLogger("Avaluma Binary")


# Get the directory where this file is located
BINARY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
LIB_DIR = os.path.join(BINARY_DIR, "lib")

if os.path.exists(BINARY_DIR):
    logger.info("Directory for avaluma_runtime C++ module: %s", BINARY_DIR)
else:
    os.mkdir(BINARY_DIR)

if not os.listdir(BINARY_DIR):
    logger.error(
        "Directory for avaluma_runtime C++ module is empty. Please add avaluma_runtime.so and lib directory to %s.",
        BINARY_DIR,
    )
    raise RuntimeError("Bin directory for avaluma_runtime C++ module is empty")

# Add plugin directory to sys.path so avaluma_runtime.so can be found
if BINARY_DIR not in sys.path:
    sys.path.insert(0, BINARY_DIR)

# Preload shared libraries using ctypes (works even after Python startup)
if os.path.exists(LIB_DIR):
    # Add to LD_LIBRARY_PATH for reference (informational)
    current_ld_path = os.environ.get("LD_LIBRARY_PATH", "")
    if LIB_DIR not in current_ld_path:
        os.environ["LD_LIBRARY_PATH"] = f"{LIB_DIR}:{current_ld_path}"

    # Explicitly preload critical shared libraries in dependency order
    # C++ runtime must be loaded first!
    libs_to_preload = [
        "libgcc_s.so.1",  # GCC runtime (load first)
        "libstdc++.so.6",  # C++ standard library (load second)
        "libonnxruntime.so.1",
        "libonnxruntime_providers_shared.so",
        # 'libonnxruntime_providers_cuda.so',
        "libdatachannel.so",
        "libopus.so",
        "libwebp.so",
        "libx264.so",
        "libcnpy.so",
    ]

    for lib_name in libs_to_preload:
        lib_path = os.path.join(LIB_DIR, lib_name)
        if os.path.exists(lib_path):
            try:
                ctypes.CDLL(lib_path, mode=ctypes.RTLD_GLOBAL)
            except Exception as e:
                logger.warning(f"Could not preload {lib_name}: {e}")

    logger.info(f"Preloaded shared libraries from {LIB_DIR}")
else:
    logger.error("Lib directory not found at %s", LIB_DIR)

from .bin import avaluma_runtime
