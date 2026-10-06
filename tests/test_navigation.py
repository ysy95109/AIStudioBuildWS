"""Focused regressions for navigation success and keepalive recovery."""

import html
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, Mock, patch

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Page, sync_playwright
    from browser import navigation
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class NavigationScreenshotTests(unittest.TestCase):
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

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1000, "height": 700})
        self.addCleanup(self.context.close)
        self.context.route("**/*", lambda route: route.abort())
        self.page = self.context.new_page()
        self.page.set_default_timeout(1500)
        self.shutdown = threading.Event()
        self.shutdown.set()
        self.logger = Mock()

    def set_content(self, modal, status="CONNECTED", script=""):
        preview = html.escape(f"<p>WS: {status}</p>", quote=True)
        self.page.set_content(
            f"""<!doctype html><html><body>
            <iframe title="Preview" srcdoc="{preview}" style="width: 600px; height: 300px"></iframe>
            <script>window.dismissed = false;</script>{modal}<script>{script}</script>
            </body></html>"""
        )

    def test_success_screenshot_is_saved_after_onboarding_and_ws_connection(self):
        self.set_content(
            """
            <section id="onboarding" role="dialog" aria-modal="true"
                     style="position: fixed; inset: 50px 150px; background: white">
                <h2>An updated flow for using Gemini in your apps</h2>
                <p>Manage your API key in Secrets.</p><button id="skip">Skip</button>
            </section>
            """,
            status="IDLE",
            script="""
            document.getElementById('skip').onclick = () => {
                document.getElementById('onboarding').remove();
                document.querySelector('iframe').contentDocument.querySelector('p').textContent = 'WS: CONNECTED';
                window.dismissed = true;
            };
            """,
        )
        self.assertEqual(navigation.get_ws_status(self.page), "IDLE")
        screenshot_states = []
        original_screenshot = Page.screenshot

        def checked_screenshot(page, *args, **kwargs):
            self.assertFalse(navigation.has_visible_dialog(page))
            self.assertTrue(page.evaluate("window.dismissed"))
            self.assertEqual(navigation.get_ws_status(page), "CONNECTED")
            screenshot_states.append("CONNECTED")
            return original_screenshot(page, *args, **kwargs)

        with tempfile.TemporaryDirectory() as output_dir:
            with patch.object(navigation, "logs_dir", return_value=output_dir), patch.object(Page, "screenshot", checked_screenshot):
                navigation.handle_successful_navigation(
                    self.page, self.logger, "fixture", shutdown_event=self.shutdown
                )
            screenshots = list(Path(output_dir).glob("SUCCESS_*.png"))
            self.assertEqual(len(screenshots), 1)
            self.assertGreater(screenshots[0].stat().st_size, 0)
        self.assertEqual(screenshot_states, ["CONNECTED"])

    def test_blocking_modal_raises_keepalive_error_without_success_screenshot(self):
        self.set_content(
            """
            <section role="dialog" aria-modal="true"
                     style="position: fixed; inset: 50px 150px; background: white">
                <h2>Review deployment settings</h2>
                <button onclick="window.dismissed = true">Deploy now</button>
            </section>
            """
        )
        real_wait_for_app_ready = navigation.wait_for_app_ready

        def short_readiness_wait(page, logger):
            return real_wait_for_app_ready(page, logger, timeout=0.4)

        with tempfile.TemporaryDirectory() as output_dir:
            with patch.object(navigation, "logs_dir", return_value=output_dir), patch.object(
                navigation, "wait_for_app_ready", side_effect=short_readiness_wait
            ):
                with self.assertRaises(navigation.KeepAliveError):
                    navigation.handle_successful_navigation(
                        self.page, self.logger, "fixture", shutdown_event=self.shutdown
                    )
            self.assertEqual(list(Path(output_dir).glob("SUCCESS_*.png")), [])
        self.assertTrue(navigation.has_visible_dialog(self.page))
        self.assertFalse(self.page.evaluate("window.dismissed"))


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class NavigationKeepaliveTests(unittest.TestCase):
    def test_unchanged_unhealthy_status_retries_after_reconnect_throttle(self):
        for status in ("IDLE", "UNKNOWN"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as output_dir:
                page = MagicMock(spec=Page)
                logger = Mock()
                shutdown = threading.Event()
                clock = [1000.0]
                reconnect_times = []

                def reconnect(page, logger):
                    reconnect_times.append(clock[0])
                    return status

                def advance_sleep(seconds):
                    clock[0] += seconds
                    if clock[0] >= 1040:
                        shutdown.set()

                with patch.object(navigation, "wait_for_app_ready", return_value="CONNECTED"), patch.object(
                    navigation, "logs_dir", return_value=output_dir
                ), patch.object(navigation, "_daily_restart_due", return_value=False), patch.object(
                    navigation, "dismiss_interaction_modal", return_value=True
                ), patch.object(navigation, "has_visible_dialog", return_value=False), patch.object(
                    navigation, "click_in_iframe", return_value=True
                ), patch.object(navigation, "get_ws_status", return_value=status) as get_status, patch.object(
                    navigation, "reconnect_ws", side_effect=reconnect
                ), patch.object(navigation.time, "monotonic", side_effect=lambda: clock[0]), patch.object(
                    navigation.time, "sleep", side_effect=advance_sleep
                ):
                    navigation.handle_successful_navigation(
                        page, logger, "fixture", shutdown_event=shutdown
                    )

                self.assertGreaterEqual(get_status.call_count, 4)
                self.assertEqual(reconnect_times, [1000.0, 1030.0])


if __name__ == "__main__":
    unittest.main()
