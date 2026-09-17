"""Stub of edx-platform ``common.djangoapps.util.date_utils``.

Only ``get_default_time_display`` is used by the fork
(video_xblock/mixins.py:22,104).
"""
# impl: FR-001-01


def get_default_time_display(date):
    """
    Format a datetime for display; stub mirrors edx-platform's behaviour.

    Used only to format asset metadata inside ``_get_asset_json``, which is
    not exercised by the unit suite.
    """
    if date is None:
        return None
    try:
        return date.strftime('%B %d, %Y')
    except AttributeError:
        return str(date)
