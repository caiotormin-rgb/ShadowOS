"""Can the transport be talked into reading content? It must not be possible."""
import io
import json
import unittest
import urllib.error

from drivectx.drive import (FILE_FIELDS, DriveReadOnly, ForbiddenEndpoint,
                            TransportError)


class FakeTokens:
    def bearer(self):
        return "ya29.FAKE"


class Recorder:
    """Stands in for the network and records every request attempted."""

    def __init__(self, payload=None, raise_http=None):
        self.requests = []
        self.payload = payload if payload is not None else {}
        self.raise_http = raise_http

    def __call__(self, url, headers, timeout):
        self.requests.append((url, headers))
        if self.raise_http:
            raise self.raise_http
        return 200, json.dumps(self.payload).encode()


def http_error(code, body=None):
    payload = io.BytesIO(json.dumps(body).encode()) if body is not None else None
    return urllib.error.HTTPError("u", code, "msg", {}, payload)


class CeilingTest(unittest.TestCase):
    def api(self, payload=None, raise_http=None, **kw):
        self.rec = Recorder(payload, raise_http)
        return DriveReadOnly(FakeTokens(), opener=self.rec, **kw)

    # -- the refusals ----------------------------------------------------
    def test_content_and_mutation_paths_are_refused(self):
        api = self.api()
        for path in ("/files/abc/export",
                     "/files/abc/revisions",
                     "/files/abc/comments",
                     "/files/abc/permissions",
                     "/files/abc/copy",
                     "/files/abc/watch",
                     "/files/trash",
                     "/changes/watch",
                     "/files/generateIds",
                     "/upload/files"):
            with self.subTest(path=path):
                with self.assertRaises(ForbiddenEndpoint):
                    api._get(path)
        self.assertEqual(self.rec.requests, [], "no refused path may reach the network")

    def test_unknown_paths_are_refused_even_when_harmless(self):
        """The allowlist is a whitelist, not a blacklist."""
        api = self.api()
        for path in ("/teamdrives", "/drives", "/files/", "/", "/../files"):
            with self.subTest(path=path):
                with self.assertRaises(ForbiddenEndpoint):
                    api._get(path)

    def test_path_traversal_cannot_escape_the_allowlist(self):
        api = self.api()
        with self.assertRaises(ForbiddenEndpoint):
            api._get("/files/..%2F..%2Fexport")

    def test_a_file_id_shaped_like_a_subpath_is_refused(self):
        """A hostile file id must not smuggle in a second path segment."""
        api = self.api()
        with self.assertRaises(ForbiddenEndpoint):
            api.get_file("abc/export")

    def test_a_file_id_that_merely_starts_with_a_verb_is_still_allowed(self):
        """The refusal must be precise. Blocking a real id whose first letters
        spell 'copy' would put a silent hole in the index."""
        api = self.api({"id": "copyright-notes"})
        api.get_file("copyright-notes")
        self.assertEqual(len(self.rec.requests), 1)

    # -- the parameter ceiling, which is the one that matters for Drive ---
    def test_the_media_download_parameter_is_refused(self):
        """Drive's content door is a query parameter, not a path. A path-only
        ceiling would let `?alt=media` straight through."""
        api = self.api()
        with self.assertRaises(ForbiddenEndpoint):
            api._get("/files/abc", {"alt": "media"})
        self.assertEqual(self.rec.requests, [])

    def test_percent_encoded_media_parameter_is_refused(self):
        api = self.api()
        with self.assertRaises(ForbiddenEndpoint):
            api._get("/files/abc", {"fields": "id&alt%3Dmedia"})

    def test_upload_and_abuse_parameters_are_refused(self):
        api = self.api()
        for params in ({"uploadType": "media"}, {"acknowledgeAbuse": "true"},
                       {"alt": "json"}):
            with self.subTest(params=params):
                with self.assertRaises(ForbiddenEndpoint):
                    api._get("/files/abc", params)

    def test_field_mask_asking_for_content_or_a_checksum_is_refused(self):
        api = self.api()
        for mask in ("id,thumbnailLink", "id,md5Checksum", "id,webContentLink",
                     "id,exportLinks", "id,resourceKey", "id,iconLink",
                     "permissions(type,emailAddress)"):
            with self.subTest(mask=mask):
                with self.assertRaises(ForbiddenEndpoint):
                    api._get("/files/abc", {"fields": mask})

    def test_the_real_field_mask_passes_its_own_check(self):
        """The production mask must not be caught by the tripwire meant for
        additions to it -- owners(emailAddress) is legitimate, and a check that
        cannot tell it from permissions(emailAddress) is useless."""
        api = self.api({"id": "x"})
        api.get_file("x")
        self.assertIn("owners", self.rec.requests[0][0])

    def test_the_field_mask_never_asks_for_a_grantee_address(self):
        """The minimization that counts happens at the request: an address that
        Google never sends cannot be stored, logged, or leaked."""
        self.assertIn("permissions(", FILE_FIELDS)
        perms = FILE_FIELDS.split("permissions(", 1)[1].split(")", 1)[0]
        self.assertNotIn("email", perms.lower())
        self.assertNotIn("displayName", perms)

    def test_every_http_request_in_the_module_is_a_get(self):
        """Structural, not textual: inspect each Request() call in the AST.

        Counting occurrences of the string `method="GET"` would also match this
        module's docstring. Counting prose is not a safety check.
        """
        import ast
        import inspect
        from drivectx import drive
        tree = ast.parse(inspect.getsource(drive))
        requests = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("Request")]
        self.assertTrue(requests, "expected at least one urllib Request call")
        for call in requests:
            methods = [kw.value for kw in call.keywords if kw.arg == "method"]
            self.assertEqual(len(methods), 1, "Request() without an explicit method")
            self.assertIsInstance(methods[0], ast.Constant)
            self.assertEqual(methods[0].value, "GET",
                             f"non-GET request in transport: {ast.unparse(call)[:60]}")

    # -- shared drives ----------------------------------------------------
    def test_shared_drives_are_excluded_by_default(self):
        api = self.api({"files": []})
        self.assertFalse(api.include_shared_drives, "the default must be false")
        api.list_files()
        url = self.rec.requests[0][0]
        self.assertIn("includeItemsFromAllDrives=false", url)
        self.assertIn("supportsAllDrives=false", url)
        self.assertIn("restrictToMyDrive=true", url)
        self.assertIn("spaces=drive", url)

    def test_shared_drives_can_be_included_only_by_the_named_setting(self):
        api = self.api({"files": []}, include_shared_drives=True)
        api.list_files()
        url = self.rec.requests[0][0]
        self.assertIn("includeItemsFromAllDrives=true", url)
        self.assertIn("restrictToMyDrive=false", url)

    # -- the permitted surface still works -------------------------------
    def test_allowed_reads_reach_the_network(self):
        api = self.api({"startPageToken": "tok"})
        self.assertEqual(api.start_page_token(), "tok")
        self.assertEqual(len(self.rec.requests), 1)
        self.assertIn("/changes/startPageToken", self.rec.requests[0][0])

    def test_bearer_is_sent_as_a_header_never_in_the_url(self):
        api = self.api({"files": []})
        api.list_files()
        url, headers = self.rec.requests[0]
        self.assertNotIn("ya29", url)
        self.assertEqual(headers["Authorization"], "Bearer ya29.FAKE")

    def test_changes_requests_removed_items_so_deletions_are_seen(self):
        api = self.api({"changes": []})
        api.list_changes("tok")
        self.assertIn("includeRemoved=true", self.rec.requests[0][0])

    def test_root_folder_id_is_a_metadata_read(self):
        api = self.api({"id": "root-abc"})
        self.assertEqual(api.root_folder_id(), "root-abc")
        self.assertIn("/files/root", self.rec.requests[0][0])

    def test_paginate_walks_pages_then_stops(self):
        pages = [{"files": [{"id": "a"}], "nextPageToken": "t1"}, {"files": [{"id": "b"}]}]
        seen = []

        def fn(page_token=None):
            seen.append(page_token)
            return pages[len(seen) - 1]

        api = self.api()
        self.assertEqual([f["id"] for f in api.paginate(fn, "files")], ["a", "b"])
        self.assertEqual(seen, [None, "t1"])

    # -- error mapping ---------------------------------------------------
    def test_http_errors_map_to_classes_without_bodies(self):
        cases = {401: "auth", 403: "forbidden", 404: "not_found",
                 429: "rate_limited", 500: "http_error"}
        for code, expected in cases.items():
            with self.subTest(code=code):
                api = self.api(raise_http=http_error(code))
                with self.assertRaises(TransportError) as cm:
                    api.about()
                self.assertEqual(cm.exception.error_class, expected)
                self.assertNotIn("msg", str(cm.exception))

    def test_expired_page_token_is_recognised_as_410(self):
        """Drive rejects a stale Changes cursor rather than serving it. That is
        an expected condition with a bounded recovery, not a failure."""
        api = self.api(raise_http=http_error(410))
        with self.assertRaises(TransportError) as cm:
            api.list_changes("old")
        self.assertEqual(cm.exception.error_class, "stale_page_token")

    def test_expired_page_token_is_recognised_as_400_with_a_reason(self):
        api = self.api(raise_http=http_error(
            400, {"error": {"errors": [{"reason": "invalidPageToken"}]}}))
        with self.assertRaises(TransportError) as cm:
            api.list_changes("old")
        self.assertEqual(cm.exception.error_class, "stale_page_token")

    def test_an_ordinary_400_is_not_mistaken_for_an_expired_token(self):
        api = self.api(raise_http=http_error(
            400, {"error": {"errors": [{"reason": "invalidParameter"}]}}))
        with self.assertRaises(TransportError) as cm:
            api.list_changes("old")
        self.assertEqual(cm.exception.error_class, "bad_request")

    def test_drive_403_rate_limiting_is_not_mistaken_for_permission_denied(self):
        for reason in ("userRateLimitExceeded", "rateLimitExceeded", "quotaExceeded"):
            with self.subTest(reason=reason):
                api = self.api(raise_http=http_error(
                    403, {"error": {"errors": [{"reason": reason}]}}))
                with self.assertRaises(TransportError) as cm:
                    api.about()
                self.assertEqual(cm.exception.error_class, "rate_limited")

    def test_error_classification_never_leaks_the_body(self):
        api = self.api(raise_http=http_error(
            403, {"error": {"errors": [{"reason": "x", "message": "SENSITIVE-ECHO"}]}}))
        with self.assertRaises(TransportError) as cm:
            api.about()
        self.assertNotIn("SENSITIVE", str(cm.exception))

    def test_a_hostile_reason_string_is_not_carried_into_the_error_class(self):
        """The reason is the one field read out of an error body, so it is
        validated rather than trusted."""
        api = self.api(raise_http=http_error(
            400, {"error": {"errors": [{"reason": "x" * 500}]}}))
        with self.assertRaises(TransportError) as cm:
            api.list_changes("old")
        self.assertEqual(cm.exception.error_class, "bad_request")

    def test_network_failure_is_a_class_not_a_traceback(self):
        api = self.api(raise_http=urllib.error.URLError("no route"))
        with self.assertRaises(TransportError) as cm:
            api.about()
        self.assertEqual(cm.exception.error_class, "network")


if __name__ == "__main__":
    unittest.main()
