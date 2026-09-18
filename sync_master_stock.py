import json
import os
from collections import defaultdict

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app import fetch_shopify_variants

SPREADSHEET_ID = os.environ.get("MASTER_SPREADSHEET_ID", "1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I").strip()
SHEET_NAME = os.environ.get("MASTER_SHEET_NAME", "MASTER").strip()
CREDS_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
DRY_RUN = os.environ.get("MASTER_STOCK_SYNC_DRY_RUN", "1").strip() != "0"

EXPECTED = {
    "shopify_variant_id": 1,   # B
    "shopify_sku": 2,          # C
    "shopify_stock_live": 41,  # AP
    "220_stock_target": 42,    # AQ
}

def _resolve(row, by_sku, by_variant_id):
    variant_id = str(row[1] if len(row) > 1 else "").strip()
    sku = str(row[2] if len(row) > 2 else "").strip()

    chosen = None
    match = "NO_MATCH"

    if variant_id:
        chosen = by_variant_id.get(variant_id)
        if chosen:
            match = "VARIANT_ID"

    if chosen is None and sku:
        matches = list(by_sku.get(sku, []))
        if not matches and sku.endswith("-OneSize"):
            matches = list(by_sku.get(sku[:-8], []))
        if matches:
            active = [x for x in matches if str(x.get("status", "")).upper() == "ACTIVE"]
            if len(active) == 1:
                chosen = active[0]
                match = "UNIQUE_ACTIVE_SKU"
            elif len(matches) == 1:
                chosen = matches[0]
                match = "UNIQUE_SKU"
            else:
                return None, None, "AMBIGUOUS"

    if chosen is None:
        return None, None, match

    raw = max(0, int(chosen.get("available") or 0))
    active = str(chosen.get("status", "")).upper() == "ACTIVE"
    if not active:
        target = 0
    elif raw == 0:
        target = 0
    elif raw < 3:
        target = 3
    else:
        target = raw
    return raw, target, match

def main():
    if not CREDS_JSON:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured")

    info = json.loads(CREDS_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    values = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A1:AQ4051",
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute().get("values", [])

    if not values:
        raise RuntimeError("Master sheet returned no rows")

    header = values[0]
    for name, idx in EXPECTED.items():
        actual = str(header[idx] if idx < len(header) else "").strip()
        if actual != name:
            raise RuntimeError(f"Master schema mismatch at column {idx+1}: expected {name!r}, got {actual!r}")

    variants = fetch_shopify_variants()
    by_variant_id = {}
    for sku, items in variants.items():
        for item in items:
            vid = str(item.get("id") or "").strip()
            if vid:
                by_variant_id[vid] = item

    output = []
    stats = defaultdict(int)
    for row in values[1:]:
        raw, target, match = _resolve(row, variants, by_variant_id)
        stats[match] += 1
        if raw is None:
            output.append(["", ""])
        else:
            output.append([raw, target])
            if raw in (1, 2) and target == 3:
                stats["RAISED_TO_3"] += 1

    # Pad to the canonical 4050 data rows so stale values cannot survive beyond returned rows.
    while len(output) < 4050:
        output.append(["", ""])

    print("MASTER_STOCK_SYNC_STATS " + json.dumps(dict(stats), sort_keys=True), flush=True)

    if DRY_RUN:
        print("MASTER_STOCK_SYNC_DRY_RUN rows=" + str(len(output)), flush=True)
        return

    body = {"values": output}
    result = sheets.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!AP2:AQ4051",
        valueInputOption="RAW",
        body=body,
    ).execute()

    print("MASTER_STOCK_SYNC_WRITE " + json.dumps({
        "updatedRange": result.get("updatedRange"),
        "updatedRows": result.get("updatedRows"),
        "updatedCells": result.get("updatedCells"),
    }, sort_keys=True), flush=True)

    # Read back a small deterministic sample around the top rows.
    verify = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!C1:C20",
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute().get("values", [])
    verify_stock = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!AP1:AQ20",
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute().get("values", [])
    print("MASTER_STOCK_SYNC_VERIFY " + json.dumps({
        "skuRows": verify[:5],
        "stockRows": verify_stock[:5],
    }, separators=(",", ":")), flush=True)

if __name__ == "__main__":
    main()
