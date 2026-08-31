# Interview inputs

Drop a job advert in `jd/` and a CV in `resume/`, then refer to them by
filename — the panel script looks in these folders automatically.

    python -m scripts.panel --jd ml-engineer.txt --resume harsh.pdf

Both accept `.txt`, `.md` or `.pdf`. PDFs are text-extracted with pypdf; a
scanned PDF with no text layer will produce nothing and the script says so
rather than silently running an un-personalised interview.

`resume/` is gitignored. Real CVs are personal data and this repository has to
be public at submission — keep them out of the commit history.
