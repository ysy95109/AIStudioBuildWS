"""Browser regressions for WS state parsing and real button actionability.

Run with ``python -m unittest discover -s tests``. Set
PLAYWRIGHT_CHROMIUM_EXECUTABLE to use an installed Chromium browser.
All page content is supplied locally and external requests are blocked.
"""

import html
import os
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from browser.ws_helper import (
    click_connect,
    click_disconnect,
    get_context,
    get_ws_status,
    wait_for_ws_connected,
)


class WebSocketHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        options = {"headless": True}
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        if executable:
            options["executable_path"] = executable
        try:
            cls.browser = cls.playwright.chromium.launch(**options)
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1000, "height": 700})
        self.context.route("**/*", lambda route: route.abort())
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def load_fixture(self, content, *, preview=False):
        if preview:
            escaped = html.escape(content, quote=True)
            self.page.set_content(
                f'<iframe title="Preview" style="width:900px;height:600px" '
                f'srcdoc="{escaped}"></iframe>'
            )
            self.page.frame_locator('iframe[title="Preview"]').locator("body").wait_for()
        else:
            self.page.set_content(content)

    def clicked_actions(self):
        return get_context(self.page).locator("body").evaluate("() => window.clicked")

    def test_full_ws_states_in_direct_page_and_preview(self):
        states = (
            "CONNECTED", "IDLE", "CONNECTING", "RECONNECTING", "DISCONNECTED", "ERROR"
        )
        for preview in (False, True):
            for state in states:
                with self.subTest(preview=preview, state=state):
                    self.load_fixture(f"<span>WS: {state}</span>", preview=preview)
                    self.assertEqual(get_ws_status(self.page), state)

    def test_disconnected_with_connected_in_other_text_is_not_connected(self):
        self.load_fixture("<span>WS: DISCONNECTED (previously CONNECTED)</span>")
        self.assertEqual(get_ws_status(self.page), "DISCONNECTED")

    def test_hidden_status_is_ignored_and_case_is_normalized(self):
        self.load_fixture(
            '<span style="display:none">WS: CONNECTED</span>'
            '<span>ws: disconnected</span>'
        )
        self.assertEqual(get_ws_status(self.page), "DISCONNECTED")

    def test_missing_or_partial_state_returns_unknown(self):
        for content in ("<p>Loading</p>", "<span>WS: CONNECTEDNESS</span>"):
            with self.subTest(content=content):
                self.load_fixture(content)
                self.assertEqual(get_ws_status(self.page), "UNKNOWN")

    def test_connect_never_clicks_disconnect_or_substring_actions(self):
        for preview in (False, True):
            with self.subTest(preview=preview):
                self.load_fixture(
                    "<script>window.clicked = [];</script>"
                    '<button onclick="clicked.push(\'disconnect\')">Disconnect</button>'
                    '<button onclick="clicked.push(\'other\')">Connect to another app</button>'
                    '<button onclick="clicked.push(\'connect\')">Connect</button>',
                    preview=preview,
                )
                self.assertTrue(click_connect(self.page))
                self.assertEqual(self.clicked_actions(), ["connect"])
                self.assertTrue(click_disconnect(self.page))
                self.assertEqual(self.clicked_actions(), ["connect", "disconnect"])

    def test_actual_proxy_accessible_names_work(self):
        self.load_fixture(
            "<script>window.clicked = [];</script>"
            '<button aria-label="Disconnect WebSocket Proxy" '
            'onclick="clicked.push(\'disconnect\')">Disconnect</button>'
            '<button aria-label="Connect WebSocket Proxy" '
            'onclick="clicked.push(\'connect\')">Connect</button>',
            preview=True,
        )
        self.assertTrue(click_connect(self.page))
        self.assertTrue(click_disconnect(self.page))
        self.assertEqual(self.clicked_actions(), ["connect", "disconnect"])

    def test_missing_connect_does_not_fall_back_to_disconnect(self):
        self.load_fixture(
            "<script>window.clicked = [];</script>"
            '<button onclick="clicked.push(\'unexpected\')">Disconnect</button>'
        )
        self.assertFalse(click_connect(self.page))
        self.assertEqual(self.clicked_actions(), [])

    def test_disabled_actions_report_failure_without_clicking(self):
        for action, helper in (("Connect", click_connect), ("Disconnect", click_disconnect)):
            with self.subTest(action=action):
                self.load_fixture(
                    "<script>window.clicked = [];</script>"
                    f'<button disabled onclick="clicked.push(\'unexpected\')">{action}</button>'
                )
                with patch("browser.ws_helper._WS_ACTION_TIMEOUT_MS", 250):
                    self.assertFalse(helper(self.page))
                self.assertEqual(self.clicked_actions(), [])

    def test_intercepted_actions_report_failure_without_js_bypass(self):
        for action, helper in (("Connect", click_connect), ("Disconnect", click_disconnect)):
            with self.subTest(action=action):
                self.load_fixture(
                    "<script>window.clicked = [];</script>"
                    f'<button onclick="clicked.push(\'unexpected\')">{action}</button>'
                    '<div style="position:fixed;inset:0;background:white;z-index:999"></div>',
                    preview=True,
                )
                with patch("browser.ws_helper._WS_ACTION_TIMEOUT_MS", 250):
                    self.assertFalse(helper(self.page))
                self.assertEqual(self.clicked_actions(), [])

    def test_cross_origin_preview_uses_frame_actions(self):
        content = (
            "<script>window.clicked = [];</script>"
            "<span>WS: IDLE</span>"
            '<button aria-label="Connect WebSocket Proxy" '
            'onclick="clicked.push(\'connect\')">Connect</button>'
        )
        self.context.route(
            "https://preview.invalid/fixture",
            lambda route: route.fulfill(status=200, content_type="text/html", body=content),
        )
        self.page.set_content('<iframe title="Preview" src="https://preview.invalid/fixture"></iframe>')
        self.page.frame_locator('iframe[title="Preview"]').locator("button").wait_for()
        self.assertEqual(get_ws_status(self.page), "IDLE")
        self.assertTrue(click_connect(self.page))
        self.assertEqual(self.clicked_actions(), ["connect"])

    def test_wait_does_not_treat_disconnected_as_connected(self):
        self.load_fixture("<span>WS: DISCONNECTED</span>")
        self.assertFalse(wait_for_ws_connected(self.page, timeout=0.05))
        self.load_fixture("<span>WS: CONNECTED</span>")
        self.assertTrue(wait_for_ws_connected(self.page, timeout=0.05))


if __name__ == "__main__":
    unittest.main()
