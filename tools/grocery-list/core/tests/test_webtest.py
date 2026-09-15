import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import grocery
import webtest


class WebAppTestCase(unittest.TestCase):
    """Drives webtest's routing functions against a throwaway database."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.app = webtest.GroceryWebApp(self.db)

    def tearDown(self):
        self.temp.cleanup()

    # -- helpers -----------------------------------------------------------

    def get(self, path="/", **query):
        return self.app.handle_get(path, {k: v for k, v in query.items()})

    def post(self, path, **form):
        return self.app.handle_post(path, {k: v for k, v in form.items()})

    def redirect_params(self, response):
        self.assertEqual(response.status, 303)
        return {k: v[0] for k, v in
                parse_qs(urlparse(response.location).query, keep_blank_values=True).items()}

    def page(self, **query):
        response = self.get("/", **query)
        self.assertEqual(response.status, 200)
        return response.text

    def rows(self, store):
        with grocery.connect(self.db) as conn:
            _, rows = grocery.current_items(conn, store)
            return [(r["name"], r["unit"], r["status"], r["quantity"]) for r in rows]

    def events(self):
        with grocery.connect(self.db) as conn:
            return [
                (r["item_name"], r["action"], r["actor"])
                for r in conn.execute("SELECT * FROM events ORDER BY id")
            ]


class RoutingTests(WebAppTestCase):
    def test_index_without_a_store_prompts_for_one(self):
        body = self.page()
        self.assertIn("Pick or create a store", body)
        self.assertIn("<!doctype html>", body)

    def test_create_store_then_add_buy_and_close(self):
        created = self.post("/store", store="costco", actor="owner")
        self.assertEqual(self.redirect_params(created)["store"], "Costco")

        added = self.post(
            "/add", store="Costco", actor="owner",
            items="milk | 2 | gal | 2%\neggs\n",
        )
        params = self.redirect_params(added)
        self.assertEqual(params["store"], "Costco")
        self.assertIn("added Milk, Eggs", params["notice"])
        self.assertEqual(
            sorted(self.rows("Costco")),
            [("Eggs", "", "needed", 1.0), ("Milk", "gal", "needed", 2.0)],
        )

        body = self.page(store="Costco")
        self.assertIn("Needed at Costco", body)
        self.assertIn("Milk", body)
        self.assertIn("2%", body)

        bought = self.post("/buy", store="Costco", name="Milk", unit="gal", actor="owner")
        self.assertIn("Milk", self.redirect_params(bought)["notice"])
        self.assertEqual(
            dict((n, s) for n, _u, s, _q in self.rows("Costco")),
            {"Milk": "purchased", "Eggs": "needed"},
        )
        self.assertIn("Purchased (this trip)", self.page(store="Costco"))

        closed = self.post("/close", store="Costco", actor="owner")
        notice = self.redirect_params(closed)["notice"]
        self.assertIn("1 purchased", notice)
        self.assertIn("1 carried forward", notice)
        # Purchased item is archived; the missing one rolls forward.
        self.assertEqual([n for n, _u, _s, _q in self.rows("Costco")], ["Eggs"])

    def test_unbuy_puts_an_item_back(self):
        self.post("/add", store="Costco", items="milk")
        self.post("/buy", store="Costco", name="Milk", unit="")
        self.assertEqual(self.rows("Costco")[0][2], "purchased")
        self.post("/unbuy", store="Costco", name="Milk", unit="")
        self.assertEqual(self.rows("Costco")[0][2], "needed")
        self.assertIn(("Milk", "unpurchased", None), self.events())

    def test_remove_deletes_the_row(self):
        self.post("/add", store="Costco", items="milk\neggs")
        removed = self.post("/remove", store="Costco", name="Eggs", unit="")
        self.assertIn("removed Eggs", self.redirect_params(removed)["notice"])
        self.assertEqual([n for n, _u, _s, _q in self.rows("Costco")], ["Milk"])

    def test_posted_unit_disambiguates_same_named_rows(self):
        self.post("/add", store="Costco", items="milk | 1 | gal\nmilk | 2 | L")
        self.assertEqual(len(self.rows("Costco")), 2)
        self.post("/buy", store="Costco", name="Milk", unit="gal")
        self.assertEqual(
            {u: s for _n, u, s, _q in self.rows("Costco")},
            {"gal": "purchased", "l": "needed"},
        )

    def test_buy_without_a_unit_assumes_and_says_so(self):
        """Reversible action: the engine picks a row and states the choice."""
        self.post("/add", store="Costco", items="milk | 1 | gal\nmilk | 2 | L")
        response = self.app.handle_post("/buy", {"store": "Costco", "name": "Milk"})
        params = self.redirect_params(response)
        self.assertNotIn("error", params)
        self.assertIn("matched 2 rows", params["assumed"])
        body = self.page(store="Costco", assumed=params["assumed"])
        self.assertIn("Assumed:", body)
        self.assertIn("matched 2 rows", body)
        self.assertEqual(
            sorted(s for _n, _u, s, _q in self.rows("Costco")),
            ["needed", "purchased"],
        )

    def test_remove_without_a_unit_still_refuses(self):
        """Destructive action: no undo, so ambiguity stays an error."""
        self.post("/add", store="Costco", items="milk | 1 | gal\nmilk | 2 | L")
        response = self.app.handle_post("/remove", {"store": "Costco", "name": "Milk"})
        params = self.redirect_params(response)
        self.assertIn("matches 2 items", params["error"])
        self.assertEqual(len(self.rows("Costco")), 2)
        self.assertIn("matches 2 items", self.page(store="Costco", error=params["error"]))

    def test_buying_an_unlisted_item_adds_it_as_purchased(self):
        self.post("/add", store="Costco", items="milk")
        response = self.app.handle_post(
            "/buy", {"store": "Costco", "name": "batteries", "actor": "owner"}
        )
        params = self.redirect_params(response)
        self.assertIn("was not on the list", params["assumed"])
        self.assertIn(("Batteries", "", "purchased", 1.0), self.rows("Costco"))
        self.assertIn(("Batteries", "added", "owner"), self.events())
        self.assertIn(("Batteries", "purchased", "owner"), self.events())

    def test_no_store_falls_back_to_the_last_one_touched(self):
        self.post("/add", store="ShopRite", items="bananas")
        self.post("/add", store="Costco", items="milk")

        landing = self.get("/")
        self.assertEqual(landing.status, 200)
        self.assertIn("Needed at Costco", landing.text)
        self.assertIn("last store touched", landing.text)

        added = self.app.handle_post("/add", {"items": "eggs"})
        params = self.redirect_params(added)
        self.assertEqual(params["store"], "Costco")
        self.assertIn("last store touched", params["assumed"])
        self.assertIn("Eggs", [n for n, _u, _s, _q in self.rows("Costco")])

    def test_no_store_and_nothing_to_guess_is_an_error(self):
        response = self.app.handle_post("/add", {"items": "milk"})
        self.assertIn("no store given", self.redirect_params(response)["error"])
        # The landing page just shows the picker instead of shouting.
        body = self.page()
        self.assertIn("Pick or create a store", body)
        self.assertNotIn("banner error", body)

    def test_actor_is_recorded_on_every_mutation(self):
        self.post("/add", store="Costco", items="milk", actor="owner")
        self.post("/buy", store="Costco", name="Milk", unit="", actor="mom")
        self.assertEqual(
            self.events(), [("Milk", "added", "owner"), ("Milk", "purchased", "mom")]
        )

    def test_blank_actor_stays_null(self):
        self.post("/add", store="Costco", items="milk", actor="   ")
        self.assertEqual(self.events(), [("Milk", "added", None)])

    def test_grocery_errors_render_as_a_banner_not_a_traceback(self):
        empty = self.post("/store", store="Costco")
        self.assertEqual(self.redirect_params(empty)["store"], "Costco")
        closed = self.post("/close", store="Costco")
        self.assertIn("cannot close an empty list", self.redirect_params(closed)["error"])

        response = self.get("/", store="Nope")
        self.assertEqual(response.status, 200)
        self.assertIn("unknown store", response.text)
        self.assertNotIn("Traceback", response.text)

    def test_bad_quantity_is_an_error_not_a_crash(self):
        response = self.post("/add", store="Costco", items="milk | lots")
        self.assertIn("invalid quantity", self.redirect_params(response)["error"])

    def test_empty_add_is_rejected(self):
        response = self.post("/add", store="Costco", items="   \n\n")
        self.assertIn("at least one item", self.redirect_params(response)["error"])

    def test_unknown_route_is_404(self):
        self.assertEqual(self.get("/nope").status, 404)
        self.assertEqual(self.post("/nope", store="Costco").status, 404)


class HistoryViewTests(WebAppTestCase):
    def test_history_lists_events_newest_first(self):
        self.post("/add", store="Costco", items="milk\neggs", actor="owner")
        self.post("/buy", store="Costco", name="Eggs", unit="", actor="mom")
        response = self.get("/history", store="Costco")
        self.assertEqual(response.status, 200)
        body = response.text
        self.assertIn("purchased", body)
        self.assertIn("added", body)
        self.assertIn("mom", body)
        self.assertIn("owner", body)
        # newest first: the buy row precedes the two add rows
        self.assertLess(body.index(">purchased<"), body.index(">added<"))

    def test_history_filters_by_store(self):
        self.post("/add", store="Costco", items="milk")
        self.post("/add", store="ShopRite", items="bananas")
        costco = self.get("/history", store="Costco").text
        self.assertIn("Milk", costco)
        self.assertNotIn("Bananas", costco)
        everything = self.get("/history").text
        self.assertIn("Milk", everything)
        self.assertIn("Bananas", everything)

    def test_history_survives_close_and_remove(self):
        self.post("/add", store="Costco", items="milk\nbananas")
        self.post("/buy", store="Costco", name="Milk", unit="")
        self.post("/close", store="Costco")
        self.post("/remove", store="Costco", name="Bananas", unit="")
        body = self.get("/history", store="Costco").text
        self.assertIn("trip_purchased", body)
        self.assertIn("removed", body)


class EscapingTests(WebAppTestCase):
    HOSTILE = '<script>alert("xss")</script>'

    def test_item_names_are_escaped_on_the_list(self):
        self.post("/add", store="Costco", items=f"{self.HOSTILE} | 1 |  | note & <b>bold</b>")
        body = self.page(store="Costco")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", body)
        # and in the form value that reposts it
        self.assertNotIn('value="<script>', body)

    def test_item_names_are_escaped_in_history(self):
        self.post("/add", store="Costco", items=self.HOSTILE, actor='<i>owner</i>')
        body = self.get("/history", store="Costco").text
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("&lt;i&gt;owner&lt;/i&gt;", body)

    def test_store_names_and_banners_are_escaped(self):
        body = self.page(store="<em>Nope</em>", error='<img src=x onerror="alert(1)">')
        self.assertNotIn("<img src=x", body)
        self.assertIn("&lt;img src=x", body)
        self.assertNotIn("<em>Nope</em>", body)


class LiveServerTests(unittest.TestCase):
    """Boots the real ThreadingHTTPServer on an ephemeral localhost port."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.httpd = webtest.make_server(self.db, 0)
        self.host, self.port = self.httpd.server_address[:2]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()
        self.temp.cleanup()

    def url(self, path="/"):
        return f"http://{self.host}:{self.port}{path}"

    def test_binds_to_loopback_only(self):
        self.assertEqual(self.host, "127.0.0.1")
        self.assertEqual(webtest.HOST, "127.0.0.1")

    def test_serves_the_index_page(self):
        with urllib.request.urlopen(self.url("/"), timeout=10) as response:
            self.assertEqual(response.status, 200)
            body = response.read().decode("utf-8")
        self.assertIn("Grocery list", body)
        self.assertIn("Pick or create a store", body)

    def test_post_add_round_trips_over_http(self):
        data = urllib.parse.urlencode(
            {"store": "Costco", "items": "milk | 2 | gal", "actor": "owner"}
        ).encode()
        request = urllib.request.Request(self.url("/add"), data=data, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            # urllib follows the 303 to the list page
            self.assertEqual(response.status, 200)
            body = response.read().decode("utf-8")
        self.assertIn("Milk", body)
        self.assertIn("gal", body)
        with urllib.request.urlopen(self.url("/history"), timeout=10) as response:
            history = response.read().decode("utf-8")
        self.assertIn("owner", history)


if __name__ == "__main__":
    unittest.main()
