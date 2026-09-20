# impl: FR-002-02
"""
AI Tutor XBlock - minimal skeleton that uses EnrollmentGuard as a student gate.
"""

from xblock.core import XBlock
from xblock.fields import Scope, String
from xblock.fragment import Fragment
from .guards import EnrollmentGuard, GuardResult


class AiTutorXBlock(XBlock):
    """
    Minimal AI Tutor XBlock skeleton that uses EnrollmentGuard as the first
    gate for student handlers and views.
    """
    
    # Fields (minimal set, can be expanded in later tasks)
    display_name = String(
        display_name="Component Display Name",
        default="AI Tutor",
        scope=Scope.settings,
        help="This name appears in the horizontal navigation at the top of the page."
    )
    
    def _check_enrollment(self) -> GuardResult:
        """
        Helper that checks enrollment using EnrollmentGuard as the student gate.
        
        This is the first gate that student handlers (ask/history) and student
        view must pass. Studio view remains author-only via guard's "lms_only"
        deny for Studio runtime.
        
        Returns:
            GuardResult indicating whether enrollment check passes
        """
        guard = EnrollmentGuard(self.runtime)
        return guard.check()
    
    # TODO: Implement student_view, studio_view, and handlers in subsequent tasks
    # T-025: ask/history handlers
    # T-026: history handler
    # T-027: student_view JS tests
    # T-028: student view implementation
    # T-029: Studio view tests
    # T-030: Studio view implementation
    
    # Placeholder methods to satisfy XBlock interface (will be implemented later)
    def student_view(self, context=None):
        """Student view - to be implemented in T-028."""
        # TODO: Implement proper student view that calls _check_enrollment() first
        # For now return a fragment indicating this is a skeleton
        frag = Fragment()
        frag.add_content("<p>AI Tutor XBlock - Student View (skeleton)</p>")
        return frag
    
    def studio_view(self, context=None):
        """Studio view - to be implemented in T-030."""
        # TODO: Implement proper studio view (author-only)
        # The EnrollmentGuard will deny Studio runtime with "lms_only" before
        # reaching any student path, but Studio view itself needs author check
        frag = Fragment()
        frag.add_content("<p>AI Tutor XBlock - Studio View (skeleton)</p>")
        return frag