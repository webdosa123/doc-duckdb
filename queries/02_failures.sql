-- What refused, how much of the corpus that is, and an example of each.
select
    error_kind,
    count(*)                                                  as files,
    round(100.0 * count(*) / sum(count(*)) over (), 2)        as pct_of_corpus,
    round(sum(size_bytes) / 1048576.0, 1)                     as mb,
    min(path)                                                 as an_example
from document
group by 1
order by files desc;
