# App Processes

An app can declare background processes in its own `pyproject.toml`. The bench runs them next to web, workers and socketio, for example a mail server for the Mail app.

```toml
[tool.pilot.background_processes.stalwart]
command = ["./stalwart", "--config", "config.toml"]
pre_run = ["./scripts/install_stalwart.sh"]
post_run = ["rm", "-f", "stalwart.sock"]
working_dir = "server"
restart_on_failure = true
stop_timeout = 30
env = { STALWART_PATH = "/opt/stalwart" }
```

Each process needs `command` or `frappe_command`. The process runs as `<app>-<name>`, here `mail-stalwart`, and logs to `logs/mail-stalwart.log`.

## Fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `command` | list of strings | one of the two | Executable and arguments. Never a shell string. |
| `frappe_command` | list of strings | one of the two | A Frappe CLI command, used instead of `command`. |
| `pre_run` | list of strings | none | Runs before each start. |
| `post_run` | list of strings | none | Runs after each stop. |
| `working_dir` | string | app directory | Directory the process runs in. |
| `restart_on_failure` | bool | `true` | Restart after a non-zero exit. |
| `stop_timeout` | int | manager default | Seconds to wait for a graceful stop. |
| `env` | table of strings | empty | Environment variables. |

The table key must match `^[a-z0-9][a-z0-9_-]{0,31}$` and env keys must match `^[A-Z_][A-Z0-9_]*$`. Values must not contain control characters.

## Paths

A relative `working_dir` is read from the app directory. The first entry of `command`, `pre_run` and `post_run` resolves from `working_dir` when it contains a slash (`./stalwart`, `bin/stalwart`). An absolute path is used as given, and a bare name (`flow`) is looked up on `PATH`. Paths are not checked on disk, so `pre_run` can download the executable.

## Frappe commands

`frappe_command` runs a command the app adds to the Frappe CLI, the same as `bench <command>`. Pilot runs it with the bench Python in the `sites` directory, so it takes no `working_dir`.

```toml
[tool.pilot.background_processes.server]
frappe_command = ["flow-server", "serve"]
```

## Hooks

`pre_run` runs on every start, so keep it idempotent. A shell line needs an explicit shell: `pre_run = ["bash", "-c", "test -x ./stalwart || ./scripts/install.sh"]`.

systemd runs the hooks as `ExecStartPre` and `ExecStopPost`. Supervisor and the dev runner run a process with hooks inside `sh`, which runs `post_run` after the process exits and keeps its exit code.

## Failures

A malformed declaration stops the bench from writing its process configuration, so the running processes stay as they are until the app is fixed. The dev runner does not restart a declared process, and a declared process that exits does not stop the bench.
