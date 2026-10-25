"""Samples CPU and memory while a benchmark runs (resource utilization metric)."""
import threading

import psutil


class ResourceMonitor:
    """Context manager sampling system-wide CPU % and the RSS of a process tree."""

    def __init__(self, root_pid: int | None = None, interval: float = 0.1):
        self.root = psutil.Process(root_pid)
        self.interval = interval
        self.cpu: list[float] = []
        self.rss: list[int] = []
        self._stop = threading.Event()

    def _tree_rss(self) -> int:
        total = 0
        for p in [self.root, *self.root.children(recursive=True)]:
            try:
                total += p.memory_info().rss
            except psutil.Error:
                pass
        return total

    def _run(self) -> None:
        psutil.cpu_percent(None)
        while not self._stop.wait(self.interval):
            self.cpu.append(psutil.cpu_percent(None))
            self.rss.append(self._tree_rss())

    def __enter__(self):
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()

    @property
    def summary(self) -> dict:
        n_cpu = psutil.cpu_count()
        avg = sum(self.cpu) / len(self.cpu) if self.cpu else 0.0
        return {
            "cpu_avg_pct": round(avg, 1),                       # of the whole machine
            "cpu_peak_pct": round(max(self.cpu, default=0.0), 1),
            "cores_busy_avg": round(avg / 100 * n_cpu, 2),       # ~ how many cores were used
            "rss_peak_mb": round(max(self.rss, default=0) / 2**20, 1),
        }
