-- Gold: deal signals for every listing on every day, using only the days BEFORE it.
-- Because each row only looks backwards, this table doubles as the backtest input:
-- "what would the deal rule have said on day X?"

with daily as (
    select * from {{ ref('fct_price_daily') }}
),

windows as (
    select
        *,
        avg(close_price) over w30 as avg_30d,
        avg(close_price) over w90 as avg_90d,
        min(close_price) over w90 as min_90d,
        max(close_price) over w14 as max_14d,
        min(close_price) over w_all as prev_all_time_low,
        count(*) over w90 as history_days_90d,
        lag(close_price) over (partition by item_id, listing_id order by price_date) as prev_close_price
    from daily
    window
        w14 as (partition by item_id, listing_id order by price_date
                range between interval 14 days preceding and interval 1 day preceding),
        w30 as (partition by item_id, listing_id order by price_date
                range between interval 30 days preceding and interval 1 day preceding),
        w90 as (partition by item_id, listing_id order by price_date
                range between interval 90 days preceding and interval 1 day preceding),
        w_all as (partition by item_id, listing_id order by price_date
                  rows between unbounded preceding and 1 preceding)
),

signals as (
    select
        *,
        least(close_price, coalesce(prev_all_time_low, close_price)) as all_time_low,
        (close_price - avg_30d) / avg_30d as pct_vs_avg_30d,
        (close_price - avg_90d) / avg_90d as pct_vs_avg_90d,
        coalesce(close_price <= min_90d, false) as is_90d_low,
        coalesce(close_price < prev_all_time_low, false) as is_all_time_low,
        coalesce(max_14d > avg_90d * (1 + {{ var('inflation_threshold') }}), false) as was_inflated_14d,
        history_days_90d >= {{ var('min_history_days') }} as has_enough_history
    from windows
)

select
    *,
    has_enough_history
        and pct_vs_avg_30d <= -{{ var('deal_min_discount_vs_30d') }}
        and close_price <= min_90d * (1 + {{ var('deal_near_low_tolerance') }})
        and not was_inflated_14d
        as deal_flag,
    case
        when not has_enough_history then 'not enough history'
        when was_inflated_14d and pct_vs_avg_30d <= -{{ var('deal_min_discount_vs_30d') }}
            then 'looks like a discount, but the price was raised in the last 14 days'
        when pct_vs_avg_30d > -{{ var('deal_min_discount_vs_30d') }}
            then 'not meaningfully below the 30-day average'
        when close_price > min_90d * (1 + {{ var('deal_near_low_tolerance') }})
            then 'below average, but it has been cheaper in the last 90 days'
        else 'genuine deal: well below the 30-day average and at its 90-day low'
    end as deal_reason
from signals
