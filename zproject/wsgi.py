"""
WSGI config for zulip project.

This module contains the WSGI application used by Django's development server
and any production WSGI deployments. It should expose a module-level variable
named ``application``. Django's ``runserver`` and ``runfcgi`` commands discover
this application via the ``WSGI_APPLICATION`` setting.

Usually you will have the standard Django WSGI application here, but it also
might make sense to replace the whole Django WSGI application with a custom one
that later delegates to the Django one. For example, you could introduce WSGI
middleware here, or combine a Django application with an application of another
framework.

"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)
from scripts.lib.setup_path import setup_path

setup_path()

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zproject.settings")

import contextlib
from collections.abc import Callable
from typing import Any

import orjson
from django.core.wsgi import get_wsgi_application

memray_tracker = None
request_count = 0
try:
    import uwsgi

    from scripts.lib.zulip_tools import get_config, get_config_file

    if (
        get_config(get_config_file(), "application_server", "memory_profile_startup", False)
        and uwsgi.worker_id() == 1
    ):
        import memray

        memray_tracker = memray.Tracker(f"/tmp/memray-django-{uwsgi.masterpid()}-{os.getpid()}")  # noqa: S108
        memray_tracker.__enter__()
        print(f"Installed memray profiling on pid {os.getpid()}")

        from django.core.signals import request_finished
        from django.dispatch import receiver

        def remove_memray(sender: Any, **kwargs: Any) -> None:
            global memray_tracker
            global request_count
            request_count += 1
            if memray_tracker is not None and request_count > 100:
                memray_tracker.__exit__(None, None, None)
                memray_tracker = None
                print(f"Uninstalled memray profiling from pid {os.getpid()}")

        receiver(request_finished)(remove_memray)

except Exception as e:
    print(f"Failed to profile memray: {e}")

try:
    # This application object is used by any WSGI server configured to use this
    # file. This includes Django's development server, if the WSGI_APPLICATION
    # setting points here.

    application = get_wsgi_application()

    # We force loading of the main parts of the application now, by
    # handing it a fake request, rather than have to pay that price
    # during the first request served by this process.  Hitting the
    # /health endpoint will not only load Django and all of the views
    # (by loading the URL dispatcher) but will also force open any
    # lazy-loaded service connections.
    #
    # The return value (and thus response status) of this healthcheck
    # request is ignored, so we do return the application handler even
    # if connections are not fully available yet.  This at least
    # allows application logging to handle any such errors, instead of
    # arcane errors from uwsgi not being able to load its handler
    # function.
    def ignored_start_response(
        status: str, headers: list[tuple[str, str]], exc_info: Any = None, /
    ) -> Callable[[bytes], object]:
        return lambda x: None

    application(
        {
            "REQUEST_METHOD": "GET",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "443",
            "PATH_INFO": "/health",
            "REMOTE_ADDR": "127.0.0.1",
            "wsgi.input": sys.stdin,
            "wsgi.url_scheme": "https",
        },
        ignored_start_response,
    )

    with contextlib.suppress(ModuleNotFoundError):
        # The uwsgi module is only importable when running under
        # uwsgi; development uses this file as well, but inside a
        # pure-Python server.  The surrounding contextmanager ensures
        # that we don't bother with these steps if we're in
        # development.
        import uwsgi

        if uwsgi.worker_id() == uwsgi.numproc:
            # This is the last worker to load in the chain reload
            with open("/var/lib/zulip/django-workers.ready", "wb") as f:
                # The contents of this file are not read by restart-server
                # in any way, but leave some useful information about the
                # state of uwsgi.
                f.write(
                    orjson.dumps(
                        uwsgi.workers(), option=orjson.OPT_INDENT_2, default=lambda e: e.decode()
                    ),
                )

except Exception:
    # If /etc/zulip/settings.py contains invalid syntax, Django
    # initialization will fail in django.setup().  In this case, our
    # normal configuration to logs errors to /var/log/zulip/errors.log
    # won't have been initialized.  Since it's really valuable for the
    # debugging process for a Zulip 500 error to always be "check
    # /var/log/zulip/errors.log", we log to that file directly here.
    import logging

    logging.basicConfig(
        filename="/var/log/zulip/errors.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.exception("get_wsgi_application() failed:")
    raise
