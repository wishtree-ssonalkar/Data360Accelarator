-- description: Product catalog, one row per tenant + sku (first catalog source wins if several).
-- depends_on: silver.std_products
CREATE OR REPLACE TABLE silver.products AS
SELECT tenant_id, sku, product_name, category, list_price, unit_cost, launched_on, coalesce(is_active, true) AS is_active
FROM silver.std_products
QUALIFY row_number() OVER (PARTITION BY tenant_id, sku ORDER BY source_name, _loaded_at DESC) = 1;
