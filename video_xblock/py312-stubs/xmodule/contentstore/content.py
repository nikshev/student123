"""Stub of edx-platform ``xmodule.contentstore.content.StaticContent``.

Only the class-level helpers below are referenced by the fork
(video_xblock/mixins.py:25,99,107,108,410).  The instantiation path is
always mocked in the unit suite; real behaviour requires edx-platform.
"""
# impl: FR-001-01


class StaticContent(object):
    """
    Stub of the edx-platform StaticContent model class.
    """

    @staticmethod
    def serialize_asset_key_with_slash(location):
        """
        Serialize an asset location the way edx-platform does.
        """
        return '/' + str(location)

    @staticmethod
    def get_static_path_from_location(location):
        """
        Return the storage path for an asset location.
        """
        return str(location)

    @staticmethod
    def get_location_from_path(path):
        """
        Generate an asset location from a storage path.
        """
        return path

    def compute_location(self, course_key, name):  # pylint: disable=unused-argument
        """
        edx-platform-side computation; not available in the unit environment.
        """
        raise NotImplementedError(
            'StaticContent.compute_location requires edx-platform'
        )
