import functools
import http.server
import shutil
import sys
import threading
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

HERE = Path(__file__).resolve().parent
RESERVED_PORTS = {8765, 8737, 8787}


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _serve(directory):
    handler = functools.partial(_Quiet, directory=str(directory))
    while True:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        if server.server_address[1] not in RESERVED_PORTS:
            break
        server.server_close()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="session")
def fixture_site(tmp_path_factory):
    """Serve the fixture site and a second origin with one stylesheet."""
    root = tmp_path_factory.mktemp("critical_css_site")
    site = root / "site"
    ext = root / "ext"
    shutil.copytree(HERE / "fixture_site", site)
    shutil.copytree(HERE / "fixture_ext", ext)
    ext_server = _serve(ext)
    ext_origin = "http://127.0.0.1:%d" % ext_server.server_address[1]
    index = site / "index.html"
    index.write_text(index.read_text(encoding="utf-8").replace("{{EXT}}", ext_origin),
                     encoding="utf-8")
    site_server = _serve(site)
    info = {
        "root": site,
        "url": "http://127.0.0.1:%d/index.html" % site_server.server_address[1],
        "ext_origin": ext_origin,
    }
    yield info
    for server in (site_server, ext_server):
        server.shutdown()
        server.server_close()
