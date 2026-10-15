"""The runner loop and live progress reporting.

A runner registers, then claims jobs of the types it advertises and runs them one at a
time. While a job runs, `Reporter` sends the exact live state (stage, video i of n,
percents, message) every couple of seconds, plus buffered log lines. A heartbeat thread
keeps the runner marked alive and notices when the user cancels the job.
"""

from __future__ import annotations

import collections
import logging
import platform
import shutil
import threading
import time
import traceback
from pathlib import Path

from .client import JobLost, PlatformClient
from .config import RunnerConfig

log = logging.getLogger("runner")


class JobCancelled(Exception):
    pass


class RetryableError(RuntimeError):
    """A transient failure (throttling, network): the platform requeues the job."""


class Reporter:
    def __init__(self, client: PlatformClient, job: dict, stages: list[str], min_interval: float = 2.0):
        self.client = client
        self.job_id = job["id"]
        self.stages = stages
        self.video_count = len(job.get("items") or []) or 1
        self.min_interval = min_interval
        self.stage_name = stages[0] if stages else ""
        self.stage_index = 1
        self.video_index = 1
        self.video_id: int | None = None
        self.stage_percent = 0.0
        self.message = ""
        self.cancelled = threading.Event()
        self._last_sent = 0.0
        self._log_buffer: list[str] = []
        self.tail: collections.deque[str] = collections.deque(maxlen=400)
        self._lock = threading.Lock()

    # ------------------------------------------------------------ state

    def stage(self, name: str, message: str = "") -> None:
        if name not in self.stages:
            self.stages.append(name)
        self.stage_name = name
        self.stage_index = self.stages.index(name) + 1
        self.video_index, self.video_id = 1, None
        self.stage_percent = 0.0
        self.message = message
        self.update(force=True)

    def video(self, index: int, video_id: int | None, message: str = "") -> None:
        """index is 1-based."""
        self.video_index, self.video_id = index, video_id
        self.stage_percent = 0.0
        self.message = message
        self.update(force=True)

    @property
    def overall_percent(self) -> float:
        n = max(len(self.stages), 1)
        within = ((self.video_index - 1) + self.stage_percent / 100) / self.video_count
        return 100 * ((self.stage_index - 1) + min(within, 1.0)) / n

    def update(self, stage_percent: float | None = None, message: str | None = None, force: bool = False) -> None:
        if self.cancelled.is_set():
            raise JobCancelled()
        if stage_percent is not None:
            self.stage_percent = max(0.0, min(100.0, float(stage_percent)))
        if message is not None:
            self.message = message
        now = time.monotonic()
        if not force and now - self._last_sent < self.min_interval:
            return
        self._last_sent = now
        with self._lock:
            logs, self._log_buffer = self._log_buffer, []
        fields = {
            "stage": self.stage_name,
            "stage_index": self.stage_index,
            "stage_count": len(self.stages),
            "video_index": self.video_index,
            "video_count": self.video_count,
            "video_id": self.video_id,
            "stage_percent": round(self.stage_percent, 2),
            "overall_percent": round(self.overall_percent, 2),
            "message": self.message,
        }
        if logs:
            fields["log"] = "\n".join(logs)
        resp = self.client.progress(self.job_id, {k: v for k, v in fields.items() if v is not None})
        if resp.get("cancel"):
            self.cancelled.set()
            raise JobCancelled()

    def log(self, line: str) -> None:
        with self._lock:
            self._log_buffer.append(line)
            self.tail.append(line)

    def flush(self) -> None:
        self.update(force=True)

    def log_tail(self) -> str:
        return "\n".join(self.tail)


class _JobLogHandler(logging.Handler):
    def __init__(self, reporter: Reporter):
        super().__init__(level=logging.INFO)
        self.reporter = reporter
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(("werkzeug", "urllib3", "httpx", "httpcore")):
            return  # HTTP chatter, not job output
        try:
            self.reporter.log(self.format(record))
        except Exception:
            pass


class BaseRunner:
    type = "cpu"
    capabilities: list[str] = []
    version = "0.1.0"
    default_name = "runner"

    def __init__(self, cfg: RunnerConfig | None = None):
        self.cfg = cfg or RunnerConfig.load(self.default_name)
        self.client = PlatformClient(self.cfg.url, self.cfg.token)
        self.current: Reporter | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ hooks for subclasses

    def stages_for(self, job: dict) -> list[str]:
        return [job["type"]]

    def handle(self, job: dict, reporter: Reporter) -> None:
        raise NotImplementedError

    def info(self) -> dict:
        info = {"python": platform.python_version()}
        root = self.data_root()
        if root and root.exists():
            info["disk_free_bytes"] = shutil.disk_usage(root).free
        return info

    def data_root(self) -> Path | None:
        return None

    def gpu_name(self) -> str | None:
        return None

    # ------------------------------------------------------------ loop

    def register(self) -> None:
        r = self.client.register(
            self.cfg.name,
            self.type,
            self.capabilities,
            gpu=self.gpu_name(),
            host=platform.node(),
            version=self.version,
            info=self.info(),
        )
        log.info("registered as %s (#%s) for %s", r["name"], r["id"], ", ".join(self.capabilities))

    def _heartbeats(self) -> None:
        while not self._stop.wait(self.cfg.heartbeat_seconds):
            try:
                resp = self.client.heartbeat(self.info())
                cur = self.current
                if cur is not None and cur.job_id in (resp.get("cancelled_jobs") or []):
                    cur.cancelled.set()
            except Exception as err:  # keep beating; the main loop surfaces real outages
                log.warning("heartbeat failed: %s", err)

    def serve(self, once: bool = False) -> None:
        self.register()
        threading.Thread(target=self._heartbeats, daemon=True, name="heartbeat").start()
        log.info("waiting for jobs at %s", self.cfg.url)
        try:
            while not self._stop.is_set():
                job = self.client.claim()
                if job is None:
                    if once:
                        return
                    self._stop.wait(self.cfg.poll_seconds)
                    continue
                self.run_one(job)
                if once:
                    return
        finally:
            self._stop.set()

    def run_one(self, job: dict) -> None:
        reporter = Reporter(self.client, job, self.stages_for(job))
        self.current = reporter
        handler = _JobLogHandler(reporter)
        logging.getLogger().addHandler(handler)
        log.info("job #%s: %s for %d videos", job["id"], job["type"], len(job["items"]))
        try:
            self.handle(job, reporter)
            reporter.flush()
            self.client.complete(job["id"])
            log.info("job #%s done", job["id"])
        except JobCancelled:
            log.info("job #%s was cancelled; stopped", job["id"])
        except JobLost as err:
            log.warning("job #%s was taken away: %s", job["id"], err)
        except RetryableError as err:
            log.warning("job #%s hit a transient error: %s", job["id"], err)
            self._safe_fail(job, str(err), reporter, retry=True)
        except Exception as err:
            log.error("job #%s failed: %s\n%s", job["id"], err, traceback.format_exc())
            self._safe_fail(job, f"{type(err).__name__}: {err}", reporter, retry=False)
        finally:
            logging.getLogger().removeHandler(handler)
            self.current = None

    def _safe_fail(self, job: dict, error: str, reporter: Reporter, retry: bool) -> None:
        try:
            self.client.fail(job["id"], error, reporter.log_tail(), retry=retry)
        except JobLost:
            pass
        except Exception as err:
            log.error("could not report failure of job #%s: %s", job["id"], err)


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
