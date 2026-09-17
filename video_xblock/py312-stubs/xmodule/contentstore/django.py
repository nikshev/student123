"""Stub of edx-platform ``xmodule.contentstore.django``.

Only the ``contentstore`` accessor is referenced by the fork
(video_xblock/video_xblock.py:29,794) inside handlers that are never
exercised by the unit suite.
"""
# impl: FR-001-01


def contentstore():
    """
    Return the edx-platform contentstore; requires edx-platform to run.
    """
    raise NotImplementedError(
        'contentstore() requires edx-platform'
    )
