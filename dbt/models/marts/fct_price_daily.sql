-- Gold: one row per item x listing x day. "close" = the last observation of the day.

select
    item_id,
    listing_id,
    any_value(source) as source,
    any_value(store) as store,
    any_value(currency) as currency,
    observed_date as price_date,
    min(price) as min_price,
    max(price) as max_price,
    arg_max(price, observed_at) as close_price,
    arg_max(total_cost, observed_at) as close_total_cost,
    arg_max(in_stock, observed_at) as close_in_stock,
    max(observed_at) as last_observed_at,
    count(*) as n_observations
from {{ ref('stg_price_observations') }}
group by item_id, listing_id, observed_date
