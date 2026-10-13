"""
Downloads the cardiology corpus described in sources.json into data/raw/.

Three sources, each resolved via a documented public API (no guessed URLs):
  - guidelines:  direct download of the hand-verified WHO IRIS PDF URLs in sources.json
  - dailymed:    NIH DailyMed v2 REST API (drug name -> SETID -> label PDF)
  - pmc:         NCBI E-utilities esearch (query -> PMCIDs) + the PMC Cloud
                 Service on AWS (S3, anonymous read) for the article PDF/XML
                 and its embedded figure images

Every downloaded file is recorded in data/raw/manifest.json with its
resolved source URL, license, retrieval timestamp, and sha256 -- this is
the reproducible provenance record for the corpus.

Usage:
    python scripts/download_corpus.py --only guidelines,dailymed,pmc
    python scripts/download_corpus.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = REPO_ROOT / "sources.json"
RAW_DIR = REPO_ROOT / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "manifest.json"

USER_AGENT = "cardiology-rag-portfolio-project/1.0 (educational use; contact via github)"
EUTILS_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PMC_OA_S3_BASE = "https://pmc-oa-opendata.s3.amazonaws.com"
DAILYMED_SPLS = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json"
DAILYMED_PDF = "https://dailymed.nlm.nih.gov/dailymed/downloadpdffile.cfm"

# NCBI asks for <=3 req/sec without an API key; stay well under that.
NCBI_DELAY_SECONDS = 0.4

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


@dataclass
class ManifestEntry:
    id: str
    source_type: str  # guideline | dailymed | pmc_pdf | pmc_image
    title: str
    resolved_url: str
    license: str
    local_path: str
    sha256: str
    retrieved_at: str


def load_sources() -> dict:
    return json.loads(SOURCES_PATH.read_text(encoding="utf-8"))


def load_manifest() -> list[dict]:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return []


def save_manifest(entries: list[dict]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_binary(url: str, dest: Path, dry_run: bool) -> bool:
    """Downloads url to dest. Returns True on success."""
    if dry_run:
        print(f"[dry-run] would GET {url} -> {dest.relative_to(REPO_ROOT)}")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = session.get(url, timeout=60)
    if resp.status_code != 200 or not resp.content:
        print(f"  ! failed ({resp.status_code}) {url}")
        return False
    dest.write_bytes(resp.content)
    return True


def download_guidelines(sources: dict, manifest: list[dict], dry_run: bool) -> None:
    print("== Guidelines (WHO IRIS) ==")
    for g in sources["guidelines"]:
        dest = RAW_DIR / "guidelines" / f"{g['id']}.pdf"
        print(f"- {g['id']}")
        if fetch_binary(g["url"], dest, dry_run):
            manifest.append(asdict(ManifestEntry(
                id=g["id"],
                source_type="guideline",
                title=g["title"],
                resolved_url=g["url"],
                license=g["license"],
                local_path=str(dest.relative_to(REPO_ROOT)),
                sha256=sha256_of(dest),
                retrieved_at=datetime.now(timezone.utc).isoformat(),
            )))


def download_dailymed(sources: dict, manifest: list[dict], dry_run: bool) -> None:
    print("== DailyMed drug labels ==")
    cfg = sources["dailymed_drugs"]
    for term in cfg["search_terms"]:
        resp = session.get(DAILYMED_SPLS, params={
            "drug_name": term,
            "pagesize": cfg.get("labels_per_term", 1),
        }, timeout=30)
        if resp.status_code != 200:
            print(f"  ! DailyMed search failed for '{term}' ({resp.status_code})")
            continue
        for row in resp.json().get("data", []):
            setid = row["setid"]
            title = row["title"]
            dest = RAW_DIR / "drug_labels" / f"{term.replace(' ', '_')}_{setid}.pdf"
            print(f"- {title[:70]}")
            pdf_url = f"{DAILYMED_PDF}?setId={setid}"
            if fetch_binary(pdf_url, dest, dry_run):
                manifest.append(asdict(ManifestEntry(
                    id=f"dailymed-{setid}",
                    source_type="dailymed",
                    title=title,
                    resolved_url=pdf_url,
                    license="Public domain (FDA-approved structured product label)",
                    local_path=str(dest.relative_to(REPO_ROOT)),
                    sha256=sha256_of(dest),
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                )))


def esearch_pmc_ids(query: str, max_results: int) -> list[str]:
    resp = session.get(EUTILS_ESEARCH, params={
        "db": "pmc",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["esearchresult"]["idlist"]


def list_pmc_s3_objects(pmcid: str) -> list[dict]:
    """Lists every object under the latest version prefix for a PMCID via the
    public S3 REST list API (anonymous read, no AWS credentials needed)."""
    resp = session.get(PMC_OA_S3_BASE, params={
        "list-type": "2",
        "prefix": f"{pmcid}.",
    }, timeout=30)
    if resp.status_code != 200:
        return []
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    root = ET.fromstring(resp.text)
    keys = [c.findtext("s3:Key", namespaces=ns) for c in root.findall("s3:Contents", ns)]
    return [k for k in keys if k]


def latest_version_prefix(keys: list[str], pmcid: str) -> str | None:
    versions = sorted({k.split("/")[0] for k in keys if k.startswith(pmcid + ".")})
    return versions[-1] if versions else None


def download_pmc(sources: dict, manifest: list[dict], dry_run: bool) -> None:
    print("== PMC Open Access cardiology articles ==")
    cfg = sources["pmc_open_access"]
    ids = esearch_pmc_ids(cfg["esearch_query"], cfg["max_results"])
    print(f"  esearch returned {len(ids)} candidate article IDs")
    for uid in ids:
        pmcid = f"PMC{uid}"
        time.sleep(NCBI_DELAY_SECONDS)
        keys = list_pmc_s3_objects(pmcid)
        if not keys:
            print(f"- {pmcid}: not in PMC OA S3 bucket, skipping")
            continue
        prefix = latest_version_prefix(keys, pmcid)
        print(f"- {pmcid} ({prefix})")

        pdf_key = f"{prefix}/{prefix}.pdf"
        if pdf_key in keys:
            dest = RAW_DIR / "pmc" / f"{pmcid}.pdf"
            url = f"{PMC_OA_S3_BASE}/{pdf_key}"
            if fetch_binary(url, dest, dry_run):
                manifest.append(asdict(ManifestEntry(
                    id=f"{pmcid}-pdf",
                    source_type="pmc_pdf",
                    title=pmcid,
                    resolved_url=url,
                    license="CC BY (PMC Open Access, commercial-use-allowed subset)",
                    local_path=str(dest.relative_to(REPO_ROOT)),
                    sha256=sha256_of(dest),
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                )))

        image_keys = [k for k in keys if k.lower().endswith((".jpg", ".jpeg", ".png")) and "-g" in k]
        for img_key in image_keys:
            fname = img_key.split("/")[-1]
            dest = RAW_DIR / "pmc" / f"{pmcid}_images" / fname
            url = f"{PMC_OA_S3_BASE}/{img_key}"
            if fetch_binary(url, dest, dry_run):
                manifest.append(asdict(ManifestEntry(
                    id=f"{pmcid}-{fname}",
                    source_type="pmc_image",
                    title=f"{pmcid} figure {fname}",
                    resolved_url=url,
                    license="CC BY (PMC Open Access, commercial-use-allowed subset)",
                    local_path=str(dest.relative_to(REPO_ROOT)),
                    sha256=sha256_of(dest),
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                )))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only", default="guidelines,dailymed,pmc",
        help="Comma-separated subset of: guidelines,dailymed,pmc",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would be downloaded, don't fetch")
    args = parser.parse_args(list(argv) if argv is not None else None)
    steps = set(s.strip() for s in args.only.split(","))

    sources = load_sources()
    manifest = load_manifest()

    if "guidelines" in steps:
        download_guidelines(sources, manifest, args.dry_run)
    if "dailymed" in steps:
        download_dailymed(sources, manifest, args.dry_run)
    if "pmc" in steps:
        download_pmc(sources, manifest, args.dry_run)

    if not args.dry_run:
        save_manifest(manifest)
        print(f"\nWrote {len(manifest)} manifest entries to {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
