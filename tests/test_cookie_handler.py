"""Cookie attribute regressions using synthetic data and no external requests.

The unit tests need only the standard library. The optional browser round trip
uses an installed Chromium; set PLAYWRIGHT_BROWSERS_PATH=.venv/browsers or
PLAYWRIGHT_CHROMIUM_EXECUTABLE when needed.
"""

import json
import os
import time
import unittest
from unittest.mock import Mock

from utils.cookie_handler import (
    auto_convert_to_playwright,
    convert_cookie_editor_to_playwright,
    convert_kv_to_playwright,
)

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def fixture_cookie(**attributes):
    cookie = {
        "name": "fixture-cookie",
        "value": "synthetic-test-value",
        "domain": "cookies.invalid",
        "path": "/",
        "httpOnly": True,
        "secure": True,
    }
    cookie.update(attributes)
    return cookie


class CookieAttributeTests(unittest.TestCase):
    def test_none_policy_is_preserved_for_both_json_export_formats(self):
        for policy in ("None", "none", "no_restriction"):
            with self.subTest(policy=policy):
                converted = convert_cookie_editor_to_playwright([
                    fixture_cookie(sameSite=policy)
                ])
                self.assertEqual(converted[0]["sameSite"], "None")
                self.assertTrue(converted[0]["secure"])

    def test_existing_same_site_policies_remain_supported(self):
        for policy, expected in (("lax", "Lax"), ("strict", "Strict"), ("unspecified", "Lax")):
            with self.subTest(policy=policy):
                converted = convert_cookie_editor_to_playwright([
                    fixture_cookie(sameSite=policy)
                ])
                self.assertEqual(converted[0]["sameSite"], expected)

    def test_null_same_site_remains_unspecified(self):
        converted = convert_cookie_editor_to_playwright([fixture_cookie(sameSite=None)])
        self.assertNotIn("sameSite", converted[0])

    def test_playwright_json_expiry_is_preserved_without_truncation(self):
        expiry = 2_000_000_000.25
        exported_json = json.dumps([fixture_cookie(sameSite="None", expires=expiry)])
        converted = auto_convert_to_playwright(json.loads(exported_json))
        self.assertEqual(converted[0]["expires"], expiry)
        self.assertEqual(converted[0]["sameSite"], "None")
        self.assertEqual(converted[0]["domain"], "cookies.invalid")
        self.assertEqual(converted[0]["path"], "/")
        self.assertTrue(converted[0]["httpOnly"])

    def test_cookie_editor_expiration_date_remains_supported(self):
        converted = convert_cookie_editor_to_playwright([
            fixture_cookie(expirationDate=2_000_000_000)
        ])
        self.assertEqual(converted[0]["expires"], 2_000_000_000)

    def test_session_cookies_remain_session_cookies(self):
        exports = (
            fixture_cookie(expires=-1),
            fixture_cookie(expirationDate=None),
            fixture_cookie(session=True, expirationDate=2_000_000_000),
        )
        for exported in exports:
            with self.subTest(attributes=set(exported) - set(fixture_cookie())):
                converted = convert_cookie_editor_to_playwright([exported])
                self.assertEqual(converted[0]["expires"], -1)

    def test_kv_defaults_are_unchanged_and_warning_recommends_json(self):
        logger = Mock()
        synthetic_value = "synthetic-kv-value-not-for-logs"
        converted = convert_kv_to_playwright(
            f"fixture-cookie={synthetic_value}", logger=logger
        )
        self.assertEqual(len(converted), 1)
        self.assertEqual(converted[0]["domain"], ".google.com")
        self.assertEqual(converted[0]["path"], "/")
        self.assertEqual(converted[0]["expires"], -1)
        self.assertEqual(converted[0]["sameSite"], "Lax")
        self.assertTrue(converted[0]["secure"])
        self.assertFalse(converted[0]["httpOnly"])
        logger.warning.assert_called()
        warnings = " ".join(str(argument) for call in logger.warning.call_args_list for argument in call.args)
        self.assertIn("JSON", warnings)
        self.assertNotIn(synthetic_value, str(logger.method_calls))


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class BrowserCookieRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.playwright = sync_playwright().start()
        except (PlaywrightError, OSError) as exc:
            raise unittest.SkipTest("Playwright runtime is unavailable") from exc
        cls.addClassCleanup(cls.playwright.stop)
        options = {"headless": True}
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        if executable:
            options["executable_path"] = executable
        try:
            cls.browser = cls.playwright.chromium.launch(**options)
        except (PlaywrightError, OSError) as exc:
            raise unittest.SkipTest("Chromium runtime is unavailable") from exc
        cls.addClassCleanup(cls.browser.close)

    def test_export_convert_import_preserves_none_and_persistent_expiry(self):
        original_context = self.browser.new_context()
        restored_context = self.browser.new_context()
        self.addCleanup(original_context.close)
        self.addCleanup(restored_context.close)
        for context in (original_context, restored_context):
            context.route("**/*", lambda route: route.abort())

        expiry = int(time.time()) + 3600
        original_context.add_cookies([fixture_cookie(sameSite="None", expires=expiry)])
        exported = original_context.cookies("https://cookies.invalid/")
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["sameSite"], "None")
        self.assertEqual(exported[0]["expires"], expiry)

        converted = auto_convert_to_playwright(json.loads(json.dumps(exported)))
        restored_context.add_cookies(converted)
        restored = restored_context.cookies("https://cookies.invalid/")
        self.assertEqual(len(restored), 1)
        for attribute in ("name", "domain", "path", "httpOnly", "secure", "sameSite", "expires"):
            with self.subTest(attribute=attribute):
                self.assertEqual(restored[0][attribute], exported[0][attribute])
        self.assertEqual(restored[0]["sameSite"], "None")
        self.assertGreater(restored[0]["expires"], time.time())


if __name__ == "__main__":
    unittest.main()
