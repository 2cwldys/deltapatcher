"""Failing tests as GitHub annotations:  python tools/ci_annotate.py tests.log

Reads unittest's output and prints one ::error line per failing test, its traceback included. An
annotation shows on the run's page and can be read through GitHub's API without signing in; a job's
log cannot.
"""

import re
import sys


def _data(text):
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def _prop(text):
    return _data(text).replace(":", "%3A").replace(",", "%2C")


def annotations(text):
    """One (title, traceback) for each ERROR: or FAIL: block of unittest's output."""
    out = []
    for block in re.split(r"^={40,}$", text, flags=re.M)[1:]:
        head, _, rest = block.strip("\n").partition("\n")
        if not head.startswith(("ERROR:", "FAIL:")):
            continue
        rest = re.sub(r"^-{40,}\n", "", rest, count=1)
        body = re.split(r"^-{40,}$", rest, flags=re.M)[0].strip()      # the last block ends at the summary
        out.append((head.strip(), body))
    return out


def main(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    found = annotations(text)
    for title, body in found:
        print("::error title=%s::%s" % (_prop(title)[:250], _data(body[-8000:])))
    if not found:                                       # no unittest summary: show the end of the output
        print("::error title=the tests failed::%s" % _data(text[-8000:]))


if __name__ == "__main__":
    main(sys.argv[1])
