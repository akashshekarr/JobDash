"""
JobDash — FastAPI backend that proxies the JSearch API (RapidAPI).
The API key stays server-side and is never exposed to the browser.

Run:
    pip install fastapi uvicorn httpx
    export RAPIDAPI_KEY="your_jsearch_rapidapi_key"
    uvicorn main:app --reload

Then open http://localhost:8000
"""

import os
import httpx
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

JSEARCH_HOST = "jsearch.p.rapidapi.com"
JSEARCH_URL = f"https://{JSEARCH_HOST}/search"
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")

app = FastAPI(title="JobDash")


@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1, description="Job title or keyword"),
    location: str | None = Query(None, description="City / country, optional"),
    employment_type: str | None = Query(
        None, description="FULLTIME, PARTTIME, CONTRACTOR, INTERN"
    ),
    work_arrangement: str | None = Query(
        None, description="ANY, WFH, ONSITE, HYBRID"
    ),
    page: int = Query(1, ge=1, le=20),
):
    if not RAPIDAPI_KEY:
        raise HTTPException(
            status_code=500,
            detail="RAPIDAPI_KEY not set on the server. Export it and restart.",
        )

    # JSearch takes a single free-text query; fold location into it.
    query = q if not location else f"{q} in {location}"

    params = {
        "query": query,
        "page": str(page),
        "num_pages": "1",
        "date_posted": "all",
    }
    if employment_type:
        params["employment_types"] = employment_type

    # WFH is a native JSearch flag. On-site / Hybrid are filtered post-hoc below.
    if work_arrangement == "WFH":
        params["work_from_home"] = "true"

    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": JSEARCH_HOST,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(JSEARCH_URL, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {e}")

    # Normalise to a lean shape the frontend actually uses.
    jobs = []
    for j in data.get("data", []) or []:
        is_remote = bool(j.get("job_is_remote"))
        blob = f"{j.get('job_title','')} {j.get('job_description','')}".lower()
        is_hybrid = "hybrid" in blob

        # Apply on-site / hybrid filters that JSearch can't do natively.
        if work_arrangement == "ONSITE" and (is_remote or is_hybrid):
            continue
        if work_arrangement == "HYBRID" and not is_hybrid:
            continue

        if is_remote:
            arrangement = "WFH"
        elif is_hybrid:
            arrangement = "Hybrid"
        else:
            arrangement = "On-site"

        # Salary: JSearch sometimes provides min/max + period + currency.
        sal_min = j.get("job_min_salary")
        sal_max = j.get("job_max_salary")
        sal_cur = j.get("job_salary_currency") or ""
        sal_period = j.get("job_salary_period") or ""
        salary_text = ""
        if sal_min or sal_max:
            def fmt(n):
                try:
                    return f"{int(n):,}"
                except (TypeError, ValueError):
                    return str(n)
            if sal_min and sal_max:
                salary_text = f"{sal_cur} {fmt(sal_min)}–{fmt(sal_max)}"
            else:
                salary_text = f"{sal_cur} {fmt(sal_min or sal_max)}"
            if sal_period:
                salary_text += f" / {sal_period.lower()}"
            salary_text = salary_text.strip()

        jobs.append(
            {
                "id": j.get("job_id"),
                "title": j.get("job_title"),
                "company": j.get("employer_name"),
                "logo": j.get("employer_logo"),
                "location": ", ".join(
                    filter(None, [j.get("job_city"), j.get("job_country")])
                )
                or "Remote / N.A.",
                "type": j.get("job_employment_type"),
                "remote": is_remote,
                "arrangement": arrangement,
                "posted": j.get("job_posted_at_datetime_utc"),
                "apply": j.get("job_apply_link"),
                "publisher": j.get("job_publisher"),
                "salary": salary_text,
                "description": (j.get("job_description") or "")[:320],
            }
        )

    return {"page": page, "count": len(jobs), "jobs": jobs}


# Serve the frontend
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")