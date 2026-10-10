"""PageUp: one RSS feed per tenant, Irish roles kept."""

from __future__ import annotations

from jobfinder.sources.pageup import parse_feed

FEED = """<?xml version="1.0"?><rss xmlns:x="http://pageuppeople.com/"><channel>
<item><guid>https://careers.pageuppeople.com/806/cw/en/job/1</guid><link>https://careers.pageuppeople.com/806/cw/en/job/1</link>
<title>Department Supervisor</title><x:refNo>1</x:refNo><x:location>Republic of Ireland|Tallaght</x:location>
<x:workType>Part time</x:workType><x:description>&lt;p&gt;Join us&lt;/p&gt;</x:description></item>
<item><guid>2</guid><title>MOT Tester</title><x:refNo>2</x:refNo><x:location>South East|Bedfordshire</x:location></item>
<item><guid>3</guid><title>Store Manager</title><x:refNo>3</x:refNo><x:location>Northern Ireland|Belfast</x:location></item>
</channel></rss>"""


def test_only_republic_roles_are_kept_with_their_work_type():
    jobs = parse_feed(FEED)
    assert [(j.source_job_id, j.title, j.location_raw, j.employment_type) for j in jobs] == [
        ("1", "Department Supervisor", "Tallaght, Ireland", "Part time"),
    ]
