-- Gold: for each item, its current listings ranked by total cost (price + shipping).
-- Only in-stock listings seen in the last 48 hours of that item's data are ranked.

with current_listings as (
    select
        *,
        max(as_of) over (partition by item_id) as item_as_of
    from {{ ref('mart_listing_current') }}
)

select
    item_id,
    item_name,
    listing_id,
    store,
    title,
    currency,
    current_price,
    current_shipping,
    current_total_cost,
    deal_flag,
    rank() over (partition by item_id, currency order by current_total_cost) as cost_rank,
    current_total_cost - min(current_total_cost) over (partition by item_id, currency)
        as extra_vs_cheapest,
    as_of
from current_listings
where in_stock
  and as_of >= item_as_of - interval 48 hours
