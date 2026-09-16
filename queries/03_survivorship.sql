-- The same question answered twice: once over the whole corpus, once over the files
-- that happened to open.
--
-- This is what rules 1 and 2 are for. The second number is the one a pipeline that
-- drops its failures would report, with nothing in the output to say it is not the
-- first. The gap is exactly the refusal rate.
select
    count(*)                                        as files_in_corpus,
    count(*) filter (where ok)                      as files_that_opened,
    round(avg(page_count), 2)                       as avg_pages_over_openers,
    round(sum(page_count) * 1.0 / count(*), 2)      as avg_pages_over_corpus,
    round(100.0 * count(*) filter (where not ok) / count(*), 2) as refusal_pct
from document
where format = 'pdf';
