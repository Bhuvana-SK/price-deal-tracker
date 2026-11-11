-- Silver: typed, de-duplicated observations, one row per item x listing x observation.
-- A listing can belong to more than one item, so the join can fan out on purpose.

with observations as (
    select *
    from {{ source('bronze', 'raw_observations') }}
    qualify row_number() over (partition by observation_key order by loaded_at) = 1
)

select
    o.observation_key,
    il.item_id,
    o.listing_id,
    o.source,
    coalesce(o.raw_payload ->> 'store', o.source) as store,
    o.title,
    o.url,
    o.price,
    o.shipping,
    o.price + o.shipping as total_cost,
    o.currency,
    o.in_stock,
    o.fetched_at as observed_at,
    cast(o.fetched_at as date) as observed_date
from observations as o
inner join {{ source('app', 'item_listings') }} as il
    on o.listing_id = il.listing_id
