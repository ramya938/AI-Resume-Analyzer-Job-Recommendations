import sys
sys.path.insert(0, '.')
from backend.job_scraper import search_jobs

print('Testing headless LinkedIn scrape with saved session...')
print()

result = search_jobs(
    skills=['Python', 'SQL', 'Machine Learning'],
    job_title='Data Scientist',
    location='Remote',
    user_id='',
    limit=10,
    use_cache=False,
)

print(f"Success  : {result['success']}")
print(f"Source   : {result['source']}")
print(f"Total    : {result['total']} jobs")
print()
print('Top 5 matches:')
for i, job in enumerate(result['jobs'][:5], 1):
    match   = job.get('match_percentage', 0)
    title   = job.get('job_title', '')[:45]
    company = job.get('company_name', '')[:25]
    loc     = job.get('location', '')[:20]
    print(f"  {i}. [{match:.0f}%] {title} @ {company} ({loc})")
