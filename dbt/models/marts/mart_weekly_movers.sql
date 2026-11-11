-- Gold: how each item's best price moved within each ISO week (input for the weekly digest).

select
    d.item_id,
    i.item_name,
    d.currency,
    date_trunc('week', d.price_date) as week_start,
    arg_min(d.best_total_cost, d.price_date) as start_cost,
    arg_max(d.best_total_cost, d.price_date) as end_cost,
    min(d.best_total_cost) as low_cost,
    max(d.best_total_cost) as high_cost,
    (arg_max(d.best_total_cost, d.price_date) - arg_min(d.best_total_cost, d.price_date))
        / arg_min(d.best_total_cost, d.price_date) as pct_change,
    count(*) as n_days
from {{ ref('mart_item_price_daily') }} as d
inner join {{ ref('stg_items') }} as i on d.item_id = i.item_id
group by d.item_id, i.item_name, d.currency, date_trunc('week', d.price_date)
