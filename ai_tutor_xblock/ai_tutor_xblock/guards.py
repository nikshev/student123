# impl: FR-002-02
"""
EnrollmentGuard for AI Tutor XBlock.

This guard implements the exact contract specified in the task requirements:
- Runtime check: Studio/Workbench/non-LMS runtime → deny "lms_only"
- Anonymous check: runtime.user is None → deny "anonymous"
- Missing course: runtime.course_id None/empty → deny "missing_course"
- Enrollment: runtime._is_enrolled(user, course_id) with fail-closed behavior
- Identity only from runtime (no browser-supplied arguments)
- GuardResult is a simple named structure with allow/reason only
"""

from dataclasses import dataclass


@dataclass
class GuardResult:
    """Guard result structure containing only allow and reason fields."""
    allow: bool
    reason: str


class EnrollmentGuard:
    """
    Guard that checks if a user is enrolled in a course before accessing the AI Tutor.
    
    The guard follows a strict order of checks and fails closed on any error or
    unexpected condition. Identity comes ONLY from the server runtime.
    """
    
    def __init__(self, runtime):
        """
        Initialize the guard with a runtime.
        
        Args:
            runtime: Server runtime with user, course_id, and _is_enrolled
        """
        self._runtime = runtime
    
    def check(self) -> GuardResult:
        """
        Check if the current runtime user is allowed access to the AI Tutor.
        
        Performs checks in the exact order specified:
        1. runtime check: Studio/Workbench/non-LMS runtime → deny "lms_only"
        2. anonymous check: runtime.user is None → deny "anonymous"
        3. missing course: runtime.course_id is None/empty → deny "missing_course"
        4. enrollment: runtime._is_enrolled(user, course_id) 
        
        Returns:
            GuardResult with allow=True/False and appropriate reason
        """
        runtime = self._runtime
        
        # 1. runtime check: Studio/Workbench/non-LMS runtime → deny "lms_only"
        if self._is_non_lms_runtime(runtime):
            return GuardResult(allow=False, reason="lms_only")
        
        # 2. anonymous check: runtime.user is None → deny "anonymous"
        if runtime.user is None:
            return GuardResult(allow=False, reason="anonymous")
        
        # 3. missing course: runtime.course_id is None/empty → deny "missing_course"
        if not runtime.course_id:
            return GuardResult(allow=False, reason="missing_course")
        
        # 4. enrollment: runtime._is_enrolled(user, course_id)
        try:
            user = runtime.user
            course_id = runtime.course_id
            is_enrolled = runtime._is_enrolled(user, course_id)
            
            # Fail closed on non-True result
            if not is_enrolled:
                return GuardResult(allow=False, reason="not_enrolled")
            
            # True result → allow "enrolled"
            return GuardResult(allow=True, reason="enrolled")
            
        except Exception:
            # Any exception from _is_enrolled → fail closed
            return GuardResult(allow=False, reason="fail_closed")
    
    def _is_non_lms_runtime(self, runtime):
        """
        Determine if runtime is Studio/Workbench/non-LMS.

        Recognizes non-LMS runtimes by class name (Studio/Workbench) and by
        the platform's `is_author_mode` flag that Studio sets on its runtime.
        Any unrecognized runtime is treated as LMS here; subsequent checks
        (anonymous/course/enrollment) still fail closed, so an unknown runtime
        can never silently grant access.

        Returns:
            bool: True if runtime is Studio/Workbench/non-LMS
        """
        if getattr(runtime, "is_author_mode", False):
            return True
        runtime_class_name = runtime.__class__.__name__
        return (
            "Studio" in runtime_class_name or
            "Workbench" in runtime_class_name
        )