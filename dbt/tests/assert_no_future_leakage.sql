-- Signals must use only EARLIER days. On a listing's first day there is no earlier
-- data, so the windows must be empty and no deal can be flagged. If a window
-- accidentally included the current row (or later ones), this would return rows.

with first_days as (
    select *
    from {{ ref('fct_listing_daily_signals') }}
    qualify row_number() over (partition by item_id, listing_id order by price_date) = 1
)

select item_id, listing_id, price_date, avg_30d, min_90d, history_days_90d
from first_days
where avg_30d is not null
   or min_90d is not null
   or history_days_90d > 0
   or deal_flag
