"""AI Studio popup handling shared by startup and keepalive."""

import re
import time

from playwright.sync_api import Page

from browser.ws_helper import click_connect, get_ws_status


DIALOG_SELECTOR = (
    'mat-mdc-dialog-container, .cdk-overlay-pane, [role="dialog"], '
    '[aria-modal="true"], ms-g1-welcome-dialog, [data-aistudiobuildws-popup]'
)
ONBOARDING_TITLE = "An updated flow for using Gemini in your apps"
URL_PROMO_TITLE = "Grab your custom URL today!"
POPUP_ACTIONS = (
    "Skip", "Continue to the app", "Continue to app", "Got it, thanks",
    "Got it", "Dismiss", "OK", "Agree", "Accept", "I agree", "Continue",
    "Connect", "确认连接", "连接", "继续", "Done", "Next",
)


class AppReadinessError(Exception):
    pass


def _visible_dialogs(page: Page):
    dialogs = []
    containers = page.locator(DIALOG_SELECTOR)
    for index in range(containers.count()):
        container = containers.nth(index)
        if container.is_visible():
            dialogs.append(container)

    # Some AI Studio announcements have no dialog role/Material wrapper.
    # Anchor to their known heading, never to generic page-wide Skip/Next.
    for title in (ONBOARDING_TITLE, URL_PROMO_TITLE):
        headings = page.get_by_text(title, exact=True)
        for index in range(headings.count()):
            heading = headings.nth(index)
            if not heading.is_visible():
                continue
            container = heading.locator(
                "xpath=ancestor::*[not(self::body) and not(self::html)]"
                "[.//button or .//*[@role='button']][1]"
            )
            if container.count() and container.is_visible():
                # Keep recognizing an unlabelled tour when Next changes its
                # heading but leaves the same popup container on screen.
                container.evaluate(
                    "(element, kind) => element.setAttribute('data-aistudiobuildws-popup', kind)",
                    "promo" if title == URL_PROMO_TITLE else "onboarding",
                )
                dialogs.append(container)
            else:
                # An announcement without an actionable container still blocks
                # readiness; don't turn a missing close control into success.
                dialogs.append(heading)
    return dialogs


def has_visible_dialog(page: Page) -> bool:
    return bool(_visible_dialogs(page))


def _click_action(button, logger=None, timeout_ms=1000) -> bool:
    try:
        if not button.is_visible() or not button.is_enabled():
            return False
        action_deadline = time.monotonic() + timeout_ms / 1000
        button.scroll_into_view_if_needed(timeout=timeout_ms)
        # A visible lower dialog can still be covered by another overlay.
        # Check the actual hit target and let Playwright recheck before clicking.
        if not button.evaluate("""element => {
            const rect = element.getBoundingClientRect();
            const target = document.elementFromPoint(
                rect.x + rect.width / 2, rect.y + rect.height / 2
            );
            return target === element || element.contains(target);
        }"""):
            return False
        remaining_ms = (action_deadline - time.monotonic()) * 1000
        if remaining_ms <= 0:
            return False
        button.click(timeout=remaining_ms)
        return True
    except Exception as error:
        if logger:
            logger.debug(f"弹窗按钮暂不可点击: {error}")
        return False


def dismiss_popups(page: Page, logger=None, max_iterations=8, deadline=None) -> bool:
    """Dismiss visible overlays; return False if an overlay is still blocking."""
    for _ in range(max_iterations):
        if deadline is not None and time.monotonic() >= deadline:
            break
        dialogs = _visible_dialogs(page)
        if not dialogs:
            return True
        clicked = False
        for dialog in reversed(dialogs):
            if dialog.get_attribute("data-aistudiobuildws-popup") == "promo" or dialog.get_by_text(URL_PROMO_TITLE, exact=True).count():
                # Close this promotion instead of entering URL publishing.
                actions = [dialog.get_by_role(
                    "button", name=re.compile(r"^(close|dismiss)(?:\s+.*)?$", re.I)
                ), dialog.locator(
                    'button:has(mat-icon:text-is("close")), '
                    'button:has(.material-icons:text-is("close")), '
                    'button:text-is("×"), button:text-is("✕"), '
                    'button:text-is("X"), button:text-is("close")'
                )]
            else:
                actions = [dialog.get_by_role("button", name=name, exact=True)
                           for name in POPUP_ACTIONS]
            for action in actions:
                for index in range(action.count()):
                    remaining_ms = 1000 if deadline is None else min(1000, (deadline - time.monotonic()) * 1000)
                    if remaining_ms <= 0:
                        return False
                    if _click_action(action.nth(index), logger, timeout_ms=remaining_ms):
                        clicked = True
                        if logger:
                            logger.info("已点击 AI Studio 弹窗内的关闭/继续按钮")
                        break
                if clicked:
                    break
            if clicked:
                break
        if not clicked:
            return False
        # Rescan after every action: it can reveal another dialog or tour step.
        pause_ms = 150 if deadline is None else min(150, max(0, deadline - time.monotonic()) * 1000)
        page.wait_for_timeout(pause_ms)
    return not has_visible_dialog(page)


def wait_for_app_ready(page: Page, logger=None, timeout=30) -> str:
    """Require a clear, rendered Preview and an established WS connection."""
    deadline = time.monotonic() + timeout
    clear_since = None
    next_connect_at = 0
    last_status = "UNKNOWN"
    preview_visible = False
    blocking = False
    while time.monotonic() < deadline:
        # One action per poll keeps repeated buttons within the real deadline.
        dismiss_popups(page, logger, max_iterations=1, deadline=deadline)
        blocking = has_visible_dialog(page)
        preview = page.locator('iframe[title="Preview"]').first
        preview_visible = preview.is_visible()
        loading = page.locator('mat-spinner:visible, [role="progressbar"]:visible').count() > 0
        last_status = get_ws_status(page, logger) if preview_visible else "UNKNOWN"
        now = time.monotonic()
        if now >= deadline:
            break
        if not blocking and not loading and preview_visible:
            if last_status == "CONNECTED":
                if clear_since is None:
                    clear_since = now
                # Catch asynchronously mounted onboarding before declaring success.
                if now - clear_since >= 1:
                    return last_status
            else:
                clear_since = None
                if last_status in ("IDLE", "DISCONNECTED", "ERROR") and now >= next_connect_at:
                    click_connect(page, logger, timeout_ms=min(1000, (deadline - now) * 1000))
                    next_connect_at = time.monotonic() + 5
        else:
            clear_since = None
        page.wait_for_timeout(min(200, max(0, deadline - time.monotonic()) * 1000))
    raise AppReadinessError(
        f"AI Studio 未准备就绪: blocking_dialog={blocking}, "
        f"preview_visible={preview_visible}, WS={last_status}"
    )
