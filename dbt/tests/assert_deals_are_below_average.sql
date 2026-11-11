-- A flagged deal must be below its 30-day average and at (or near) its 90-day low.
-- Returns offending rows; dbt fails the test if any come back.

select item_id, listing_id, price_date, close_price, avg_30d, min_90d
from {{ ref('fct_listing_daily_signals') }}
where deal_flag
  and (
      close_price >= avg_30d
      or close_price > min_90d * (1 + {{ var('deal_near_low_tolerance') }})
      or was_inflated_14d
  )
