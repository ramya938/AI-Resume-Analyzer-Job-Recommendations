"""
Quick live LinkedIn scrape test with fixed selectors.
Tests: session restore -> headless search -> card extraction -> results
"""
import sys, logging
sys.path.insert(0, '.')
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

from dotenv import load_dotenv
load_dotenv()

from backend.job_scraper import search_jobs

print()
print("=" * 60)
print("  Live LinkedIn Scrape Test (headless + saved session)")
print("=" * 60)
print()

result = search_jobs(
    skills=['Python', 'SQL', 'Machine Learning'],
    job_title='Data Scientist',
    location='Remote',
    user_id='',
    limit=10,
    use_cache=False,
)

print()
print(f"Success  : {result['success']}")
print(f"Source   : {result['source']}")
print(f"Total    : {result['total']} jobs")
print()
print("Top matches:")
for i, job in enumerate(result['jobs'][:8], 1):
    match   = job.get('match_percentage', 0)
    title   = job.get('job_title', '')[:45]
    company = job.get('company_name', '')[:25]
    src     = job.get('source', '')
    print(f"  {i}. [{match:.0f}%] [{src}] {title} @ {company}")

print()
print("=" * 60)
print("  DONE")
print("=" * 60)
