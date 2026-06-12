from django.test import SimpleTestCase

from aiCore.services.circuit_breaker import CircuitBreaker, CLOSED, OPEN, HALF_OPEN


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class CircuitBreakerTest(SimpleTestCase):
    def test_starts_closed_and_allows(self):
        cb = CircuitBreaker("t", failure_threshold=3)
        self.assertEqual(cb.state, CLOSED)
        self.assertTrue(cb.allow())

    def test_opens_after_threshold_consecutive_failures(self):
        cb = CircuitBreaker("t", failure_threshold=3, reset_timeout=30)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CLOSED)  # not yet at threshold
        cb.record_failure()
        self.assertEqual(cb.state, OPEN)

    def test_open_rejects_calls_before_timeout(self):
        clock = _FakeClock()
        cb = CircuitBreaker("t", failure_threshold=1, reset_timeout=30, clock=clock)
        cb.record_failure()
        self.assertEqual(cb.state, OPEN)
        self.assertFalse(cb.allow())
        clock.advance(29)
        self.assertFalse(cb.allow())

    def test_half_open_after_reset_timeout_allows_one_trial(self):
        clock = _FakeClock()
        cb = CircuitBreaker("t", failure_threshold=1, reset_timeout=30, clock=clock)
        cb.record_failure()
        clock.advance(30)
        self.assertTrue(cb.allow())          # trial call permitted
        self.assertEqual(cb.state, HALF_OPEN)
        self.assertFalse(cb.allow())         # only one trial at a time

    def test_half_open_success_closes(self):
        clock = _FakeClock()
        cb = CircuitBreaker("t", failure_threshold=1, reset_timeout=30, clock=clock)
        cb.record_failure()
        clock.advance(30)
        cb.allow()
        cb.record_success()
        self.assertEqual(cb.state, CLOSED)
        self.assertTrue(cb.allow())

    def test_half_open_failure_reopens(self):
        clock = _FakeClock()
        cb = CircuitBreaker("t", failure_threshold=1, reset_timeout=30, clock=clock)
        cb.record_failure()
        clock.advance(30)
        cb.allow()
        cb.record_failure()
        self.assertEqual(cb.state, OPEN)
        self.assertFalse(cb.allow())  # reopened, cooldown restarts

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("t", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CLOSED)  # 2 failures, count was reset by success

    def test_reset_forces_closed(self):
        cb = CircuitBreaker("t", failure_threshold=1)
        cb.record_failure()
        self.assertEqual(cb.state, OPEN)
        cb.reset()
        self.assertEqual(cb.state, CLOSED)
        self.assertTrue(cb.allow())
