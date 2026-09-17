"""Stub of edx-platform ``openedx.core.djangoapps.contentserver.middleware``.

Only ``StaticContentServer`` is referenced by the fork
(video_xblock/mixins.py:23,411); it backs the ``srt_to_vtt`` handler,
which the unit suite exercises with this class mocked.
"""
# impl: FR-001-01


class StaticContentServer(object):
    """
    Stub of the edx-platform static content server middleware.
    """

    def load_asset_from_location(self, location):  # pylint: disable=unused-argument
        """
        Asset loading requires a running edx-platform; not available here.
        """
        raise NotImplementedError(
            'StaticContentServer.load_asset_from_location requires edx-platform'
        )
