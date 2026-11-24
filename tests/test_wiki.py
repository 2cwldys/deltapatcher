"""tools/wiki.py: every guide becomes a wiki page, and its links still lead somewhere."""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read  # noqa: E402
import wiki  # noqa: E402  (tools/wiki.py)


class Wiki(TempCase):
    def test_every_guide_is_a_page(self):
        guides = {"docs/" + f for f in os.listdir(os.path.join(ROOT, "docs")) if f.endswith(".md")}
        guides |= {"docker/README.md", "mcp/README.md", "SECURITY.md"}
        self.assertEqual(guides, set(wiki.PAGE_OF), "add the new guide to PAGES in tools/wiki.py")

    def test_the_links_lead_somewhere(self):
        names = wiki.write_wiki(self.p("wiki"))
        pages = {n[:-3] for n in names}
        for name in names:
            text = read(self.p("wiki", name)).decode("utf-8")
            outside = re.sub(r"```.*?```", "", text, flags=re.S)       # links in code examples are text
            for link in re.findall(r"\]\(([^)\s]+)\)", outside):        # every link, a picture's included
                if link.startswith(("https://", "http://", "mailto:", "#")):
                    continue
                self.assertIn(link.split("#")[0], pages, "%s: %s leads nowhere" % (name, link))
        home = read(self.p("wiki", "Home.md")).decode("utf-8")
        self.assertIn("(Quick-start)", home)
        picture = "https://github.com/%s/raw/main/docs/images/app-update.png" % wiki.REPO
        self.assertIn("[![The Update page](%s)](%s)" % (picture, picture), home)   # a click opens it full size
        self.assertFalse(home.startswith("# "))                         # the wiki shows the title itself
        self.assertIn("(Setting-up-a-VPS)", read(self.p("wiki", "_Sidebar.md")).decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
