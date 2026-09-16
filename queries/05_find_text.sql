-- Find a phrase and get back somewhere to point at.
--
-- For a PDF the box bounds the characters of that line, so x0/y0/x1/y1 is a citation
-- you can draw. For a slide the box is the shape's, which is why box_of is selected
-- next to it rather than left for the reader to assume.
select
    d.path,
    b.part_kind,
    b.part_index,
    b.part_label,
    b.cell_ref,
    b.box_of,
    round(b.box_x0, 1) as x0,
    round(b.box_y0, 1) as y0,
    round(b.box_x1, 1) as x1,
    round(b.box_y1, 1) as y1,
    b.byte_start,
    b.byte_end,
    b.text
from block b
join document d using (doc_id)
where b.text ilike '%invoice%'
order by d.path, b.block_index
limit 50;
