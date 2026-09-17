# trace: ignore-file — записані відповіді Bunny API (FR-001-05, FR-001-09) є
# тестовими даними, а не ланками ланцюга трасування.
"""
Recorded responses for ``POST /library/{libraryId}/videos`` (bunny-api.md §1).
"""

CREATE_VIDEO_200 = {
    "status_code": 200,
    "body": {
        "guid": "32d140e2-e4f4-4eec-9d53-20371e9be607",
        "videoLibraryId": 759,
        "title": "Лекція 1",
        "dateUploaded": "2026-09-17T12:00:00.000Z",
    },
}

CREATE_VIDEO_401 = {
    "status_code": 401,
    "body": {
        "message": "Unauthorized",
        "code": "access_denied",
    },
}
