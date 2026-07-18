"""
Babel Tool: arxiv_fetcher
Fetch the latest research papers from arXiv based on a query.
"""

REQUIRED_ENV_VARS = []

import urllib.parse
import urllib.request
import json
from typing import Dict, Any


def arxiv_fetcher(
    query: str,
    max_results: int = 10,
    sort_by: str = "submittedDate",
) -> Dict[str, Any]:
    """
    Fetch papers from arXiv based on a search query.

    Args:
        query: Search term for arXiv papers
        max_results: Maximum number of papers to return (default: 10)
        sort_by: Sort by 'relevance' or 'submittedDate' (default: 'submittedDate')

    Returns:
        Dict containing 'papers' list with metadata for each paper
    """
    try:
        # Validate sort_by parameter
        if sort_by not in ["relevance", "submittedDate"]:
            sort_by = "submittedDate"

        # Build arXiv API URL
        base_url = "http://export.arxiv.org/api/query?"
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": "descending",
        }
        url = base_url + urllib.parse.urlencode(params)

        # Fetch data from arXiv API
        with urllib.request.urlopen(url, timeout=10) as response:
            xml_data = response.read().decode("utf-8")

        # Parse XML response (simplified parsing for key fields)
        papers = []
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_data)
        namespace = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

        for entry in root.findall(".//atom:entry", namespace):
            paper = {
                "title": entry.find("atom:title", namespace).text.strip() if entry.find("atom:title", namespace) is not None else "",
                "authors": [author.find("atom:name", namespace).text.strip() for author in entry.findall("atom:author", namespace) if author.find("atom:name", namespace) is not None],
                "abstract": entry.find("atom:summary", namespace).text.strip() if entry.find("atom:summary", namespace) is not None else "",
                "publication_date": entry.find("atom:published", namespace).text.strip() if entry.find("atom:published", namespace) is not None else "",
                "arxiv_url": entry.find("atom:id", namespace).text.strip() if entry.find("atom:id", namespace) is not None else "",
            }
            papers.append(paper)

        return {"papers": papers}

    except Exception as e:
        return {"error": f"Failed to fetch arXiv papers: {str(e)}"}
