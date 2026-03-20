import os
import struct
import unittest

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("TRANSCRIBER_PROXY_KEY", "test-proxy-key")

from proxy import (
    build_upload_filename,
    format_upstream_error,
    normalize_audio_upload,
    resolve_language,
)


class BuildUploadFilenameTests(unittest.TestCase):
    def test_preserves_existing_extension(self) -> None:
        self.assertEqual(
            build_upload_filename("meeting.webm", "audio/webm"),
            "meeting.webm",
        )

    def test_infers_extension_from_content_type(self) -> None:
        self.assertEqual(
            build_upload_filename("meeting", "audio/webm"),
            "meeting.webm",
        )

    def test_falls_back_to_wav_when_missing_metadata(self) -> None:
        self.assertEqual(build_upload_filename(None, None), "audio.wav")


class FormatUpstreamErrorTests(unittest.TestCase):
    def test_prefers_openai_error_message(self) -> None:
        self.assertEqual(
            format_upstream_error({"error": {"message": "Bad audio"}}),
            "Bad audio",
        )

    def test_falls_back_to_raw_text(self) -> None:
        self.assertEqual(format_upstream_error("plain text"), "plain text")


class ResolveLanguageTests(unittest.TestCase):
    def test_prefers_explicit_language(self) -> None:
        self.assertEqual(resolve_language("en", "ru"), "en")

    def test_falls_back_to_default_language(self) -> None:
        self.assertEqual(resolve_language(None, "ru"), "ru")

    def test_returns_none_when_language_not_set(self) -> None:
        self.assertIsNone(resolve_language(None, None))


class NormalizeAudioUploadTests(unittest.TestCase):
    def test_converts_float32_chunks_to_wav(self) -> None:
        raw = b"".join(
            struct.pack("<f", sample)
            for sample in (0.0, 0.5, -0.5, 1.0, -1.0)
        )

        filename, content, content_type = normalize_audio_upload(
            "chunk.f32",
            "application/octet-stream",
            raw,
        )

        self.assertEqual(filename, "chunk.wav")
        self.assertEqual(content_type, "audio/wav")
        self.assertTrue(content.startswith(b"RIFF"))
        self.assertIn(b"WAVE", content[:16])

    def test_keeps_regular_audio_file_unchanged(self) -> None:
        filename, content, content_type = normalize_audio_upload(
            "meeting.webm",
            "audio/webm",
            b"fake-audio",
        )

        self.assertEqual(filename, "meeting.webm")
        self.assertEqual(content_type, "audio/webm")
        self.assertEqual(content, b"fake-audio")


if __name__ == "__main__":
    unittest.main()
