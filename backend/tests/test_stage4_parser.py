"""Stage 4 tests: the pure page-analysis brain, on realistic fixture HTML."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.analysis.parser import (  # noqa: E402
    detect_login_wall,
    find_document_links,
    guess_authority,
    guess_doc_type,
    link_extension,
    needs_browser,
    parse_page,
)

# Modeled on an RBI notifications listing: server-rendered, relative and
# absolute links, PDFs on the rbidocs subdomain, plenty of nav noise.
RBI_LISTING = """
<html><head><title>Reserve Bank of India - Notifications</title></head><body>
<a href="/home.aspx">Home</a>
<a href="/Scripts/NotificationUser.aspx">Notifications</a>
<table>
 <tr><td>
   <a href="https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NT1234.PDF">
     Master Direction – Know Your Customer (KYC) Direction, 2016
     (Updated as on July 15, 2026)</a>
 </td></tr>
 <tr><td>
   <a href="/rdocs/notification/PDFs/NT1235.PDF">Amendment to FEMA
     Notification dated 05/03/2026</a>
 </td></tr>
 <tr><td>
   <a href="https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NT1234.PDF">
     Duplicate link to the same circular</a>
 </td></tr>
 <tr><td><a href="mailto:helpdoc@rbi.org.in">Contact</a></td></tr>
 <tr><td><a href="#top">Back to top</a></td></tr>
</table>
<p>The Reserve Bank of India publishes notifications on this page. Banks and
NBFCs are advised to take note of the directions and act accordingly. This
listing is updated as circulars are issued by the various departments.</p>
</body></html>
"""

# Modeled on a SEBI circular page with a DOCX annexure alongside the PDF.
SEBI_PAGE = """
<html><head><title>SEBI | Circulars</title></head><body>
<a href="https://www.sebi.gov.in/sebi_data/attachdocs/jul-2026/circular123.pdf">
  Circular on Listing Obligations and Disclosure Requirements</a>
<a href="attachdocs/jul-2026/annexure-a.docx">Annexure A (formats)</a>
<a href="/legal/circulars">All circulars</a>
<p>Securities and Exchange Board of India issues this circular in exercise of
its powers under section 11 of the SEBI Act to protect investor interests and
regulate the securities market. All recognised stock exchanges shall bring the
provisions of this circular to the notice of listed entities.</p>
</body></html>
"""

# Modeled on the MCA V3 portal shell: an Angular app with no real content
# until JavaScript runs.
MCA_JS_SHELL = """
<html><head><title>MCA</title></head><body>
<app-root></app-root>
<script src="runtime.js"></script>
<script src="polyfills.js"></script>
<script src="main.js"></script>
<noscript>Please enable JavaScript to continue using this application.</noscript>
</body></html>
"""

# Modeled on an MCA login page (rendered): password field + sign-in wording.
MCA_LOGIN = """
<html><head><title>MCA - Sign In</title></head><body>
<h1>Sign In</h1>
<form action="/user/login" method="post">
  <input type="text" name="userName" placeholder="User ID" />
  <input type="password" name="password" placeholder="Password" />
  <button>Login</button>
  <a href="/forgot-password">Forgot Password?</a>
</form>
<p>New user? Register on the MCA portal to access company filing services,
view public documents and make payments. Registered users can sign in with
their user ID and password to continue to the requested service.</p>
</body></html>
"""


class TestParsePage(unittest.TestCase):
    def test_title_links_and_counts(self) -> None:
        page = parse_page(RBI_LISTING)
        self.assertEqual(page.title, "Reserve Bank of India - Notifications")
        hrefs = [href for href, _ in page.links]
        self.assertIn("/rdocs/notification/PDFs/NT1235.PDF", hrefs)
        self.assertFalse(page.has_password_field)
        self.assertGreater(page.text_length, 200)

    def test_anchor_text_is_joined_and_squashed(self) -> None:
        page = parse_page(RBI_LISTING)
        texts = {text for _, text in page.links}
        self.assertIn(
            "Master Direction – Know Your Customer (KYC) Direction, 2016 "
            "(Updated as on July 15, 2026)",
            texts,
        )

    def test_broken_html_never_raises(self) -> None:
        page = parse_page("<a href='x.pdf'>unclosed <b>anchor")
        self.assertEqual(page.links, [("x.pdf", "unclosed anchor")])


class TestFindDocumentLinks(unittest.TestCase):
    def test_rbi_listing_resolves_dedupes_and_filters(self) -> None:
        page = parse_page(RBI_LISTING)
        links = find_document_links(
            "https://www.rbi.org.in/Scripts/NotificationUser.aspx", page
        )
        urls = [link["url"] for link in links]
        self.assertEqual(urls, [
            "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/NT1234.PDF",
            "https://www.rbi.org.in/rdocs/notification/PDFs/NT1235.PDF",
        ])
        self.assertEqual(links[0]["kind"], "pdf")
        # mailto:, #fragment and .aspx nav links are all excluded.

    def test_sebi_page_finds_pdf_and_docx(self) -> None:
        page = parse_page(SEBI_PAGE)
        links = find_document_links(
            "https://www.sebi.gov.in/legal/circulars/jul-2026/x.html", page
        )
        kinds = {link["kind"] for link in links}
        self.assertEqual(kinds, {"pdf", "docx"})
        self.assertTrue(
            all(link["url"].startswith("https://www.sebi.gov.in") for link in links)
        )

    def test_relative_resolution_against_base(self) -> None:
        page = parse_page('<a href="files/a.pdf">A</a>')
        links = find_document_links("https://example.org/section/page.html", page)
        self.assertEqual(links[0]["url"], "https://example.org/section/files/a.pdf")

    def test_link_extension_handles_queries(self) -> None:
        self.assertEqual(link_extension("https://x.in/a/b.PDF?y=1"), "pdf")
        self.assertIsNone(link_extension("https://x.in/a/b?y=1.pdf"))


class TestBrowserAndLoginDetection(unittest.TestCase):
    def test_mca_shell_needs_browser(self) -> None:
        page = parse_page(MCA_JS_SHELL)
        self.assertTrue(needs_browser(MCA_JS_SHELL, page))

    def test_server_rendered_pages_do_not(self) -> None:
        for html in (RBI_LISTING, SEBI_PAGE):
            page = parse_page(html)
            self.assertFalse(needs_browser(html, page))

    def test_mca_login_detected_with_tailored_message(self) -> None:
        page = parse_page(MCA_LOGIN)
        reason = detect_login_wall(
            "https://www.mca.gov.in/content/mca/global/en/foportal/fologin.html",
            page, 200,
        )
        self.assertIsNotNone(reason)
        self.assertIn("MCA", reason)
        self.assertIn("public pages only", reason)

    def test_login_by_url_path(self) -> None:
        page = parse_page("<html><body>redirecting…</body></html>")
        self.assertIsNotNone(
            detect_login_wall("https://portal.example.in/login?next=/docs", page, 200)
        )

    def test_401_is_a_wall_even_without_markers(self) -> None:
        page = parse_page("<html><body>Unauthorized</body></html>")
        self.assertIsNotNone(detect_login_wall("https://x.gov.in/area", page, 401))

    def test_public_pages_are_not_walls(self) -> None:
        page = parse_page(RBI_LISTING)
        self.assertIsNone(
            detect_login_wall("https://www.rbi.org.in/Scripts/x.aspx", page, 200)
        )


class TestGuessing(unittest.TestCase):
    KNOWN = ["RBI", "SEBI", "MCA", "IRDAI", "CBDT", "NCLT"]
    TYPES = ["Master Direction", "Master Circular", "Circular", "Notification",
             "Press Release", "Guidelines"]

    def test_authority_by_domain_beats_title(self) -> None:
        self.assertEqual(
            guess_authority(
                "https://rbidocs.rbi.org.in/rdocs/x.pdf", "SEBI mentioned", self.KNOWN
            ),
            "RBI",
        )
        self.assertEqual(
            guess_authority("https://www.sebi.gov.in/x", "", self.KNOWN), "SEBI"
        )
        self.assertEqual(
            guess_authority("https://www.mca.gov.in/x", "", self.KNOWN), "MCA"
        )
        self.assertEqual(
            guess_authority("https://www.incometax.gov.in/x", "", self.KNOWN), "CBDT"
        )

    def test_authority_from_title_when_domain_unknown(self) -> None:
        self.assertEqual(
            guess_authority(
                "https://news.example.com/x", "New SEBI circular explained", self.KNOWN
            ),
            "SEBI",
        )
        self.assertIsNone(
            guess_authority("https://news.example.com/x", "Nothing here", self.KNOWN)
        )

    def test_doc_type_prefers_longest_match(self) -> None:
        self.assertEqual(
            guess_doc_type("Master Direction – KYC Direction 2016", self.TYPES),
            "Master Direction",
        )
        self.assertEqual(
            guess_doc_type("A circular on margins", self.TYPES), "Circular"
        )
        self.assertIsNone(guess_doc_type("Annual report", self.TYPES))


if __name__ == "__main__":
    unittest.main()
