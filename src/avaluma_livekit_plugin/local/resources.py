"""Resource monitoring for Avaluma avatar sessions"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import psutil

from ..log import logger

try:
    import pynvml

    NVML_AVAILABLE = True
except ImportError:
    NVML_AVAILABLE = False
    logger.warning("pynvml not available, VRAM monitoring will be disabled")


class InsufficientResourcesError(Exception):
    """Exception raised when insufficient system resources are available"""

    def __init__(
        self,
        message: str,
        *,
        available_ram_gb: Optional[float] = None,
        required_ram_gb: Optional[float] = None,
        available_vram_gb: Optional[float] = None,
        required_vram_gb: Optional[float] = None,
        gpu_name: Optional[str] = None,
    ):
        super().__init__(message)
        self.available_ram_gb = available_ram_gb
        self.required_ram_gb = required_ram_gb
        self.available_vram_gb = available_vram_gb
        self.required_vram_gb = required_vram_gb
        self.gpu_name = gpu_name


@dataclass
class ResourceThresholds:
    """Thresholds for resource availability checks"""

    min_free_ram_gb: float = float(os.getenv("AVALUMA_MIN_FREE_RAM_GB", "3.0"))
    min_free_vram_gb: float = float(os.getenv("AVALUMA_MIN_FREE_VRAM_GB", "3.0"))
    gpu_device_id: int = int(os.getenv("AVALUMA_GPU_DEVICE_ID", "0"))


@dataclass
class ResourceStatus:
    """Current system resource status"""

    total_ram_gb: float
    available_ram_gb: float
    total_vram_gb: Optional[float] = None
    available_vram_gb: Optional[float] = None
    gpu_name: Optional[str] = None


class ResourceMonitor:
    """Monitor system RAM and GPU VRAM resources"""

    def __init__(self, thresholds: Optional[ResourceThresholds] = None):
        """
        Initialize the resource monitor.

        Args:
            thresholds: Resource thresholds to check against. If not provided,
                       defaults from environment variables will be used.
        """
        self.thresholds = thresholds or ResourceThresholds()
        self._nvml_initialized = False

        if NVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self._nvml_initialized = True
                logger.info("NVML initialized for VRAM monitoring")
            except Exception as e:
                logger.warning(f"Failed to initialize NVML: {e}")

    def get_status(self) -> ResourceStatus:
        """
        Get current resource status.

        Returns:
            ResourceStatus object with current RAM and VRAM information
        """
        # Get RAM status
        mem = psutil.virtual_memory()
        total_ram_gb = mem.total / (1024**3)
        available_ram_gb = mem.available / (1024**3)

        # Get VRAM status if available
        total_vram_gb = None
        available_vram_gb = None
        gpu_name = None

        if self._nvml_initialized:
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(
                    self.thresholds.gpu_device_id
                )
                gpu_name = pynvml.nvmlDeviceGetName(handle)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                total_vram_gb = mem_info.total / (1024**3)
                available_vram_gb = mem_info.free / (1024**3)
            except Exception as e:
                logger.warning(f"Failed to get VRAM info: {e}")

        return ResourceStatus(
            total_ram_gb=total_ram_gb,
            available_ram_gb=available_ram_gb,
            total_vram_gb=total_vram_gb,
            available_vram_gb=available_vram_gb,
            gpu_name=gpu_name,
        )

    def check_resources(self) -> ResourceStatus:
        """
        Check if sufficient resources are available.

        Returns:
            ResourceStatus object if resources are sufficient

        Raises:
            InsufficientResourcesError: If resources are below thresholds
        """
        status = self.get_status()

        # Check RAM
        if status.available_ram_gb < self.thresholds.min_free_ram_gb:
            raise InsufficientResourcesError(
                f"Insufficient RAM: {status.available_ram_gb:.2f} GB available, "
                f"{self.thresholds.min_free_ram_gb:.2f} GB required",
                available_ram_gb=status.available_ram_gb,
                required_ram_gb=self.thresholds.min_free_ram_gb,
            )

        # Check VRAM if available
        if self._nvml_initialized and status.available_vram_gb is not None:
            if status.available_vram_gb < self.thresholds.min_free_vram_gb:
                raise InsufficientResourcesError(
                    f"Insufficient VRAM: {status.available_vram_gb:.2f} GB available, "
                    f"{self.thresholds.min_free_vram_gb:.2f} GB required. "
                    f"GPU: {status.gpu_name}",
                    available_vram_gb=status.available_vram_gb,
                    required_vram_gb=self.thresholds.min_free_vram_gb,
                    gpu_name=status.gpu_name,
                )

        return status

    def cleanup(self) -> None:
        """Cleanup NVML resources"""
        if self._nvml_initialized:
            try:
                pynvml.nvmlShutdown()
                self._nvml_initialized = False
                logger.info("NVML shutdown complete")
            except Exception as e:
                logger.warning(f"Error during NVML shutdown: {e}")
