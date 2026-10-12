from agent_tools.chair_runnable import Blocking, Runnable, TaskView, WaitingInitiative, runnable_verdict

PHASES = ("p1", "p2")


def test_ready_behind_quarantined_phase_is_blocked():
    tasks = (TaskView("a", "p1", "quarantined"), TaskView("b", "p2", "ready"))
    assert runnable_verdict(tasks, PHASES) == Blocking("p1", 1, 0)


def test_ready_behind_approved_phase_counts_awaiting_land():
    tasks = (TaskView("a", "p1", "approved"), TaskView("b", "p2", "ready"))
    assert runnable_verdict(tasks, PHASES) == Blocking("p1", 0, 1)


def test_ready_in_first_phase_with_needs_done_is_runnable():
    tasks = (TaskView("a", "p1", "done"), TaskView("b", "p1", "ready", ("a",)))
    assert runnable_verdict(tasks, PHASES) == Runnable(("b",))


def test_ready_with_needs_not_done_is_not_runnable():
    tasks = (TaskView("a", "p1", "approved"), TaskView("b", "p1", "ready", ("a",)))
    assert runnable_verdict(tasks, PHASES) == Blocking("p1", 0, 1)
    assert runnable_verdict((TaskView("b", "p1", "ready", ("a",)),), PHASES) == Runnable(())


def test_ready_after_all_done_phase_is_runnable():
    tasks = (TaskView("a", "p1", "done"), TaskView("b", "p2", "ready"))
    assert runnable_verdict(tasks, PHASES) == Runnable(("b",))


def test_no_ready_and_no_blocked_phase_invents_no_blocking():
    tasks = (TaskView("a", "p1", "done"), TaskView("b", "p2", "draft"))
    assert runnable_verdict(tasks, PHASES) == Runnable(())


def test_blocking_in_same_phase_does_not_block_ready_task():
    tasks = (TaskView("a", "p1", "quarantined"), TaskView("b", "p1", "ready"))
    assert runnable_verdict(tasks, PHASES) == Runnable(("b",))


def test_earliest_blocking_phase_is_named_with_both_counts():
    tasks = (
        TaskView("a", "p2", "quarantined"),
        TaskView("b", "p1", "quarantined"),
        TaskView("c", "p1", "approved"),
        TaskView("d", "p1", "approved"),
    )
    assert runnable_verdict(tasks, PHASES) == Blocking("p1", 1, 2)


def test_waiting_initiative_carries_blocking():
    assert WaitingInitiative("init", Blocking("p1", 1, 0)).blocking.phase == "p1"
