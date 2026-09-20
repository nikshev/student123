# verifies: FR-002-02
"""
EnrollmentGuard tests for AI Tutor XBlock (FR-002-02).

Contract for T-024 (contracts/xblock-interface.md §1, §6 — the implementation
must satisfy exactly this interface):

    EnrollmentGuard(runtime).check() -> GuardResult

    GuardResult is a structured result with at least:
        allow: bool
        reason: str  (one of "enrolled", "lms_only", "anonymous",
                      "not_enrolled", "fail_closed", "missing_course")

    check() performs the guards strictly in this order and returns on the
    first deny (so downstream checks never run for early denials):
        1. runtime check: Studio/Workbench/non-LMS runtime -> deny "lms_only";
        2. anonymous check: runtime.user is None (or user is anonymous) ->
           deny "anonymous";
        3. enrollment check: runtime._is_enrolled(user, course_id); a missing
           course_id (None/empty) -> deny "missing_course" BEFORE calling
           _is_enrolled; any exception or non-true result -> deny (fail-closed).

    Identity comes ONLY from the server runtime (runtime.user, runtime.course_id).
    The guard takes no browser-supplied identity and the result exposes no
    service URL/config: callers must not reach TutorServiceClient or
    TrackingPublisher when result.allow is False.

The tests are red right now for the expected reason: the module
ai_tutor_xblock.ai_tutor_xblock.guards does not exist yet (T-024 creates it),
so collection fails with ModuleNotFoundError.
"""

import pytest

from ai_tutor_xblock.ai_tutor_xblock.guards import EnrollmentGuard


class StubUser:
    """Server-derived platform user; only `.id` matters to the guard."""

    def __init__(self, user_id="student-1"):
        self.id = user_id


class LMSRuntime:
    """LMS-shaped runtime stub: has user, course_id and _is_enrolled."""

    def __init__(self, user=StubUser(), course_id="course-v1:demo+math+2026"):
        self.user = user
        self.course_id = course_id
        self.enroll_calls = []

    def _is_enrolled(self, user, course_id):
        self.enroll_calls.append((user, course_id))
        return True


class StudioRuntime(LMSRuntime):
    pass


class WorkbenchRuntime(LMSRuntime):
    pass


@pytest.fixture
def lms_runtime():
    return LMSRuntime()


class TestEnrollmentGuard:
    """Guard contract and fail-closed behavior per FR-002-02."""

    def test_lms_enrolled_allows_access(self, lms_runtime):
        result = EnrollmentGuard(lms_runtime).check()
        assert result.allow is True
        assert result.reason == "enrolled"
        assert lms_runtime.enroll_calls == [
            (lms_runtime.user, lms_runtime.course_id)
        ], "_is_enrolled мав отримати серверні user/course з runtime"

    def test_studio_runtime_denies_without_is_enrolled_call(self):
        runtime = StudioRuntime()
        result = EnrollmentGuard(runtime).check()
        assert result.allow is False
        assert result.reason == "lms_only"
        assert runtime.enroll_calls == [], "Studio: _is_enrolled не має викликатись"

    def test_workbench_runtime_denies_without_is_enrolled_call(self):
        runtime = WorkbenchRuntime()
        result = EnrollmentGuard(runtime).check()
        assert result.allow is False
        assert result.reason == "lms_only"
        assert runtime.enroll_calls == [], "Workbench: _is_enrolled не має викликатись"

    def test_anonymous_user_denies_before_is_enrolled(self):
        runtime = LMSRuntime(user=None)
        result = EnrollmentGuard(runtime).check()
        assert result.allow is False
        assert result.reason == "anonymous"
        assert runtime.enroll_calls == [], "анонім відхиляється до _is_enrolled"

    def test_not_enrolled_denies(self):
        runtime = LMSRuntime()
        runtime._is_enrolled = lambda user, course_id: (
            runtime.enroll_calls.append((user, course_id)) or False
        )
        result = EnrollmentGuard(runtime).check()
        assert result.allow is False
        assert result.reason == "not_enrolled"
        assert runtime.enroll_calls == [(runtime.user, runtime.course_id)]

    def test_is_enrolled_exception_fails_closed(self):
        runtime = LMSRuntime()
        runtime._is_enrolled = lambda user, course_id: (
            runtime.enroll_calls.append((user, course_id))
            or (_ for _ in ()).throw(RuntimeError("DB down"))
        )
        result = EnrollmentGuard(runtime).check()
        assert result.allow is False
        assert result.reason == "fail_closed"

    @pytest.mark.parametrize("exc", [RuntimeError("db"), ValueError("course"), TypeError("type")])
    def test_any_enrollment_exception_fails_closed(self, exc):
        runtime = LMSRuntime()

        def boom(user, course_id):
            raise exc

        runtime._is_enrolled = boom
        result = EnrollmentGuard(runtime).check()
        assert result.allow is False
        assert result.reason == "fail_closed"

    def test_missing_course_id_denies_before_is_enrolled(self):
        runtime = LMSRuntime(course_id=None)
        result = EnrollmentGuard(runtime).check()
        assert result.allow is False
        assert result.reason == "missing_course"
        assert runtime.enroll_calls == [], "без course_id _is_enrolled не викликається"

    def test_empty_course_id_denies(self):
        runtime = LMSRuntime(course_id="")
        result = EnrollmentGuard(runtime).check()
        assert result.allow is False
        assert result.reason == "missing_course"
        assert runtime.enroll_calls == []

    def test_identity_comes_only_from_runtime(self, lms_runtime):
        """Browser identity is never a guard input: check() takes no identity args."""
        guard = EnrollmentGuard(lms_runtime)
        with pytest.raises(TypeError):
            guard.check(
                user_id="browser-user",
                course_id="browser-course",
            )
        with pytest.raises(TypeError):
            guard.check("browser-course")

    def test_guard_order_runtime_then_anonymous_then_enrollment(self):
        """The enrollment adapter may only run after runtime+anonymous pass."""
        runtime = LMSRuntime()
        runtime.user = None
        order = []

        def spy(user, course_id):
            order.append("enroll")
            return True

        runtime._is_enrolled = spy
        EnrollmentGuard(runtime).check()
        assert order == [], (
            "порядок порушено: _is_enrolled викликано для аноніма "
            "(мало бути відхилено на етапі anonymous)"
        )

        runtime.user = StubUser()
        result = EnrollmentGuard(runtime).check()
        assert result.allow is True
        assert order == ["enroll"]

    def test_deny_result_exposes_no_service_url_or_config(self, lms_runtime):
        """Denied result carries no service URL/config/client/publish payload."""
        lms_runtime.user = None
        result = EnrollmentGuard(lms_runtime).check()
        assert result.allow is False
        for forbidden in ("service_url", "config", "client", "published_event"):
            assert not hasattr(result, forbidden), (
                f"deny-результат не має містити {forbidden}"
            )
