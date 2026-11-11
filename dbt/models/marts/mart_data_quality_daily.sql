-- Gold: per source per day, how many rows were loaded vs. rejected (and why).

with loaded as (
    select source, cast(fetched_at as date) as obs_date, count(*) as n_loaded
    from {{ source('bronze', 'raw_observations') }}
    group by 1, 2
),

rejected as (
    select
        source,
        cast(fetched_at as date) as obs_date,
        count(*) as n_rejected,
        count(*) filter (where reason = 'non_positive_price') as n_non_positive,
        count(*) filter (where reason = 'price_outlier') as n_outlier,
        count(*) filter (where reason not in ('non_positive_price', 'price_outlier')) as n_other
    from {{ source('bronze', 'rejected_observations') }}
    group by 1, 2
)

select
    coalesce(l.source, r.source) as source,
    coalesce(l.obs_date, r.obs_date) as obs_date,
    coalesce(l.n_loaded, 0) as n_loaded,
    coalesce(r.n_rejected, 0) as n_rejected,
    coalesce(r.n_non_positive, 0) as n_non_positive,
    coalesce(r.n_outlier, 0) as n_outlier,
    coalesce(r.n_other, 0) as n_other,
    coalesce(r.n_rejected, 0) / (coalesce(l.n_loaded, 0) + coalesce(r.n_rejected, 0)) as reject_rate
from loaded as l
full outer join rejected as r on l.source = r.source and l.obs_date = r.obs_date
