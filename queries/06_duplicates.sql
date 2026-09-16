-- The same bytes filed in more than one place.
select
    content_sha256,
    count(*)                        as copies,
    round(any_value(size_bytes) * (count(*) - 1) / 1048576.0, 1) as wasted_mb,
    string_agg(path, E'\n' order by path) as paths
from document
where content_sha256 is not null
group by 1
having count(*) > 1
order by copies desc, wasted_mb desc
limit 40;
