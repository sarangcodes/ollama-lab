"""Mac memory readings: RAM, swap, memory pressure and the GPU's share of unified memory."""

from __future__ import annotations

import asyncio
import platform
import subprocess

import psutil

GB = 1024**3


def _sysctl(key: str) -> str | None:
    try:
        return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=2).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def snapshot() -> dict:
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    total_gb = vm.total / GB
    wired = _sysctl("iogpu.wired_limit_mb")
    # 0 means macOS's default GPU cap: about two thirds of RAM on Macs with up to 36 GB, three quarters above.
    gpu_limit_gb = int(wired) / 1024 if wired and wired != "0" else total_gb * (0.75 if total_gb > 36 else 2 / 3)
    free_pct = _sysctl("kern.memorystatus_level")
    return {
        "chip": _sysctl("machdep.cpu.brand_string") or platform.processor(),
        "macos": platform.mac_ver()[0],
        "ram_gb": round(total_gb, 1),
        "ram_used_gb": round((vm.total - vm.available) / GB, 2),
        "swap_used_gb": round(sw.used / GB, 2),
        "pressure_free_pct": int(free_pct) if free_pct and free_pct.isdigit() else None,
        "gpu_limit_gb": round(gpu_limit_gb, 1),
        "gpu_limit_custom": bool(wired and wired != "0"),
    }


class Sampler:
    """Samples RAM and swap in the background while a model runs, keeping the peaks."""

    def __init__(self, every: float = 0.5):
        self.every = every
        self.start = snapshot()
        self.peak_ram = self.start["ram_used_gb"]
        self.peak_swap = self.start["swap_used_gb"]
        self.min_free = self.start["pressure_free_pct"]
        self._task: asyncio.Task | None = None

    async def _run(self):
        while True:
            vm, sw = psutil.virtual_memory(), psutil.swap_memory()
            self.peak_ram = max(self.peak_ram, (vm.total - vm.available) / GB)
            self.peak_swap = max(self.peak_swap, sw.used / GB)
            lvl = _sysctl("kern.memorystatus_level")
            if lvl and lvl.isdigit():
                self.min_free = int(lvl) if self.min_free is None else min(self.min_free, int(lvl))
            await asyncio.sleep(self.every)

    def __enter__(self):
        self._task = asyncio.get_running_loop().create_task(self._run())
        return self

    def __exit__(self, *exc):
        if self._task:
            self._task.cancel()

    def result(self) -> dict:
        return {
            "ram_before_gb": self.start["ram_used_gb"],
            "ram_peak_gb": round(self.peak_ram, 2),
            "swap_growth_gb": round(max(0.0, self.peak_swap - self.start["swap_used_gb"]), 2),
            "pressure_min_free_pct": self.min_free,
        }
