import sys
import threading

from django.apps import AppConfig


_schema_repair_lock = threading.Lock()
_schema_repair_attempted = False


def _should_run_runtime_schema_repair():
    command_line = ' '.join(sys.argv).lower()
    return any(command in command_line for command in ('gunicorn', 'runserver', 'uvicorn', 'daphne'))


class UsuariosConfig(AppConfig):
    name = 'config.usuarios'

    def ready(self):
        global _schema_repair_attempted

        if not _should_run_runtime_schema_repair():
            return

        with _schema_repair_lock:
            if _schema_repair_attempted:
                return
            _schema_repair_attempted = True

        from .schema_repair import ensure_permission_overrides_column_on_startup

        ensure_permission_overrides_column_on_startup()
