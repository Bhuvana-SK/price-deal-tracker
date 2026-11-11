-- Gold: the best (lowest total cost) price per item per day across all its listings.

select
    item_id,
    price_date,
    currency,
    min(close_total_cost) as best_total_cost,
    arg_min(close_price, close_total_cost) as best_price,
    arg_min(store, close_total_cost) as best_store,
    count(*) as n_listings
from {{ ref('fct_price_daily') }}
where close_in_stock
group by item_id, price_date, currency
