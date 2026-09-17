# verifies: FR-001-05
"""Offline polling tests through the public JSON handler (contract §2.3).

T-014 stops at the missing video_info handler; T-015 supplies production code.
Recorded API bodies retain their raw statuses: only the handler maps metadata.
"""

import json
from contextlib import ExitStack
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml
from xblock.field_data import DictFieldData
from xblock.test.tools import TestRuntime

from video_xblock.backends import bunny
from video_xblock.bunny_config import DEFAULT_CONFIG_PATH
from video_xblock.tests.fixtures.bunny.create_video import CREATE_VIDEO_200
from video_xblock.tests.fixtures.bunny.delete_video import DELETE_VIDEO_200
from video_xblock.tests.fixtures.bunny.video_info import VIDEO_INFO_STATUSES
from video_xblock.tests.unit.base import arrange_request_mock
from video_xblock.video_xblock import VideoXBlock


@pytest.fixture
def polling():
    """Fresh metadata, config and client per case; all patches unwind on failure."""
    with ExitStack() as patches:
        for method in ("request", "send"):
            patches.enter_context(patch(
                "requests.sessions.Session." + method,
                autospec=True,
                side_effect=AssertionError("Network access is forbidden in unit tests"),
            ))

        with DEFAULT_CONFIG_PATH.open(encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
        # Nondefault sentinel test input, not a replacement production limit.
        config["max_duration_seconds"] = 947
        patches.enter_context(patch.object(bunny, "load_bunny_config", return_value=config))
        client_class = patches.enter_context(patch.object(bunny, "BunnyApiClient"))
        client = client_class.return_value
        client_class.from_settings.return_value = client
        created = deepcopy(CREATE_VIDEO_200["body"])
        client.library_id = created["videoLibraryId"]
        client.delete_video.side_effect = lambda video_id: deepcopy(DELETE_VIDEO_200["body"])

        xblock = VideoXBlock(
            TestRuntime(),
            DictFieldData({
                "account_id": "account_id",
                "metadata": {
                    "bunny_video_id": created["guid"],
                    "bunny_library_id": client.library_id,
                    "bunny_status": "UPLOADING",
                    "bunny_length_seconds": None,
                    "bunny_title": created["title"],
                    "source_type": "bunny",
                    "token_protected": True,
                    "config_version": config["version"],
                },
            }),
            scope_ids=Mock(spec=[]),
        )
        yield SimpleNamespace(
            xblock=xblock, config=config, client=client, video_id=created["guid"],
        )


@pytest.mark.parametrize("status, expected_state", [
    pytest.param(0, "UPLOADING", id="0-UPLOADING"),
    pytest.param(1, "UPLOADING", id="1-UPLOADING"),
    pytest.param(2, "PROCESSING", id="2-PROCESSING"),
    pytest.param(3, "PROCESSING", id="3-PROCESSING"),
    pytest.param(4, "READY", id="4-READY"),
    pytest.param(5, "ERROR", id="5-ERROR"),
    pytest.param(6, "ERROR", id="6-ERROR"),
])
def test_video_info_maps_recorded_status_to_metadata(polling, status, expected_state):
    polling.client.get_video_info.side_effect = lambda video_id: deepcopy(
        VIDEO_INFO_STATUSES[status]["body"]
    )
    # Start in a different valid state so even UPLOADING must be written back.
    polling.xblock.metadata["bunny_status"] = (
        "PROCESSING" if status in (0, 1) else "UPLOADING"
    )
    player = bunny.BunnyPlayer(polling.xblock)
    request = arrange_request_mock(json.dumps({"video_id": polling.video_id}))

    response = player.video_info(request)

    assert response.status_int == 200
    body = json.loads(response.body)
    assert type(body["status"]) is int
    assert body["status"] == status
    assert polling.xblock.metadata["bunny_status"] == expected_state
    if status == 4:
        assert body["length"] == 612.5
        assert polling.xblock.metadata["bunny_length_seconds"] == 612.5
        polling.client.delete_video.assert_not_called()
    else:
        assert "length" not in body
        assert polling.xblock.metadata.get("bunny_length_seconds") is None
    polling.client.get_video_info.assert_called_once_with(polling.video_id)


@pytest.mark.parametrize("max_duration_seconds", [600, 612])
def test_video_info_deletes_ready_video_over_injected_duration_limit(polling, max_duration_seconds):
    # Keep the recording intact: both limits reject 612.5 despite the YAML
    # default accepting it. 612 is the closest valid integer limit below it.
    polling.config["max_duration_seconds"] = max_duration_seconds
    polling.client.get_video_info.side_effect = lambda video_id: deepcopy(
        VIDEO_INFO_STATUSES[4]["body"]
    )
    player = bunny.BunnyPlayer(polling.xblock)
    request = arrange_request_mock(json.dumps({"video_id": polling.video_id}))

    player.video_info(request)

    # The over-limit HTTP response is unspecified; assert only its effects.
    assert polling.xblock.metadata["bunny_status"] == "ERROR"
    polling.client.get_video_info.assert_called_once_with(polling.video_id)
    polling.client.delete_video.assert_called_once_with(polling.video_id)


def test_video_info_accepts_ready_video_below_injected_duration_limit(polling):
    # The schema requires integer limits; 613 is the nearest allowed limit
    # above the recorded 612.5 seconds. No synthetic fixture length is needed.
    polling.config["max_duration_seconds"] = 613
    polling.client.get_video_info.side_effect = lambda video_id: deepcopy(
        VIDEO_INFO_STATUSES[4]["body"]
    )
    player = bunny.BunnyPlayer(polling.xblock)
    request = arrange_request_mock(json.dumps({"video_id": polling.video_id}))

    response = player.video_info(request)

    assert response.status_int == 200
    body = json.loads(response.body)
    assert type(body["status"]) is int
    assert body["status"] == 4
    assert body["length"] == 612.5
    assert polling.xblock.metadata["bunny_status"] == "READY"
    assert polling.xblock.metadata["bunny_length_seconds"] == 612.5
    polling.client.get_video_info.assert_called_once_with(polling.video_id)
    polling.client.delete_video.assert_not_called()
