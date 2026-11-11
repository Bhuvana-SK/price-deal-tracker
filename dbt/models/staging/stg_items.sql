select
    item_id,
    name as item_name,
    query,
    target_price,
    currency,
    sources,
    active
from {{ source('app', 'items') }}
