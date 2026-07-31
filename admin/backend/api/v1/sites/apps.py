from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from flask import current_app, jsonify, request, url_for

from admin.backend.api.responses import accepted_response, accepted_task_response, error_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import (
    internal_error,
    invalid_fields,
    malformed_body,
    site_name,
    site_not_found,
    task_failure,
    text_fields,
)
from admin.backend.middleware import require_scope
from admin.backend.providers.apps import AppProvider
from admin.backend.providers.sites import SiteProvider
from pilot.core.bench import Bench
from pilot.exceptions import (
    BenchError,
    MigrationConflictError,
    MigrationNotFoundError,
    RegistryUnavailableError,
    TaskNotFoundError,
)
from pilot.internal.site_paths import site_exists
from pilot.internal.validators import validate_app_name, validate_repo_url
from pilot.managers.task import TaskReader
from pilot.tasks.get_and_install_app import GetAndInstallAppTask
from pilot.tasks.install_app import InstallAppTask
from pilot.tasks.uninstall_app import UninstallAppTask

if TYPE_CHECKING:
    from pilot.core.app import App


@sites_bp.get("/<name>/apps")
@require_scope(site_name)
def site_apps(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        site = SiteProvider(bench_root).get_one(name)
    except Exception:
        return internal_error("Could not read site apps.")

    provider = AppProvider(bench_root)
    result = []
    for app_name in site.installed_apps:
        try:
            info = provider.get_app(app_name)
            result.append(
                {
                    "name": app_name,
                    "title": info.title,
                    "description": info.description,
                    "branch": info.branch,
                    "commit": info.current_commit,
                    "version": info.installed_version,
                    "repo": info.repo,
                    "has_local_changes": info.has_local_changes,
                }
            )
        except Exception:
            result.append(
                {
                    "name": app_name,
                    "title": app_name,
                    "description": "",
                    "branch": "",
                    "commit": "",
                    "version": "",
                    "repo": "",
                }
            )

    return jsonify({"apps": result})


@sites_bp.get("/<name>/marketplace")
@require_scope(site_name)
def site_marketplace(name: str):
    """The bench app catalog, read-only. Scoped to a site because the bench-wide
    /marketplace/apps needs a bench token, which a managed site does not hold."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        from pilot.integrations.marketplace import Marketplace

        apps = Marketplace(Bench(bench_root)).read_all_apps()
    except RegistryUnavailableError as exc:
        return error_response("registry_unavailable", str(exc), 503)
    except Exception:
        return internal_error("Could not read marketplace apps.")
    return jsonify([app.to_dict() for app in apps])


@sites_bp.post("/<name>/apps")
@require_scope(site_name)
def install_site_app(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    fields = text_fields(data, "app", "repo", "branch")
    if fields is None:
        return invalid_fields()
    app, repo, branch = fields["app"], fields["repo"], fields["branch"]
    if not app and not repo:
        return error_response("missing_app", "App name or repository is required.", 422)
    if repo and (err := validate_repo_url(repo)):
        return error_response("invalid_repository", err, 422)

    try:
        task_id = _submit_install_task(bench_root, name, app, repo, branch)
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


def _submit_install_task(bench_root: Path, site: str, app: str, repo: str, branch: str) -> str:
    """An app already cloned into the bench installs directly; otherwise it is
    fetched first, by repository URL or by marketplace name."""
    bench = Bench(bench_root)
    if cloned := _cloned_app(bench, app, repo):
        return InstallAppTask.queue(bench, site=site, app=cloned.module_name)
    if repo:
        return GetAndInstallAppTask.queue(bench, repo=repo, branch=branch, site=site)
    return GetAndInstallAppTask.queue(bench, marketplace_app=app, site=site)


def _cloned_app(bench: Bench, app: str, repo: str) -> "App | None":
    """The app this request refers to, if the bench already has it cloned - by
    name, or by repository URL so re-installing from a URL skips the fetch too."""
    from pilot.core.app import App

    try:
        candidate = bench.app(app) if app else App.from_repo(bench, repo)
    except BenchError:
        return None
    return candidate if candidate.is_cloned else None


@sites_bp.post("/<name>/actions/update-apps")
@require_scope(site_name)
def update_site_apps(name: str):
    """Update apps installed on this site through Pilot's safeguarded migration flow."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()

    requested, request_error = _requested_update_apps()
    if request_error is not None:
        return request_error
    try:
        installed = set(SiteProvider(bench_root).get_one(name).installed_apps)
    except Exception:
        return internal_error("Could not read site apps.")
    apps = requested if requested is not None else installed - {"frappe"}
    if not apps:
        return invalid_fields()
    not_installed = sorted(apps - installed)
    if not_installed:
        return error_response(
            "app_not_installed",
            f"App '{not_installed[0]}' is not installed on this site.",
            422,
        )

    try:
        migrations = Bench(bench_root).migrations
        operation = migrations.create_update(apps)
        task_id = operation.begin()
    except MigrationConflictError:
        raise  # the app-wide handler answers 409 migration_conflict with its message
    except Exception as error:
        if "operation" in locals():
            migrations.delete(operation.id)
        return task_failure(error)

    return accepted_response(
        {"operation_id": operation.id, "task_id": task_id},
        url_for("sites.site_task", name=name, task_id=task_id),
    )


def _requested_update_apps():
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return None, malformed_body()
    requested = data.get("apps")
    if requested is not None and (
        not isinstance(requested, list) or not all(isinstance(app, str) for app in requested)
    ):
        return None, invalid_fields()
    if requested is None:
        return None, None
    apps = {app.strip() for app in requested}
    if not apps or any(validate_app_name(app) for app in apps):
        return None, invalid_fields()
    return apps, None


@sites_bp.get("/<name>/tasks/<task_id>")
@require_scope(site_name)
def site_task(name: str, task_id: str):
    """Poll a task this site started. Scoped because the bench-wide /tasks/<id>
    needs a bench token, and ownership is checked so one site can't read another's."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        task = TaskReader(bench_root).read_task(task_id)
    except TaskNotFoundError:
        raise  # the app-wide handler answers 404 task_not_found
    except Exception:
        return internal_error("Could not read task.")
    args = task.args or {}
    operation = _task_operation(bench_root, args)
    if operation is not None:
        if name not in {site.name for site in operation.sites}:
            return error_response("forbidden", "Task does not belong to this site.", 403)
        return jsonify(_operation_task_data(task.as_dict(), str(operation.state)))
    if args.get("site") != name and name not in (args.get("sites") or []):
        return error_response("forbidden", "Task does not belong to this site.", 403)
    return jsonify(task.as_dict())


def _task_operation(bench_root: Path, args: dict):
    operation_id = args.get("operation_id")
    if not isinstance(operation_id, str):
        return None
    try:
        return Bench(bench_root).migrations.get(operation_id)
    except MigrationNotFoundError:
        return None


def _operation_task_data(data: dict, state: str) -> dict:
    if state == "completed":
        data.update(status="success", exit_code=0)
    elif state in {"needs_attention", "revert_failed", "reverted"}:
        data.update(status="failed", exit_code=1)
    else:
        data.update(status="running", exit_code=None)
    return data


@sites_bp.delete("/<name>/apps/<app>")
@require_scope(site_name)
def delete_site_app(name: str, app: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    err = validate_app_name(app)
    if err:
        return error_response("invalid_app", err, 422)

    force = request.args.get("force") == "true"
    try:
        task_id = UninstallAppTask.queue(Bench(bench_root), site=name, app=app, force=force)
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)
