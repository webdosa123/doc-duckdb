-- What each run cost, and how big the tables are next to the documents they came from.
--
-- Two rows for the same root are a paired measurement. The second run reads files the
-- operating system still has cached, so the difference between them is partly the page
-- cache and not the work; neither number means anything without the other.
select
    label,
    files_attempted                                      as files,
    round(walk_s, 2)                                     as walk_s,
    round(elapsed_s, 2)                                  as read_s,
    round(walk_s + elapsed_s, 2)                         as total_s,
    round(1000 * elapsed_s / files_attempted, 2)         as ms_per_file,
    jobs,
    hashed,
    round(source_bytes / 1048576.0, 1)                   as source_mb,
    round((document_bytes + block_bytes) / 1048576.0, 2) as store_mb,
    round((document_bytes + block_bytes) * 1.0 / source_bytes, 4) as store_over_source,
    blocks_written
from sweep
order by started_utc;
