"""Settings of gunicorn read from the working folder (Dockerfile.prod). Audit item 22: with
C4R_LOG_JSON on, the records of gunicorn itself are JSON lines like those of the application
(c4rsys.platform.logs); the answered requests are logged by the application, not here."""

import os

if os.environ.get("C4R_LOG_JSON", "").strip().lower() in {"1", "true", "yes", "on"}:
    from c4rsys.platform.logs import gunicorn_config

    logconfig_dict = gunicorn_config()

# The control socket of gunicorn (gunicornc) is not used; in its default folder, the home of the
# user, it only failed with «Permission denied» at every start.
control_socket_disable = True
