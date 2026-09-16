-- The same table without the generated views, for anyone reading the JSON Lines
-- directly.
--
-- sample_size = -1 is not optional. read_json_auto samples the head of the file by
-- default; the head of a sweep is mostly successes, so error_kind types from nulls and
-- every refusal row drops out of the result on this side of the contract -- the rows
-- rule 1 exists to keep, lost by the reader instead of by the writer.
select error_kind, count(*) as files
from read_json_auto('out/*/document.jsonl', sample_size = -1)
group by 1
order by files desc;
