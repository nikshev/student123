# trace: ignore-file — записані відповіді Bunny API (FR-001-05, FR-001-09) є
# тестовими даними, а не ланками ланцюга трасування.
"""
Recorded responses for ``GET /library/{libraryId}/videos/{videoId}``
(bunny-api.md §3).

Statuses 0-6 follow research R12: 0 Created, 1 Uploaded, 2 Processing,
3 Transcoding, 4 Finished (with ``length``), 5 Error, 6 UploadFailed.
"""

GUID = "32d140e2-e4f4-4eec-9d53-20371e9be607"
TITLE = "Лекція 1"

VIDEO_INFO_STATUSES = {
    0: {
        "status_code": 200,
        "body": {"guid": GUID, "status": 0, "length": None, "title": TITLE},
    },
    1: {
        "status_code": 200,
        "body": {"guid": GUID, "status": 1, "length": None, "title": TITLE},
    },
    2: {
        "status_code": 200,
        "body": {"guid": GUID, "status": 2, "length": None, "title": TITLE},
    },
    3: {
        "status_code": 200,
        "body": {"guid": GUID, "status": 3, "length": None, "title": TITLE},
    },
    4: {
        "status_code": 200,
        "body": {"guid": GUID, "status": 4, "length": 612.5, "title": TITLE},
    },
    5: {
        "status_code": 200,
        "body": {"guid": GUID, "status": 5, "length": None, "title": TITLE},
    },
    6: {
        "status_code": 200,
        "body": {"guid": GUID, "status": 6, "length": None, "title": TITLE},
    },
}

VIDEO_INFO_404 = {
    "status_code": 404,
    "body": {
        "message": "Video not found",
        "code": "not_found",
    },
}
