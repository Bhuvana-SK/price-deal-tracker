-- Gold: the latest state of every listing, with "is this really a deal?" signals
-- and the 24-hour change (via an ASOF join to the observation ~24h earlier).

with latest_obs as (
    select *
    from {{ ref('stg_price_observations') }}
    qualify row_number() over (partition by item_id, listing_id order by observed_at desc) = 1
),

day_before as (
    select
        l.item_id,
        l.listing_id,
        prev.price as price_24h_ago
    from latest_obs as l
    asof left join {{ ref('stg_price_observations') }} as prev
        on l.item_id = prev.item_id
        and l.listing_id = prev.listing_id
        and l.observed_at - interval 24 hours >= prev.observed_at
)

select
    l.item_id,
    i.item_name,
    l.listing_id,
    l.source,
    l.store,
    l.title,
    l.url,
    l.currency,
    l.price as current_price,
    l.shipping as current_shipping,
    l.total_cost as current_total_cost,
    l.in_stock,
    l.observed_at as as_of,
    i.target_price,
    coalesce(l.price <= i.target_price, false) as is_below_target,
    d.price_24h_ago,
    (d.price_24h_ago - l.price) / d.price_24h_ago as pct_drop_24h,
    coalesce((d.price_24h_ago - l.price) / d.price_24h_ago >= {{ var('drop_24h_alert') }}, false)
        as is_big_drop_24h,
    s.avg_30d,
    s.avg_90d,
    s.min_90d,
    s.all_time_low,
    s.pct_vs_avg_30d,
    s.pct_vs_avg_90d,
    s.is_90d_low,
    s.is_all_time_low,
    s.was_inflated_14d,
    s.has_enough_history,
    s.deal_flag,
    s.deal_reason
from latest_obs as l
inner join {{ ref('stg_items') }} as i on l.item_id = i.item_id
left join day_before as d on l.item_id = d.item_id and l.listing_id = d.listing_id
left join {{ ref('fct_listing_daily_signals') }} as s
    on l.item_id = s.item_id
    and l.listing_id = s.listing_id
    and l.observed_date = s.price_date
