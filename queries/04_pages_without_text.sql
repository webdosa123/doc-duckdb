-- PDF pages that carry no extractable text.
--
-- This is the usual signature of a scan. It is a finding, not a failure: the file
-- opened, the page was read, and there was nothing in it to read.
--
-- The page list is generated from page_count rather than taken from the blocks, and
-- that is the whole point. A page with no text has no rows in `block`, so joining the
-- two tables and looking for pages with no characters finds nothing: the pages you are
-- looking for are exactly the ones the join drops. It is rule 1 again, one level down --
-- an absence is not an answer -- and here the fix is on the query side, because the
-- document row already knows how many pages there were.
with pages as (
    select
        d.doc_id,
        d.path,
        d.page_count,
        unnest(generate_series(0, d.page_count - 1)) as page_index
    from document d
    where d.format = 'pdf' and d.ok and d.page_count > 0
),
text_on_page as (
    select doc_id, part_index, sum(char_count) as chars
    from block
    where part_kind = 'page'
    group by 1, 2
)
select
    p.path,
    any_value(p.page_count)                                     as pages,
    count(*)                                                    as pages_without_text,
    round(100.0 * count(*) / any_value(p.page_count), 1)        as pct,
    list(p.page_index order by p.page_index)[1:8]               as first_few
from pages p
left join text_on_page t
    on t.doc_id = p.doc_id and t.part_index = p.page_index
where coalesce(t.chars, 0) = 0
group by p.path
order by pct desc, pages desc
limit 40;
