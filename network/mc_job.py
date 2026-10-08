"""Monte Carlo as a background job.

A long Streamlit script run is fragile: any widget click, a dropped websocket or the host's request timeout stops the run and the page
restarts from scratch. The job below runs in its own thread (module-level registry, so it survives reruns), reports progress and an ETA, can be
cancelled, and leaves the result for the page to pick up. Realizations use the fast forecast settings (no per-element results,
fewer tubing segments).
"""
from __future__ import annotations
import threading, time, functools
from network.uncertainty import run_monte_carlo, MonteCarloCancelled

JOBS: dict[str, "MCJob"] = {}
_LOCK = threading.Lock()
SPEEDS = {'Accurate (12 tubing segments)': None, 'Balanced (6 segments)': 6, 'Fast (4 segments)': 4}


def fast_runner(vlp_segments=None):
    from network.forecast import run_forecast
    return functools.partial(run_forecast, store_elements=False, vlp_segments=vlp_segments)   # picklable (spawn workers)


class MCJob:
    def __init__(self, key):
        self.key, self.done, self.n, self.status, self.error, self.result = key, 0, 0, 'running', None, None
        self.t0 = time.time(); self.t_end = None; self._cancel = threading.Event(); self.thread = None

    def cancel(self): self._cancel.set()

    @property
    def elapsed(self): return (self.t_end or time.time()) - self.t0

    @property
    def eta_s(self):
        return None if self.done <= 0 or self.status != 'running' else self.elapsed / self.done * (self.n - self.done)


def start_job(key, nodes, edges, scenario, config, *, workers=1, keep_series=False, vlp_segments=None):
    """Start (or restart) the job registered under ``key``. Returns the job. Inputs are copied so later edits cannot change a running job."""
    import copy
    with _LOCK:
        old = JOBS.get(key)
        if old and old.status == 'running': old.cancel()
        job = MCJob(key); job.n = int(config.samples); JOBS[key] = job
    nodes, edges = copy.deepcopy(nodes), copy.deepcopy(edges)

    def work():
        try:
            def prog(i, n): job.done, job.n = i, n
            job.result = run_monte_carlo(nodes, edges, scenario, config, forecast_runner=fast_runner(vlp_segments), progress=prog,
                                         workers=int(workers), keep_series=bool(keep_series), cancel=job._cancel.is_set)
            job.status = 'done'
        except MonteCarloCancelled: job.status = 'cancelled'
        except BaseException as exc: job.status, job.error = 'failed', f'{exc.__class__.__name__}: {exc}'
        finally: job.t_end = time.time()
    job.thread = threading.Thread(target=work, name=f'mc-{key}', daemon=True); job.thread.start()
    return job


def get_job(key): return JOBS.get(key)
