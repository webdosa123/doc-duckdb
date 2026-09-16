-- What is in the corpus, per format, with the failures in the same rows as the successes.
--
-- count(*) filter (where not ok) is the number rule 1 exists to keep. If a failed file
-- were simply missing from the table, this column would read 0 on every row and the
-- pages and blocks below would look like the whole corpus rather than the part of it
-- that opened.
select
    coalesce(format, '(none)')            as format,
    count(*)                              as files,
    count(*) filter (where ok)            as ok,
    count(*) filter (where not ok)        as failed,
    round(100.0 * count(*) filter (where ok) / count(*), 1) as ok_pct,
    sum(page_count)                       as pages,
    sum(slide_count)                      as slides,
    sum(sheet_count)                      as sheets,
    sum(block_count)                      as blocks,
    round(sum(size_bytes) / 1048576.0, 1) as source_mb
from document
group by 1
order by files desc;
