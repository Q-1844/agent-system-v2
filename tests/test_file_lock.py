"""Real process contention, including Windows locking beyond an empty file."""
import multiprocessing
from pathlib import Path
import time

import pytest

from src.core.file_lock import file_transaction


def test_empty_lock_supports_nested_transactions_and_exception_release(tmp_path):
    path = tmp_path / "transaction.lock"
    with pytest.raises(RuntimeError, match="transaction failed"):
        with file_transaction(path):
            with file_transaction(path):
                assert path.stat().st_size == 0
            raise RuntimeError("transaction failed")
    with file_transaction(path, timeout=0.1):
        assert path.stat().st_size == 0


def _contend_on_empty_files(root, barrier, results):
    root = Path(root)
    for index in range(12):
        path = root / f"{index}.lock"
        counter = root / f"{index}.count"
        barrier.wait(timeout=15)
        with file_transaction(path, timeout=5):
            value = int(counter.read_text()) if counter.exists() else 0
            # Without the process lock both workers can read the same value.
            time.sleep(0.005)
            counter.write_text(str(value + 1))
        barrier.wait(timeout=15)
    results.put(True)


def test_empty_lock_contention_serializes_processes(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(2), ctx.Queue()
    workers = [ctx.Process(target=_contend_on_empty_files,
                           args=(str(tmp_path), barrier, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        assert [results.get(timeout=15) for _ in workers] == [True, True]
        for worker in workers:
            worker.join(15)
            assert worker.exitcode == 0
        for index in range(12):
            assert (tmp_path / f"{index}.count").read_text() == "2"
            # Regression: no initialization write may race with a Windows lock.
            assert (tmp_path / f"{index}.lock").stat().st_size == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
        results.close()
