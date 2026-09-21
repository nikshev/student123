# impl: FR-002-09
"""Tracking events for AI Tutor XBlock."""


class TrackingPublisher:
    """Publishes tracking events via runtime.publish."""

    EVENT_ASKED = "xblock-ai-tutor.question.asked"
    EVENT_SHOWN = "xblock-ai-tutor.answer.shown"
    EVENT_BLOCKED = "xblock-ai-tutor.answer.blocked"

    def __init__(self, runtime, scope_ids=None):
        self.runtime = runtime
        self.scope_ids = scope_ids
        self._asked_request_ids = set()

    def build_asked_payload(self, user_id, course_id, unit_usage_key, request_id,
                            question, topic, conversation_id, config_version,
                            latency_ms=None, blocked_reason=None):
        return {
            "user_id": user_id,
            "course_id": course_id,
            "unit_usage_key": unit_usage_key,
            "request_id": request_id,
            "event_type": "asked",
            "question": question,
            "topic": topic,
            "conversation_id": conversation_id,
            "latency_ms": latency_ms,
            "blocked_reason": blocked_reason,
            "config_version": config_version,
        }

    def build_shown_payload(self, user_id, course_id, unit_usage_key, request_id,
                            question, topic, conversation_id, config_version,
                            latency_ms=0):
        return {
            "user_id": user_id,
            "course_id": course_id,
            "unit_usage_key": unit_usage_key,
            "request_id": request_id,
            "event_type": "shown",
            "question": question,
            "topic": topic,
            "conversation_id": conversation_id,
            "latency_ms": latency_ms,
            "blocked_reason": None,
            "config_version": config_version,
        }

    def build_blocked_payload(self, user_id, course_id, unit_usage_key, request_id,
                              question, topic, conversation_id, blocked_reason, config_version):
        return {
            "user_id": user_id,
            "course_id": course_id,
            "unit_usage_key": unit_usage_key,
            "request_id": request_id,
            "event_type": "blocked",
            "question": question,
            "topic": topic,
            "conversation_id": conversation_id,
            "latency_ms": None,
            "blocked_reason": blocked_reason,
            "config_version": config_version,
        }

    def publish_asked(self, user_id, request_id, course_id=None, unit_usage_key=None,
                      question=None, topic="other", conversation_id=None, config_version="unknown"):
        """Publish an asked event after validation but before service call.

        Deduplicates by request_id to avoid duplicate events.
        """
        if request_id in self._asked_request_ids:
            return
        payload = self.build_asked_payload(
            user_id=user_id,
            course_id=course_id or "",
            unit_usage_key=unit_usage_key or "",
            request_id=request_id,
            question=question or "",
            topic=topic,
            conversation_id=conversation_id,
            config_version=config_version,
        )
        self.runtime.publish(self.EVENT_ASKED, payload)
        self._asked_request_ids.add(request_id)

    def publish_shown(self, user_id, answer, question, topic, course_id=None, unit_usage_key=None,
                      request_id=None, conversation_id=None, config_version="unknown",
                      latency_ms=0):
        """Publish a shown event after successful service response."""
        payload = self.build_shown_payload(
            user_id=user_id,
            course_id=course_id or "",
            unit_usage_key=unit_usage_key or "",
            request_id=request_id,
            question=question,
            topic=topic,
            conversation_id=conversation_id,
            config_version=config_version,
            latency_ms=latency_ms,
        )
        self.runtime.publish(self.EVENT_SHOWN, payload)

    def publish_blocked(self, user_id, blocked_reason, course_id=None, unit_usage_key=None,
                        request_id=None, question=None, topic="other",
                        conversation_id=None, config_version="unknown"):
        """Publish a blocked event after service returns blocked."""
        payload = self.build_blocked_payload(
            user_id=user_id,
            course_id=course_id or "",
            unit_usage_key=unit_usage_key or "",
            request_id=request_id,
            question=question or "",
            topic=topic,
            conversation_id=conversation_id,
            blocked_reason=blocked_reason,
            config_version=config_version,
        )
        self.runtime.publish(self.EVENT_BLOCKED, payload)

    @property
    def weekly_asked_count(self):
        """Number of unique asked events published in the current week."""
        return len(self._asked_request_ids)

    def get_weekly_metric(self):
        """Get the weekly asked count metric."""
        return self.weekly_asked_count
