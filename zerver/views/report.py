# System documented in https://zulip.readthedocs.io/en/latest/subsystems/logging.html
import logging
import subprocess
from typing import Any, Dict, Mapping, Optional
from urllib.parse import SplitResult

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from zerver.lib.markdown import privacy_clean_markdown
from zerver.lib.queue import queue_json_publish
from zerver.lib.request import REQ, has_request_variables
from zerver.lib.response import json_success
from zerver.lib.storage import static_path
from zerver.lib.unminify import SourceMap
from zerver.lib.validator import check_bool, check_dict
from zerver.models import UserProfile

js_source_map: Optional[SourceMap] = None

# Read the source map information for decoding JavaScript backtraces.
def get_js_source_map() -> Optional[SourceMap]:
    global js_source_map
    if not js_source_map and not (settings.DEVELOPMENT or settings.TEST_SUITE):
        js_source_map = SourceMap([
            static_path('webpack-bundles'),
        ])
    return js_source_map

@has_request_variables
def report_error(request: HttpRequest, user_profile: UserProfile, message: str=REQ(),
                 stacktrace: str=REQ(), ui_message: bool=REQ(validator=check_bool),
                 user_agent: str=REQ(), href: str=REQ(), log: str=REQ(),
                 more_info: Mapping[str, Any]=REQ(validator=check_dict([]), default={}),
                 ) -> HttpResponse:
    """Accepts an error report and stores in a queue for processing.  The
    actual error reports are later handled by do_report_error"""
    if not settings.BROWSER_ERROR_REPORTING:
        return json_success()
    more_info = dict(more_info)

    js_source_map = get_js_source_map()
    if js_source_map:
        stacktrace = js_source_map.annotate_stacktrace(stacktrace)

    try:
        version: Optional[str] = subprocess.check_output(
            ["git", "log", "HEAD^..HEAD", "--oneline"],
            universal_newlines=True,
        )
    except Exception:
        version = None

    # Get the IP address of the request
    remote_ip = request.META['REMOTE_ADDR']

    # For the privacy of our users, we remove any actual text content
    # in draft_content (from drafts rendering exceptions).  See the
    # comment on privacy_clean_markdown for more details.
    if more_info.get('draft_content'):
        more_info['draft_content'] = privacy_clean_markdown(more_info['draft_content'])

    if user_profile.is_authenticated:
        email = user_profile.delivery_email
        full_name = user_profile.full_name
    else:
        email = "unauthenticated@example.com"
        full_name = "Anonymous User"

    queue_json_publish('error_reports', dict(
        type = "browser",
        report = dict(
            host = SplitResult("", request.get_host(), "", "", "").hostname,
            ip_address = remote_ip,
            user_email = email,
            user_full_name = full_name,
            user_visible = ui_message,
            server_path = settings.DEPLOY_ROOT,
            version = version,
            user_agent = user_agent,
            href = href,
            message = message,
            stacktrace = stacktrace,
            log = log,
            more_info = more_info,
        ),
    ))

    return json_success()

@csrf_exempt
@require_POST
@has_request_variables
def report_csp_violations(request: HttpRequest,
                          csp_report: Dict[str, Any]=REQ(argument_type='body')) -> HttpResponse:
    def get_attr(csp_report_attr: str) -> str:
        return csp_report.get(csp_report_attr, '')

    logging.warning("CSP Violation in Document('%s'). "
                    "Blocked URI('%s'), Original Policy('%s'), "
                    "Violated Directive('%s'), Effective Directive('%s'), "
                    "Disposition('%s'), Referrer('%s'), "
                    "Status Code('%s'), Script Sample('%s')",
                    get_attr('document-uri'),
                    get_attr('blocked-uri'),
                    get_attr('original-policy'),
                    get_attr('violated-directive'),
                    get_attr('effective-directive'),
                    get_attr('disposition'),
                    get_attr('referrer'),
                    get_attr('status-code'),
                    get_attr('script-sample'))

    return json_success()
