"""Small fail-closed stdlib HTTP helpers for credentialed WebUI probes."""
from __future__ import annotations

import urllib.request


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn every HTTP redirect into the original ``HTTPError`` response."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())
_STDLIB_URLOPEN = urllib.request.urlopen


def credentialed_urlopen(request, *, timeout):
    """Open a credentialed request without following a redirect.

    Explicit ``urllib.request.urlopen`` test doubles remain supported at the
    established stdlib seam. Production requests always use the private opener,
    so a redirect cannot contact a second origin with request identity.
    """
    if urllib.request.urlopen is not _STDLIB_URLOPEN:
        return urllib.request.urlopen(request, timeout=timeout)
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)
