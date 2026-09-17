"""Stub of edx-platform ``openedx.core.djangoapps.site_configuration.helpers``.

Only ``configuration_helpers.get_value`` is used by the fork
(video_xblock/mixins.py:24,100).
"""
# impl: FR-001-01


def get_value(name, default=None):
    """
    Return ``default`` for any configuration value; there is no real
    edx-platform configuration in the standalone unit-test environment.
    """
    return default
