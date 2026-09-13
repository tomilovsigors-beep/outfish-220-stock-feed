import csv
import io
import os
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

import requests
from flask import Flask, Response

app = Flask(__name__)

SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE", "153ac6-2.myshopify.com").strip()
SHOPIFY_CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
SHOPIFY_CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-07").strip()
MAIN_LOCATION_ID = os.environ.get(
    "SHOPIFY_MAIN_LOCATION_ID", "gid://shopify/Location/84891861330"
).strip()
FEED_CSV = os.environ.get("FEED_CSV", os.path.join(os.path.dirname(__file__), "sia_fhm.csv"))
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "300"))

_cache = {"ts": 0, "variants": None}
_token_cache = {"token": None, "expires_at": 0}

QUERY = """
query VariantsFor220($after: String, $locationId: ID!) {
  productVariants(first: 250, after: $after) {
    nodes {
      sku
      product { status }
      inventoryItem {
        inventoryLevel(locationId: $locationId) {
          quantities(names: [\"available\"]) { quantity }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _num(value):
    try:
        return int(Decimal(str(value or 0)))
    except (InvalidOperation, ValueError):
        return 0


def _price(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        d = Decimal(value.replace(",", "."))
        return format(d, "f")
    except InvalidOperation:
        return value


def load_feed_rows():
    rows = []
    with open(FEED_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row = {str(k).strip(): ("" if v is None else str(v).strip()) for k, v in raw.items()}
            sku = row.get("sku", "")
            ean = row.get("gtin", "")
            before = _price(row.get("(1) price before discount", ""))
            after = _price(row.get("(2) price after discount", "")) or before
            hours = row.get("collectionhours", "").strip() or "72"
            if sku and ean and before:
                rows.append({
                    "sku": sku,
                    "ean": ean,
                    "before": before,
                    "after": after,
                    "hours": hours,
                })
    return rows


def get_shopify_access_token():
    now = time.time()
    cached = _token_cache.get("token")
    # Refresh five minutes before Shopify says the token expires.
    if cached and now < _token_cache.get("expires_at", 0) - 300:
        return cached

    if not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
        raise RuntimeError("SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET are not configured")

    token_url = f"https://{SHOPIFY_STORE}/admin/oauth/access_token"
    r = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    token = (body.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Shopify did not return an access token")

    expires_in = int(body.get("expires_in") or 86399)
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in
    return token


def fetch_shopify_variants():
    now = time.time()
    if _cache["variants"] is not None and now - _cache["ts"] < CACHE_SECONDS:
        return _cache["variants"]

    access_token = get_shopify_access_token()
    url = f"https://{SHOPIFY_STORE}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
    }

    by_sku = defaultdict(list)
    after = None
    while True:
        payload = {"query": QUERY, "variables": {"after": after, "locationId": MAIN_LOCATION_ID}}
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise RuntimeError(body["errors"])
        conn = body["data"]["productVariants"]
        for node in conn["nodes"]:
            sku = (node.get("sku") or "").strip()
            if not sku:
                continue
            status = (node.get("product") or {}).get("status", "")
            level = ((node.get("inventoryItem") or {}).get("inventoryLevel") or {})
            quantities = level.get("quantities") or []
            available = quantities[0].get("quantity", 0) if quantities else 0
            by_sku[sku].append({"status": status, "available": _num(available)})
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]

    _cache["variants"] = by_sku
    _cache["ts"] = now
    return by_sku


def resolve_stock(feed_sku, variants_by_sku):
    lookup = feed_sku
    matches = variants_by_sku.get(lookup, [])
    if not matches and feed_sku.endswith("-OneSize"):
        lookup = feed_sku[:-8]
        matches = variants_by_sku.get(lookup, [])

    if not matches:
        return 0

    active = [x for x in matches if str(x["status"]).upper() == "ACTIVE"]
    if len(active) == 1:
        chosen = active[0]
    elif len(matches) == 1:
        chosen = matches[0]
    else:
        # Ambiguous duplicate SKU: fail safe to zero to prevent overselling.
        return 0

    if str(chosen["status"]).upper() != "ACTIVE":
        return 0
    return max(0, _num(chosen["available"]))


def build_xml(variants_by_sku):
    root = ET.Element("products")
    for row in load_feed_rows():
        p = ET.SubElement(root, "product")
        ET.SubElement(p, "sku").text = row["sku"]
        ET.SubElement(p, "ean").text = row["ean"]
        ET.SubElement(p, "price-before-discount").text = row["before"]
        ET.SubElement(p, "price-after-discount").text = row["after"]
        ET.SubElement(p, "stock").text = str(resolve_stock(row["sku"], variants_by_sku))
        ET.SubElement(p, "collectionhours").text = row["hours"]
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


@app.get("/")
def index():
    return "Outfish 220.lv stock/price feed is running. Use /220-stock.xml\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/220-stock.xml")
def feed():
    try:
        variants = fetch_shopify_variants()
        xml = build_xml(variants)
        return Response(xml, status=200, mimetype="application/xml")
    except Exception as e:
        # Never return a partial/invalid feed to the marketplace.
        return Response(f"Feed generation error: {e}\n", status=503, mimetype="text/plain")


@app.get("/health")
def health():
    return {"ok": True}, 200
