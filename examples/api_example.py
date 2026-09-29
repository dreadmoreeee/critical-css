"""Use critical-css from Python.

    python examples/api_example.py path/to/index.html
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from critical_css import Options, run  # noqa: E402


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python examples/api_example.py URL_OR_HTML_FILE")
    target = sys.argv[1]
    result = run(target, Options(viewports=((390, 844), (1366, 768)), delay=1.0))
    rep = result.report
    print("critical %d B (gzip %d B) of %d B full CSS" % (
        rep["critical"]["bytes"], rep["critical"]["gzip"], rep["full"]["bytes"]))
    print("style-src", rep["csp"]["style_src"])
    for r in rep["validation"]["results"]:
        print(r["viewport"], "critical-only diff %.2f%%" % r["critical_only_diff_percent"])
    Path("critical.css").write_text(result.critical_css, encoding="utf-8")


if __name__ == "__main__":
    main()
