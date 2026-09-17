# trace: ignore-file — записані відповіді Bunny API (FR-001-05, FR-001-09) є
# тестовими даними, а не ланками ланцюга трасування.
"""
Recorded response for a rejected embed player request (bunny-api.md §5):
403 means an invalid/expired token, missing parameters, or a Referer outside
the allowed domains.
"""

EMBED_403 = {
    "status_code": 403,
    "body": {"message": "Forbidden"},
}
