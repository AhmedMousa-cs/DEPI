"""Deterministic tests: critical path, float, total duration."""
import random
from datetime import date
from functools import lru_cache

import pytest

from app.schemas.scheduling import TaskInput
from app.tools.compute_dates import compute_dates

SUN = date(2026, 11, 15)


def T(id, d, deps=(), **kw):
    return TaskInput(id=id, duration_days=d, depends_on=deps, **kw)


def test_diamond():
    # A(2) -> B(3) -> D(1)   and   A -> C(1) -> D
    r = compute_dates([T("A", 2), T("B", 3, ["A"]), T("C", 1, ["A"]), T("D", 1, ["B", "C"])], SUN)
    t = {x.id: x for x in r.tasks}
    assert r.total_duration_days == 6
    assert r.critical_path == ("A", "B", "D")
    assert set(r.critical_task_ids) == {"A", "B", "D"}
    assert t["C"].total_float == 2 and not t["C"].is_critical
    assert (t["C"].es, t["C"].ls) == (2, 4)


def test_float_of_independent_tasks():
    r = compute_dates([T("A", 3), T("B", 4), T("C", 2)], SUN)
    t = {x.id: x for x in r.tasks}
    assert (t["A"].total_float, t["B"].total_float, t["C"].total_float) == (1, 0, 2)
    assert r.critical_path == ("B",)


def test_two_equal_chains_are_both_critical():
    r = compute_dates([T("A", 2), T("B", 2), T("C", 1, ["A", "B"])], SUN)
    assert set(r.critical_task_ids) == {"A", "B", "C"}
    assert r.critical_path == ("A", "C")  # deterministic tie-break: first in input order


def test_milestone_in_critical_chain():
    r = compute_dates([T("A", 2), T("M", 0, ["A"]), T("B", 3, ["M"])], SUN)
    assert r.critical_path == ("A", "M", "B") and r.total_duration_days == 5


def test_not_before_makes_task_critical():
    r = compute_dates([T("X", 2, not_before=date(2026, 11, 22)), T("Y", 3)], SUN)
    t = {x.id: x for x in r.tasks}
    assert r.total_duration_days == 7  # 5 idle working days + 2
    assert t["X"].is_critical and t["Y"].total_float == 4
    assert r.critical_path == ("X",)


def test_single_task_is_critical():
    r = compute_dates([T("A", 5)], SUN)
    assert r.critical_path == ("A",) and r.tasks[0].total_float == 0


def test_all_zero_duration():
    r = compute_dates([T("A", 0), T("B", 0, ["A"])], SUN)
    assert r.total_duration_days == 0 and r.critical_path == ("A", "B")


def test_critical_path_duration_equals_project_duration():
    r = compute_dates([T("A", 2), T("B", 3, ["A"]), T("C", 4, ["B"]), T("S", 1)], SUN)
    by = {x.id: x for x in r.tasks}
    assert sum(by[i].duration_days for i in r.critical_path) == r.total_duration_days == 9


def _random_dag(rng: random.Random, n: int):
    tasks = []
    for i in range(n):
        deps = [f"t{j}" for j in range(i) if rng.random() < 0.25]
        tasks.append(T(f"t{i}", rng.randint(0, 6), deps))
    rng.shuffle(tasks)  # input order must not matter
    return tasks


@pytest.mark.parametrize("seed", range(25))
def test_random_dags_match_bruteforce_longest_path(seed):
    rng = random.Random(seed)
    tasks = _random_dag(rng, rng.randint(1, 18))
    by = {t.id: t for t in tasks}

    @lru_cache(None)
    def longest_to(i):  # longest path ending at i (inclusive)
        return by[i].duration_days + max((longest_to(d) for d in by[i].depends_on), default=0)

    expected = max(longest_to(i) for i in by)
    r = compute_dates(tasks, SUN)
    s = {x.id: x for x in r.tasks}

    assert r.total_duration_days == expected
    for x in r.tasks:
        assert x.total_float >= 0
        assert x.ef == x.es + x.duration_days and x.lf == x.ls + x.duration_days
        for d in by[x.id].depends_on:
            assert x.es >= s[d].ef  # dependencies respected
    assert any(x.is_critical for x in r.tasks)
    # the representative chain is contiguous and spans the whole project
    chain = [s[i] for i in r.critical_path]
    assert chain[0].es == 0 and chain[-1].ef == expected
    for a, b in zip(chain, chain[1:]):
        assert a.id in by[b.id].depends_on and a.ef == b.es
