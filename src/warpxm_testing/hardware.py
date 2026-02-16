"""Identify the current hardware configuration (CPU + GPU)."""

import platform
import subprocess


def get_cpu_name() -> str:
    """Return a human-readable CPU model string."""
    system = platform.system()
    if system == "Darwin":
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    elif system == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except FileNotFoundError:
            pass
    return platform.processor() or "unknown"


def get_gpu_name() -> str:
    """Return a GPU model string, or 'none' if no GPU is detected."""
    # Try NVIDIA first
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            # May return multiple GPUs, take first
            return result.stdout.strip().splitlines()[0].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # On Apple Silicon, the GPU is integrated
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        cpu = get_cpu_name()
        if "Apple" in cpu:
            # e.g. "Apple M1 (integrated)"
            chip = cpu.split()[1] if len(cpu.split()) > 1 else cpu
            return f"Apple {chip} (integrated)"

    return "none"


def get_hardware_id() -> str:
    """Return a unique hardware identifier string: 'cpu | gpu'."""
    return f"{get_cpu_name()} | {get_gpu_name()}"


def get_hardware_info() -> dict:
    """Return a dict with cpu, gpu, and combined hardware_id."""
    cpu = get_cpu_name()
    gpu = get_gpu_name()
    return {
        "cpu": cpu,
        "gpu": gpu,
        "hardware_id": f"{cpu} | {gpu}",
    }
