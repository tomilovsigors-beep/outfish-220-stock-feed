import unittest
from unittest.mock import patch
import app


def row(**quantities):
    return {"id": "variant", "status": "ACTIVE", "available": quantities.get("main", 0), **quantities}


class DeliveryTests(unittest.TestCase):
    def result(self, **quantities):
        return app.storefront_availability("SKU", {"SKU": [row(**quantities)]})

    def test_physical_store_takes_priority(self):
        self.assertEqual(self.result(main=2, lowa=3, fjord=4, gediminas=5),
                         {"status": "store", "hours": 24, "source": "store", "stock": 2})

    def test_lowa_remains_two_to_three_days(self):
        self.assertEqual(self.result(lowa=3, gediminas=5)["status"], "lowa")

    def test_fjord_unchanged(self):
        self.assertEqual(self.result(fjord=4)["source"], "fjord")

    def test_gediminas_uses_existing_three_to_five_day_message(self):
        self.assertEqual(self.result(gediminas=5),
                         {"status": "fjord", "hours": 72, "source": "gediminas", "stock": 5})

    def test_zero_and_negative_stock_do_not_promise_delivery(self):
        for quantities in ({}, {"gediminas": 0}, {"gediminas": -1}):
            self.assertEqual(self.result(**quantities)["status"], "unavailable")

    def test_ambiguous_or_inactive_sku_remains_unavailable(self):
        for rows in ([row(gediminas=5), row(main=2)], [{**row(gediminas=5), "status": "DRAFT"}]):
            self.assertEqual(app.storefront_availability("SKU", {"SKU": rows})["status"], "unavailable")

    def test_marketplace_stock_still_uses_physical_store_only(self):
        self.assertEqual(app.resolve_stock("SKU", {"SKU": [row(gediminas=5)]}), 0)
        self.assertEqual(app.resolve_stock("SKU", {"SKU": [row(main=2, gediminas=5)]}), 2)

    def test_availability_route_and_cors(self):
        with patch.object(app, "fetch_shopify_variants", return_value={"SKU": [row(gediminas=5)]}):
            response = app.app.test_client().get("/availability?sku=SKU")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["source"], "gediminas")
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")

    def test_upstream_failure_is_not_a_delivery_promise(self):
        with patch.object(app, "fetch_shopify_variants", side_effect=RuntimeError("upstream unavailable")):
            response = app.app.test_client().get("/availability?sku=SKU")
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
