"""Tailoring a CV to one job.

The CV is read as a list of paragraphs (`document`), a model rewrites the ones worth
rewriting against the advert (`rewrite`, over free models from `llm`), the changed
sentences are proofread (`proofread`), and the result is written back into the original
file with its layout untouched and scored the way an applicant tracking system reads a
CV (`ats`).

What never changes: which paragraphs exist and in what order, the name, contact details,
employers, job titles, dates and qualifications. What may: the summary, the skills lines
and the bullet points, worded toward the advert where the CV already supports it.
"""
