# trace: ignore-file — записані відповіді Bunny API (FR-001-05, FR-001-09) є
# тестовими даними, а не ланками ланцюга трасування.
"""
Recorded responses for ``DELETE /library/{libraryId}/videos/{videoId}``
(bunny-api.md §4). A repeated DELETE of a missing video (404) is treated as
idempotent success.
"""

DELETE_VIDEO_200 = {
    "status_code": 200,
    "body": {"success": True, "message": "OK"},
}

DELETE_VIDEO_404 = {
    "status_code": 404,
    "body": {"success": False, "message": "Video not found"},
}
