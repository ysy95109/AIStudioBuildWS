"""Local-browser regressions for AI Studio popup handling and startup readiness.

Run with ``python -m unittest discover -s tests -v``. Set
PLAYWRIGHT_CHROMIUM_EXECUTABLE to use an existing Chromium installation.
"""

import html
import os
import time
import unittest
from unittest.mock import patch

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Locator, sync_playwright
except ImportError:
    sync_playwright = None


ONBOARDING_HEADING = "An updated flow for using Gemini in your apps"
PAGE_STYLE = """
    body { margin: 0; font: 16px sans-serif; }
    button { padding: 10px 16px; margin: 4px; }
    iframe { width: 500px; height: 100px; border: 0; }
    .panel {
        position: fixed; left: 365px; top: 55px; width: 480px;
        padding: 24px; background: white; border: 1px solid #ccc;
        box-shadow: 0 2px 8px #888; z-index: 20;
    }
    .backdrop { position: fixed; inset: 0; background: #ffffffbb; z-index: 10; }
    .footer { display: flex; justify-content: flex-end; margin-top: 48px; }
    .promo { left: 930px; top: 55px; width: 230px; z-index: 30; }
    .promo .close { float: right; }
"""


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class DialogRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.playwright = sync_playwright().start()
        except (PlaywrightError, OSError) as exc:
            reason = str(exc).splitlines()[0]
            raise unittest.SkipTest(f"Playwright runtime is unavailable: {reason}") from exc
        cls.addClassCleanup(cls.playwright.stop)
        options = {"headless": True}
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
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
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 720})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(1500)

    def set_content(self, content, script="", status="CONNECTED"):
        preview = html.escape(f"<p>WS: {status}</p>", quote=True)
        self.page.set_content(
            f"""<!doctype html><html><head><style>{PAGE_STYLE}</style></head>
            <body><iframe title="Preview" srcdoc="{preview}"></iframe>
            <script>window.events = []; window.backgroundClicks = [];</script>
            {content}<script>{script}</script></body></html>"""
        )

    def test_unlabelled_onboarding_and_custom_url_promo_are_dismissed(self):
        self.set_content(
            f"""
            <div class="backdrop" id="backdrop"></div>
            <section class="panel" id="onboarding">
                <div style="height: 220px">Secrets: GEMINI_API_KEY</div>
                <h2>{ONBOARDING_HEADING}</h2>
                <p>Your API key is now attached automatically to your apps.
                   You can view or manage your key in the Secrets panel.</p>
                <div class="footer">
                    <button id="skip">Skip</button><button id="next">Next</button>
                </div>
            </section>
            <aside class="panel promo" id="promo">
                <button class="close" aria-label="Close" id="promo-close">×</button>
                <p>NEW</p><h3>Grab your custom URL today!</h3>
                <p>Publish your app and pick your favorite .ai.studio URL.</p>
                <button id="lets-go">Let's go</button>
            </aside>
            """,
            """
            document.getElementById('skip').onclick = () => {
                events.push('skip');
                document.getElementById('onboarding').remove();
                document.getElementById('backdrop').remove();
            };
            document.getElementById('next').onclick = () => events.push('next');
            document.getElementById('promo-close').onclick = () => {
                events.push('promo-close'); document.getElementById('promo').remove();
            };
            document.getElementById('lets-go').onclick = () => events.push('lets-go');
            """,
        )

        self.assertTrue(self.dialogs.has_visible_dialog(self.page))
        self.assertTrue(self.dialogs.dismiss_popups(self.page))
        self.assertCountEqual(self.page.evaluate("events"), ["skip", "promo-close"])
        self.assertFalse(self.dialogs.has_visible_dialog(self.page))

    def test_next_and_done_advance_the_known_modal_when_skip_is_absent(self):
        self.set_content(
            f"""
            <section class="panel" role="dialog" aria-modal="true" id="tour">
                <h2>{ONBOARDING_HEADING}</h2>
                <p id="step">Your API key is attached to your apps.</p>
                <button id="next">Next</button>
            </section>
            """,
            """
            document.getElementById('next').onclick = () => {
                events.push('next');
                document.getElementById('step').textContent = 'View your key in Secrets.';
                const done = document.createElement('button');
                done.textContent = 'Done';
                done.onclick = () => { events.push('done'); document.getElementById('tour').remove(); };
                document.getElementById('next').replaceWith(done);
            };
            """,
        )

        self.assertTrue(self.dialogs.dismiss_popups(self.page))
        self.assertEqual(self.page.evaluate("events"), ["next", "done"])
        self.assertFalse(self.dialogs.has_visible_dialog(self.page))

    def test_readiness_clears_a_delayed_onboarding_even_with_connected_preview(self):
        self.set_content(
            "",
            f"""
            setTimeout(() => {{
                const tour = document.createElement('section');
                tour.className = 'panel'; tour.id = 'delayed-tour';
                tour.innerHTML = '<h2>{ONBOARDING_HEADING}</h2><p>Manage your key in Secrets.</p><button>Skip</button>';
                tour.querySelector('button').onclick = () => {{ events.push('delayed-skip'); tour.remove(); }};
                document.body.appendChild(tour);
            }}, 250);
            """,
        )

        self.assertEqual(self.dialogs.wait_for_app_ready(self.page, timeout=3), "CONNECTED")
        self.assertEqual(self.page.evaluate("events"), ["delayed-skip"])
        self.assertFalse(self.dialogs.has_visible_dialog(self.page))

    def test_unknown_visible_modal_prevents_readiness(self):
        self.set_content(
            """
            <section class="panel" role="dialog" aria-modal="true">
                <h2>Review deployment settings</h2>
                <p>Choose a deployment environment before proceeding.</p>
                <button onclick="events.push('deploy')">Deploy now</button>
            </section>
            """
        )

        self.assertTrue(self.dialogs.has_visible_dialog(self.page))
        self.assertFalse(self.dialogs.dismiss_popups(self.page))
        with self.assertRaises(self.dialogs.AppReadinessError):
            self.dialogs.wait_for_app_ready(self.page, timeout=0.4)
        self.assertEqual(self.page.evaluate("events"), [])
        self.assertTrue(self.dialogs.has_visible_dialog(self.page))

    def test_background_actions_are_never_treated_as_popup_controls(self):
        self.set_content(
            """
            <main>
                <h1>WebSocket Proxy Logger</h1>
                <button onclick="backgroundClicks.push('Skip')">Skip</button>
                <button onclick="backgroundClicks.push('Next')">Next</button>
                <button onclick="backgroundClicks.push('Continue')">Continue</button>
                <button onclick="backgroundClicks.push('Connect')">Connect</button>
            </main>
            """
        )

        self.assertFalse(self.dialogs.has_visible_dialog(self.page))
        self.assertTrue(self.dialogs.dismiss_popups(self.page))
        self.assertEqual(self.dialogs.wait_for_app_ready(self.page, timeout=3), "CONNECTED")
        self.assertEqual(self.page.evaluate("backgroundClicks"), [])

    def test_layered_modals_only_click_controls_that_receive_pointer_events(self):
        self.set_content(
            f"""
            <section class="panel" role="dialog" aria-modal="true" id="lower">
                <h2>{ONBOARDING_HEADING}</h2><p>Manage your API key.</p>
                <button onclick="events.push('lower');document.getElementById('lower').remove()">Skip</button>
            </section>
            <section class="panel" style="z-index: 30" role="dialog" aria-modal="true" id="upper">
                <h2>{ONBOARDING_HEADING}</h2><p>Manage your API key.</p>
                <button onclick="events.push('upper');document.getElementById('upper').remove()">Skip</button>
            </section>
            """
        )
        original_click = Locator.click

        def checked_click(locator, *args, **kwargs):
            self.assertFalse(kwargs.get("force", False), "Popup clicks must respect hit testing")
            self.assertTrue(
                locator.evaluate("""element => {
                    const box = element.getBoundingClientRect();
                    const top = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
                    return top === element || element.contains(top);
                }"""),
                "Only the exposed modal control may be clicked",
            )
            return original_click(locator, *args, **kwargs)

        with patch.object(Locator, "click", checked_click):
            self.assertTrue(self.dialogs.dismiss_popups(self.page))
        self.assertEqual(self.page.evaluate("events"), ["upper", "lower"])
        self.assertFalse(self.dialogs.has_visible_dialog(self.page))

    def test_unknown_or_idle_websocket_does_not_count_as_ready(self):
        for status in ("UNKNOWN", "IDLE"):
            with self.subTest(status=status):
                self.set_content("", status=status)
                with self.assertRaises(self.dialogs.AppReadinessError):
                    self.dialogs.wait_for_app_ready(self.page, timeout=0.4)

    def test_unknown_websocket_can_become_connected_during_startup(self):
        self.set_content(
            "",
            """
            setTimeout(() => {
                document.querySelector('iframe').contentDocument.querySelector('p').textContent = 'WS: CONNECTED';
                events.push('connected');
            }, 250);
            """,
            status="UNKNOWN",
        )

        self.assertEqual(self.dialogs.wait_for_app_ready(self.page, timeout=3), "CONNECTED")
        self.assertEqual(self.page.evaluate("events"), ["connected"])

    def test_persistent_popup_action_does_not_extend_readiness_deadline(self):
        self.set_content(
            f"""
            <section class="panel" role="dialog" aria-modal="true">
                <h2>{ONBOARDING_HEADING}</h2>
                <p>This popup remains open after its button is clicked.</p>
                <button onclick="events.push('skip')">Skip</button>
            </section>
            """
        )

        started = time.monotonic()
        with self.assertRaises(self.dialogs.AppReadinessError):
            self.dialogs.wait_for_app_ready(self.page, timeout=0.6)
        self.assertLess(time.monotonic() - started, 2, "Repeated popup actions must share the startup deadline")
        self.assertTrue(self.page.evaluate("events.length > 0"))
        self.assertTrue(self.dialogs.has_visible_dialog(self.page))

    def test_missing_or_hidden_preview_does_not_count_as_ready(self):
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                self.set_content("")
                if hidden:
                    self.page.locator('iframe[title="Preview"]').evaluate("element => element.style.display = 'none'")
                else:
                    self.page.locator('iframe[title="Preview"]').evaluate("element => element.remove()")
                with self.assertRaises(self.dialogs.AppReadinessError):
                    self.dialogs.wait_for_app_ready(self.page, timeout=0.4)

    def test_unlabelled_tour_remains_recognized_after_its_heading_changes(self):
        self.set_content(
            f'<section class="panel" id="tour"><h2>{ONBOARDING_HEADING}</h2><button>Next</button></section>',
            """
            const tour = document.getElementById('tour');
            tour.querySelector('button').onclick = () => {
                events.push('next');
                tour.innerHTML = '<h2>Manage your key in Secrets</h2><button>Done</button>';
                tour.querySelector('button').onclick = () => { events.push('done'); tour.remove(); };
            };
            """,
        )
        self.assertEqual(self.dialogs.wait_for_app_ready(self.page, timeout=3), "CONNECTED")
        self.assertEqual(self.page.evaluate("events"), ["next", "done"])
        self.assertFalse(self.dialogs.has_visible_dialog(self.page))

    def test_legacy_connection_actions_remain_scoped_to_the_modal(self):
        for label in ("Connect", "确认连接", "连接", "继续"):
            with self.subTest(label=label):
                self.set_content(
                    f'''<button onclick="backgroundClicks.push('Connect')">Connect</button>
                    <section class="panel" role="dialog" id="legacy">
                    <h2>Continue to the app</h2>
                    <button onclick="events.push('modal');document.getElementById('legacy').remove()">{label}</button>
                    </section>'''
                )
                self.assertTrue(self.dialogs.dismiss_popups(self.page))
                self.assertEqual(self.page.evaluate("events"), ["modal"])
                self.assertEqual(self.page.evaluate("backgroundClicks"), [])

    def test_disabled_connect_shares_the_readiness_deadline(self):
        self.set_content("", status="IDLE")
        self.page.frame_locator('iframe[title="Preview"]').locator('body').evaluate(
            '''body => body.insertAdjacentHTML('beforeend', '<button disabled>Connect</button>')'''
        )
        started = time.monotonic()
        with self.assertRaises(self.dialogs.AppReadinessError):
            self.dialogs.wait_for_app_ready(self.page, timeout=0.4)
        self.assertLess(time.monotonic() - started, 1.5)


if __name__ == "__main__":
    unittest.main()
