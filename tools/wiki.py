"""The GitHub wiki, made from the guides (docs/, docker/README.md, SECURITY.md), which stay the one place
to edit.

    python tools/wiki.py FOLDER        write the wiki's pages into FOLDER

The wiki is a repository of its own. Once: create its first page on GitHub (the repository's Wiki tab,
"Create the first page", any text), then clone it:
    git clone https://github.com/2cwldys/deltapatcher.wiki.git
Then, whenever the guides change: run this into the clone, commit and push there.

Links between guides become links between wiki pages; pictures point at the repository's own copies (they
are in Git LFS, which /raw/ serves); links to other files point at them on GitHub.
"""

import os
import posixpath
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from deltapatcher import REPO  # noqa: E402

BRANCH = "main"
# (the guide, the wiki page's name, its title in the sidebar)
PAGES = [
    ("docs/README.md", "Home", "Home"),
    ("docs/QUICKSTART.md", "Quick-start", "Quick start"),
    ("docs/PLAYERS.md", "For-players", "For players"),
    ("docs/PUBLISHING.md", "Publishing-a-release", "Publishing a release"),
    ("docs/VPS-SETUP.md", "Setting-up-a-VPS", "Setting up a VPS"),
    ("docs/CLI.md", "Command-line", "Command line"),
    ("docker/README.md", "Docker-and-Pterodactyl", "Docker and Pterodactyl"),
    ("docs/FORMAT.md", "Store-and-manifest-format", "The store and manifest"),
    ("docs/TROUBLESHOOTING.md", "Troubleshooting", "Troubleshooting"),
    ("SECURITY.md", "Security", "Security"),
]
PAGE_OF = {src: name for src, name, _title in PAGES}
LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")
LINKED_PICTURE = re.compile(r"\[!\[([^\]]*)\]\(([^)\s]+)\)\]\(([^)\s]+)\)")    # [![text](picture)](where a click goes)
PICTURE = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


def target(src, link):
    """A link in the guide `src`, as the wiki needs it."""
    if re.match(r"^[a-z][a-z0-9+.-]*:", link, re.I) or link.startswith("#"):
        return link                                            # https:, mailto:, or a heading of this page
    path, _, anchor = link.partition("#")
    full = posixpath.normpath(posixpath.join(posixpath.dirname(src), path)) if path else src
    if full in PAGE_OF:
        return PAGE_OF[full] + ("#" + anchor if anchor else "")
    if full.lower().endswith(PICTURE):
        return "https://github.com/%s/raw/%s/%s" % (REPO, BRANCH, full)
    kind = "tree" if os.path.isdir(os.path.join(ROOT, *full.split("/"))) else "blob"
    return "https://github.com/%s/%s/%s/%s" % (REPO, kind, BRANCH, full) + ("#" + anchor if anchor else "")


def page(src):
    with open(os.path.join(ROOT, *src.split("/")), encoding="utf-8") as f:
        lines = f.read().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]                                      # the wiki shows the page's name as its title
        while lines and not lines[0].strip():
            lines = lines[1:]
    out, fenced = [], False
    for line in lines:
        if line.lstrip().startswith("```"):
            fenced = not fenced
        out.append(line if fenced else links(src, line))
    return "\n".join(out) + "\n"


def links(src, line):
    """The line's links and pictures as the wiki needs them. A picture inside a link is done first, both
    addresses at once; target() leaves a full address alone, so the second pass changes nothing there."""
    line = LINKED_PICTURE.sub(lambda m: "[![%s](%s)](%s)" % (m.group(1), target(src, m.group(2)), target(src, m.group(3))), line)
    return LINK.sub(lambda m: "%s[%s](%s)" % (m.group(1), m.group(2), target(src, m.group(3))), line)


def write_wiki(folder):
    os.makedirs(folder, exist_ok=True)
    written = []
    for src, name, _title in PAGES:
        with open(os.path.join(folder, name + ".md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(page(src))
        written.append(name + ".md")
    with open(os.path.join(folder, "_Sidebar.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("**DeltaPatcher**\n\n" + "".join("- [%s](%s)\n" % (title, name) for _s, name, title in PAGES))
    with open(os.path.join(folder, "_Footer.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("Made from the repository's guides by `tools/wiki.py`: edit them there, not here. "
                "[The repository](https://github.com/%s)\n" % REPO)
    return written + ["_Sidebar.md", "_Footer.md"]


def main(argv):
    if len(argv) != 1:
        sys.exit(__doc__)
    for name in write_wiki(argv[0]):
        print(os.path.join(argv[0], name))


if __name__ == "__main__":
    main(sys.argv[1:])
