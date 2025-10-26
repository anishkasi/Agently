from typing import Tuple
from firecrawl import Firecrawl
from core import settings


firecrawl = Firecrawl(api_key=settings.FIRECRAWL_API_KEY)

def fetch_page_summary(url: str, return_markdown: bool = False) -> str:
    """Fetch a page summary or markdown via Firecrawl."""
    if return_markdown:
        formats = ['markdown']
    else:
        formats = ['summary']
    scrape_result = firecrawl.scrape(
        url, 
        formats=formats
    )
    if return_markdown:
        return scrape_result.markdown
    else:
        return scrape_result.summary



