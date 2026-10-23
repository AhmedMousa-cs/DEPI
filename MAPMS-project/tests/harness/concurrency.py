from concurrent.futures import ThreadPoolExecutor
from threading import Barrier


def run_barrier_race(operation, *, participants=2, timeout=5):
    """Run operations concurrently after all participants reach a barrier."""
    if participants < 2:
        raise ValueError("participants must be at least 2")

    barrier = Barrier(participants, timeout=timeout)

    def run_one(index):
        barrier.wait()
        return operation(index)

    with ThreadPoolExecutor(max_workers=participants) as executor:
        futures = [
            executor.submit(run_one, index)
            for index in range(participants)
        ]
        return [future.result(timeout=timeout + 1) for future in futures]
