import time
import os
from contextlib import contextmanager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMING_LOG_PATH = os.path.join(PROJECT_ROOT, "dashboardtiming.log")

def log_timing(category: str, detail: str, elapsed_sec: float, cache_status: str = "N/A") -> None:
    """Write detailed timing log to the separate dashboardtiming.log file."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_line = (
        f"[{timestamp}] [CATEGORY: {category.upper()}] "
        f"Detail: {detail} | Cache: {cache_status} | Elapsed: {elapsed_sec*1000:.2f}ms\n"
    )
    try:
        with open(TIMING_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception as e:
        print(f"[!] Logging error: {e}")

@contextmanager
def time_block(category: str, detail: str, cache_status: str = "N/A"):
    """Context manager to measure execution time of a code block."""
    start_time = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start_time
    log_timing(category, detail, elapsed, cache_status)
