"""Opt-in, request-local evaluation details; never added to the public Answer contract."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from threading import Lock
from time import perf_counter


class Diagnostics:
    def __init__(self):
        self.stages = {}
        self.evidence = []
        self.lock = Lock()


_current: ContextVar[Diagnostics | None] = ContextVar("evaluation_diagnostics", default=None)


@contextmanager
def capture_diagnostics():
    diagnostic = Diagnostics()
    token = _current.set(diagnostic)
    try:
        yield diagnostic
    finally:
        _current.reset(token)


def stage(name):
    def decorate(function):
        @wraps(function)
        def timed(*args, **kwargs):
            diagnostic = _current.get()
            if diagnostic is None:
                return function(*args, **kwargs)
            started = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                with diagnostic.lock:
                    diagnostic.stages[name] = diagnostic.stages.get(name, 0) + perf_counter() - started
        return timed
    return decorate


def record_evidence(evidence):
    diagnostic = _current.get()
    if diagnostic is not None:
        # Exact final evidence given to compose/validate, including matched text chunks.
        # Internal storage paths and ingestion lease tokens are not evaluation data.
        diagnostic.evidence = deepcopy([{k: v for k, v in item.items() if k != "crop_key"}
                                        for item in evidence])
