-- The same tables without the generated views, for anyone reading the JSON Lines
-- directly.
--
-- sample_size = -1 is not optional on the block table. read_json_auto types each column
-- from a sample, and the head of a sweep is whatever sorted first. Measured on a
-- 379 MiB block table whose first rows were docx blocks: all twelve coordinate columns
-- came back JSON, because docx blocks have no coordinates. Then
-- `avg(char_end - char_start)` raised a binder error, and `where box_y0 > 700` returned
-- 38,247 rows against a true 38,497 -- no error, just a wrong number.
select part_kind, count(*) as blocks, round(avg(char_end - char_start), 2) as avg_span
from read_json_auto('out/*/block.jsonl', sample_size = -1)
group by 1
order by blocks desc;

-- The document table is smaller and usually lands inside the sample window, so the same
-- read without sample_size may well look fine. That is the trap, not the reassurance:
-- it starts being wrong when the corpus grows, and nothing announces the change.
select error_kind, count(*) as files
from read_json_auto('out/*/document.jsonl', sample_size = -1)
group by 1
order by files desc;
