"""Pausable / stoppable run controller for long calculations (forecast).

Wraps a generator that yields progress events (see ``network.forecast.iter_forecast``). Nothing runs between ``advance()``
calls, so *pause* simply means not advancing, *continue* resumes from the exact same state, and *stop* keeps the last
snapshot as a valid partial result. A Streamlit script re-run (button click) does not disturb a suspended generator.
"""
from __future__ import annotations
import time


class RunController:
    RUNNING, PAUSED, STOPPED, DONE, FAILED = 'running', 'paused', 'stopped', 'done', 'failed'

    def __init__(self, generator, label='Forecast'):
        self.gen = generator; self.label = label; self.status = self.RUNNING
        self.last = None; self.snapshot = None; self.error = None; self.events = 0; self.t_run = 0.0; self._t_resume = time.perf_counter()

    # -- control -----------------------------------------------------------------------------------
    def pause(self):
        if self.status == self.RUNNING: self._bank(); self.status = self.PAUSED

    def resume(self):
        if self.status == self.PAUSED: self._t_resume = time.perf_counter(); self.status = self.RUNNING

    def stop(self):
        if self.status in (self.RUNNING, self.PAUSED):
            self._bank(); self.status = self.STOPPED
            try: self.gen.close()
            except Exception: pass

    def _bank(self): self.t_run += time.perf_counter() - self._t_resume

    # -- progress ----------------------------------------------------------------------------------
    @property
    def active(self): return self.status in (self.RUNNING, self.PAUSED)

    @property
    def fraction(self):
        e = self.last
        if not e: return 0.0
        if self.status == self.DONE: return 1.0
        return min(max(e['step'] / max(e['n_steps'], 1), 0.0), 1.0)

    def advance(self, max_events=1, time_budget_s=None):
        """Advance the generator by up to ``max_events`` events (or until ``time_budget_s`` elapses). Returns the last event."""
        if self.status != self.RUNNING: return self.last
        t0 = time.perf_counter(); n = 0
        while self.status == self.RUNNING and n < max_events:
            try: ev = next(self.gen)
            except StopIteration: self._bank(); self.status = self.DONE; break
            except Exception as exc: self._bank(); self.status = self.FAILED; self.error = str(exc); break
            self.last = ev; self.events += 1; n += 1
            if ev.get('result') is not None: self.snapshot = ev['result']
            if ev['type'] == 'done': self._bank(); self.status = self.DONE
            if time_budget_s is not None and time.perf_counter() - t0 >= time_budget_s: break
        return self.last

    def describe(self):
        e = self.last
        if not e: return 'Starting…'
        eta = e.get('eta_s')
        s = f"{e['stage']} · step {min(e['step'] + (0 if e['type'] == 'step' else 1), e['n_steps'])}/{e['n_steps']}"
        if e.get('substep'): s += f" · substep {e['substep'] + 1}"
        s += f" · elapsed {e['elapsed_s']:.0f} s"
        if eta is not None and self.status == self.RUNNING: s += f" · ≈{eta:.0f} s left"
        return s
