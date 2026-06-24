import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shared.http import DEFAULT_HEADERS, json_response


class HttpHeadersTest(unittest.TestCase):
    def test_default_cors_headers_allow_credentials_auth_and_cookie_headers(self):
        allowed_headers = {
            value.strip().lower()
            for value in DEFAULT_HEADERS["Access-Control-Allow-Headers"].split(",")
        }

        self.assertEqual(DEFAULT_HEADERS["Access-Control-Allow-Credentials"], "true")
        self.assertIn("authorization", allowed_headers)
        self.assertIn("content-type", allowed_headers)
        self.assertIn("cookie", allowed_headers)
        self.assertIn("x-csrf-token", allowed_headers)

    def test_response_uses_matching_allowed_origin_and_vary_header(self):
        with patch.dict(
            os.environ,
            {"CORS_ALLOW_ORIGINS": "http://localhost:5173,https://lovv.example.com"},
            clear=False,
        ):
            response = json_response(
                200,
                {"ok": True},
                event={"headers": {"Origin": "https://lovv.example.com"}},
            )

        self.assertEqual(response["headers"]["Access-Control-Allow-Origin"], "https://lovv.example.com")
        self.assertEqual(response["headers"]["Access-Control-Allow-Credentials"], "true")
        self.assertEqual(response["headers"]["Vary"], "Origin")

    def test_response_does_not_reflect_disallowed_origin(self):
        with patch.dict(
            os.environ,
            {"CORS_ALLOW_ORIGINS": "http://localhost:5173,https://lovv.example.com"},
            clear=False,
        ):
            response = json_response(
                200,
                {"ok": True},
                event={"headers": {"Origin": "https://attacker.example"}},
            )

        self.assertNotIn("Access-Control-Allow-Origin", response["headers"])


if __name__ == "__main__":
    unittest.main()
