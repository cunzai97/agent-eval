import pytest

from server.scheduler import Scheduler
from speculative.worker import SpeculativeWorker


def test_cold_prompt_uses_absolute_positions_then_local_generation():
    worker = SpeculativeWorker()
    server = Scheduler(worker)
    server.start_prompt(0, 0, [[0, 1], [2, 3]])
    assert worker.states[0].drained_positions == [0, 1, 2, 3]
    assert worker.states[0].mode == "generation_local"
    server.generate(0, 3)
    assert worker.states[0].verify_rows == 3


def test_checkpoint_resumed_prompt_explicitly_reenters_absolute_mode():
    worker = SpeculativeWorker()
    server = Scheduler(worker)
    server.start_prompt(0, 0, [[0, 1]])
    server.generate(0, 2)
    server.start_prompt(0, 4096, [[4096, 4097], [4098]])
    assert worker.states[0].prompt_start == 4096
    assert worker.states[0].drained_positions[-3:] == [4096, 4097, 4098]
    server.generate(0, 2)


def test_prompt_begin_drains_stale_jobs_before_reusing_buffers():
    worker = SpeculativeWorker()
    worker.prompt_begin(0, 0)
    worker.enqueue_prompt(0, [0, 1])
    worker.prompt_begin(0, 100)
    assert worker.states[0].queued_positions == []
    assert worker.states[0].drained_positions == [0, 1]


def test_new_request_clears_failure_and_verification_state():
    worker = SpeculativeWorker()
    worker.prompt_begin(0, 0)
    worker.prompt_finish(0)
    worker.verify_generation(0, [0, 1, 2])
    worker.mark_failed(0)
    worker.prompt_begin(0, 8000)
    state = worker.states[0]
    assert state.failed is False
    assert state.verify_rows == 0
    assert state.mode == "prompt_absolute"


def test_finish_is_the_only_transition_to_generation_local():
    worker = SpeculativeWorker()
    worker.prompt_begin(0, 50)
    worker.enqueue_prompt(0, [50, 51])
    with pytest.raises(RuntimeError):
        worker.verify_generation(0, [0])
    worker.prompt_finish(0)
    worker.verify_generation(0, [0])


def test_cancel_clears_all_state_for_only_target_sequence():
    worker = SpeculativeWorker()
    worker.prompt_begin(0, 0)
    worker.enqueue_prompt(0, [0])
    worker.mark_failed(0)
    worker.prompt_begin(1, 700)
    worker.enqueue_prompt(1, [700])
    worker.cancel(0)
    assert worker.states[0].mode == "idle"
    assert worker.states[0].queued_positions == []
    assert worker.states[0].failed is False
    assert worker.states[0].verify_rows == 0
    assert worker.states[1].mode == "prompt_absolute"
    assert worker.states[1].queued_positions == [700]


def test_two_sequences_can_resume_at_different_absolute_positions():
    worker = SpeculativeWorker()
    server = Scheduler(worker)
    server.start_prompt(0, 1024, [[1024, 1025]])
    server.start_prompt(1, 8192, [[8192], [8193]])
    assert worker.states[0].drained_positions == [1024, 1025]
    assert worker.states[1].drained_positions == [8192, 8193]


def test_second_prompt_does_not_accumulate_previous_verify_rows():
    worker = SpeculativeWorker()
    server = Scheduler(worker)
    server.start_prompt(0, 0, [[0]])
    server.generate(0, 4)
    server.start_prompt(0, 200, [[200]])
    server.generate(0, 1)
    assert worker.states[0].verify_rows == 1


def test_generation_indices_are_always_local_even_after_large_restore():
    worker = SpeculativeWorker()
    server = Scheduler(worker)
    server.start_prompt(0, 63000, [[63000, 63001]])
    server.generate(0, 2)
    with pytest.raises(RuntimeError):
        worker.verify_generation(0, [63002])
