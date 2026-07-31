"""High-level admin UI actions that wait for background tasks."""

from __future__ import annotations

from harness.tasks import run_task_action, wait_for_task
from playwright.sync_api import Page, expect


def login(page: Page, base_url: str, password: str) -> None:
    # PUT /api/v1/setup/configuration hands back a session cookie, which carries over
    # in this shared browser context - so without clearing it we'd already be
    # authenticated and the login form would never render. Drop it to exercise the
    # actual login.
    page.context.clear_cookies()
    page.goto(f"{base_url}/")
    page.get_by_placeholder("Password").fill(password)
    page.get_by_role("button", name="Continue").click()
    # Landed on the Sites page once the header action is mounted.
    expect(page.get_by_role("button", name="New site")).to_be_visible(timeout=30_000)


def create_site(page: Page, base_url: str, site_name: str) -> None:
    page.goto(f"{base_url}/")
    page.get_by_role("button", name="New site").click()

    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Site name").fill(site_name)

    task_id = run_task_action(
        page,
        "/api/v1/sites",
        lambda: dialog.get_by_role("button", name="Create Site").click(),
    )
    wait_for_task(page.request, base_url, task_id)


def drop_site(page: Page, base_url: str, site_name: str) -> None:
    # Drop lives in the site's Danger section, part of the "settings" tab (there
    # is no standalone "actions" tab anymore).
    _open_site_tab(page, base_url, site_name, "settings")
    page.get_by_role("button", name="Drop site").click()

    dialog = page.get_by_role("dialog")
    dialog.get_by_label(f"Type {site_name} to confirm").fill(site_name)
    task_id = run_task_action(
        page,
        f"/api/v1/sites/{site_name}",
        lambda: dialog.get_by_role("button", name="Drop site", exact=True).click(),
        method="DELETE",
    )
    wait_for_task(page.request, base_url, task_id)


def installed_apps(page: Page, base_url: str, site_name: str) -> list[str]:
    res = page.request.get(f"{base_url}/api/v1/sites/{site_name}")
    expect(res).to_be_ok()
    return res.json().get("installed_apps") or []


def site_exists(page: Page, base_url: str, site_name: str) -> bool:
    res = page.request.get(f"{base_url}/api/v1/sites")
    if not res.ok:
        return False
    return any(s.get("name") == site_name for s in res.json())


def _open_site_tab(page: Page, base_url: str, site_name: str, tab: str) -> None:
    # The tab is a router path param (/sites/:name/:tab?), not a URL hash, so
    # navigating straight to it is the most deterministic way to land there.
    page.goto(f"{base_url}/sites/{site_name}/{tab}")
    expect(page.get_by_text(site_name, exact=False).first).to_be_visible(timeout=30_000)
