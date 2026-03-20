import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app


class CreateCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app.app)

    def test_reuses_existing_capture_on_conflict(self) -> None:
        conflict = HTTPException(
            status_code=409,
            detail=(
                "An active or requested meeting already exists for this platform and "
                "meeting ID. Platform: google_meet, Native Meeting ID: thj-kync-rih"
            ),
        )
        transcript_payload = {
            "platform": "google_meet",
            "native_meeting_id": "thj-kync-rih",
            "status": "requested",
            "segments": [],
        }

        async def vexa_side_effect(method, path, json_body=None):
            if method == "POST" and path == "/bots":
                raise conflict
            if method == "GET" and path == "/transcripts/google_meet/thj-kync-rih":
                return transcript_payload
            if method == "PUT" and path == "/bots/google_meet/thj-kync-rih/avatar":
                return {}
            raise AssertionError(f"Unexpected call: {method} {path}")

        with patch.object(app, "vexa_request", AsyncMock(side_effect=vexa_side_effect)):
            response = self.client.post(
                "/api/captures",
                data={"meeting_url": "https://meet.google.com/thj-kync-rih"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["reused"])
        self.assertEqual(payload["created"]["status"], "requested")
        self.assertEqual(payload["transcript"]["native_meeting_id"], "thj-kync-rih")

    def test_returns_friendly_message_when_capture_not_found(self) -> None:
        missing = HTTPException(
            status_code=404,
            detail="No active meeting found for google_meet/abc-defg-hij",
        )

        with patch.object(app, "vexa_request", AsyncMock(side_effect=missing)):
            response = self.client.get("/api/captures/google_meet/abc-defg-hij")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"],
            "No capture found for this meeting yet. Send the bot to a real active meeting link first.",
        )


if __name__ == "__main__":
    unittest.main()
