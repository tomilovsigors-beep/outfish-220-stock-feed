# Outfish 220.lv stock/price XML

Endpoint after deployment: `/220-stock.xml`

Rules implemented:
- SKU and EAN are taken from `sia_fhm.csv` (220.lv identifiers remain the source of truth).
- `price-before-discount`, `price-after-discount`, and `collectionhours` are taken from `sia_fhm.csv`.
- If `price-after-discount` is blank, the regular price is repeated as required by 220.lv.
- Stock is taken only from Shopify location `Cēsu iela 18, Veikals`.
- LOWA and Fjord Nansen are not included.
- DRAFT/non-ACTIVE product = stock 0.
- Missing Shopify SKU = stock 0.
- Ambiguous duplicate Shopify SKU = stock 0.
- Negative Shopify availability is clamped to 0 for the marketplace feed.
- `-OneSize` 220 SKU falls back to the same Shopify SKU without the suffix.

Required Render secret:
- `SHOPIFY_ACCESS_TOKEN`

Do not put the access token into the repository.
