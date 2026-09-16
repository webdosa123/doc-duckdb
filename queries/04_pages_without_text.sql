-- PDF pages that carry no extractable text.
--
-- This is the usual signature of a scan. It is a finding, not a failure: the file
-- opened, the page was read, and there was nothing in it to read. A row with
-- block_count = 0 and a row with block_count = null are different states, and this
-- query only makes sense because they are kept apart.
with pages as (
    select
        d.doc_id,
        d.path,
        d.page_count,
        b.part_index,
        sum(b.char_count) as chars
    from document d
    left join block b
        on b.doc_id = d.doc_id and b.part_kind = 'page'
    where d.format = 'pdf' and d.ok
    group by 1, 2, 3, 4
)
select
    path,
    page_count,
    count(*) filter (where coalesce(chars, 0) = 0) as pages_without_text,
    round(100.0 * count(*) filter (where coalesce(chars, 0) = 0) / page_count, 1) as pct
from pages
group by 1, 2
having pages_without_text > 0
order by pct desc, page_count desc
limit 40;
