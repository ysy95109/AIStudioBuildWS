"""Local-browser regressions for Google's rendered 401 authentication page."""

import html
import os
import time
import unittest

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


ERROR_BODY = """
    <p><b>401.</b> <ins>That’s an error.</ins></p>
    <p>The server cannot process the request because it is malformed.
       It should not be retried. <ins>That’s all we know.</ins></p>
"""
GOOGLE_ERROR_PAGE = "<h1>Google</h1>" + ERROR_BODY


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class PreviewAuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.playwright = sync_playwright().start()
        except (PlaywrightError, OSError) as exc:
            reason = str(exc).splitlines()[0]
            raise unittest.SkipTest(f"Playwright runtime is unavailable: {reason}") from exc
        cls.addClassCleanup(cls.playwright.stop)
        options = {"headless": True}
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        if executable:
            options["executable_path"] = executable
        try:
            cls.browser = cls.playwright.chromium.launch(**options)
        except (PlaywrightError, OSError) as exc:
            reason = str(exc).splitlines()[0]
            raise unittest.SkipTest(f"Chromium runtime is unavailable: {reason}") from exc
        cls.addClassCleanup(cls.browser.close)

        from browser import dialogs

        cls.dialogs = dialogs

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1000, "height": 700})
        self.addCleanup(self.context.close)
        self.context.route("**/*", lambda route: route.abort())
        self.page = self.context.new_page()
        self.page.set_default_timeout(1500)

    @staticmethod
    def frame(content, title="Preview", hidden=False):
        escaped = html.escape(content, quote=True)
        style = "display: none" if hidden else "width: 900px; height: 500px"
        return f'<iframe title="{html.escape(title, quote=True)}" srcdoc="{escaped}" style="{style}"></iframe>'

    def assert_authentication_rejected_immediately(self):
        started = time.monotonic()
        with self.assertRaises(self.dialogs.PreviewAuthenticationError) as raised:
            self.dialogs.wait_for_app_ready(self.page, timeout=3)
        self.assertLess(time.monotonic() - started, 1.5, "Google's non-retryable 401 must fail before startup timeout")
        self.assertIn("401", str(raised.exception))

    def test_google_401_in_preview_fails_startup_immediately(self):
        self.page.set_content(self.frame(GOOGLE_ERROR_PAGE))

        self.assert_authentication_rejected_immediately()

    def test_google_401_in_renamed_iframe_is_still_detected(self):
        logo = '<img alt="Google" width="100" height="30" src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22%3E%3C/svg%3E">'
        self.page.set_content(self.frame(logo + ERROR_BODY, title="Application output"))

        self.assert_authentication_rejected_immediately()

    def test_google_401_in_top_document_fails_startup_immediately(self):
        self.page.set_content(GOOGLE_ERROR_PAGE)

        self.assert_authentication_rejected_immediately()

    def test_hidden_google_401_frame_does_not_block_connected_preview(self):
        self.page.set_content(
            self.frame(GOOGLE_ERROR_PAGE, title="Old authentication frame", hidden=True)
            + self.frame("<h1>WebSocket Proxy Logger</h1><p>WS: CONNECTED</p>")
        )

        self.assertEqual(self.dialogs.wait_for_app_ready(self.page, timeout=3), "CONNECTED")

    def test_incidental_401_and_api_error_logs_do_not_match_google_error_page(self):
        self.page.set_content(
            self.frame(
                "<h1>WebSocket Proxy Logger</h1><p>WS: CONNECTED</p>"
                "<p>Provider: Google Gemini</p>"
                "<pre>HTTP_ERROR401: request failed. Status 401 appeared in API logs.</pre>"
            )
        )

        self.assertEqual(self.dialogs.wait_for_app_ready(self.page, timeout=3), "CONNECTED")

    def test_generic_401_page_without_google_marker_is_not_google_authentication_failure(self):
        self.page.set_content(self.frame("<h1>Proxy request error</h1>" + ERROR_BODY))

        with self.assertRaises(self.dialogs.AppReadinessError) as raised:
            self.dialogs.wait_for_app_ready(self.page, timeout=0.4)
        self.assertNotIsInstance(raised.exception, self.dialogs.PreviewAuthenticationError)

    def test_editor_quote_of_google_error_does_not_block_working_preview(self):
        self.page.set_content(
            '<h1>Google AI Studio</h1><a href="https://www.google.com/">Google</a>'
            '<section><h2>Previous error report</h2>' + ERROR_BODY + '</section>'
            + self.frame('<p>WS: CONNECTED</p>')
        )
        self.assertEqual(self.dialogs.wait_for_app_ready(self.page, timeout=3), "CONNECTED")

    def test_onboarding_can_replace_a_transient_google_error_document(self):
        self.page.set_content(
            self.frame(GOOGLE_ERROR_PAGE)
            + '<section role="dialog" id="tour" style="position:fixed;inset:50px;background:white">'
            + '<h2>An updated flow for using Gemini in your apps</h2>'
            + '<button id="skip">Skip</button></section>'
            + """<script>
            document.getElementById('skip').onclick = () => {
                document.getElementById('tour').remove();
                setTimeout(() => {
                    document.querySelector('iframe').srcdoc = '<p>WS: CONNECTED</p>';
                }, 300);
            };
            </script>"""
        )
        self.assertEqual(self.dialogs.wait_for_app_ready(self.page, timeout=3), "CONNECTED")


if __name__ == "__main__":
    unittest.main()
