#!/usr/bin/env python3
"""Clean-room, offline-first structure identifier and source URL resolver."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


RCSB_DOWNLOAD_ROOT = "https://files.rcsb.org/download"
UNIPROT_SEARCH_URL = "https://rest.uniprot.org/uniprotkb/search"
DEFAULT_TIMEOUT_SECONDS = 30
STANDARD_PDB_ID_RE = re.compile(r"^[0-9][A-Za-z0-9]{3}$")
EXTENDED_PDB_ID_RE = re.compile(r"^PDB_[A-Za-z0-9]{8}$", re.IGNORECASE)
UNIPROT_ACCESSION_RE = re.compile(r"^[A-Za-z0-9]{6,10}$")
GENE_SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


class ResolverError(RuntimeError):
    """A safe resolver error that contains no remote response content."""


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _normalize_pdb_id(pdb_id: str) -> tuple[str, bool]:
    value = str(pdb_id or "").strip().upper()
    if STANDARD_PDB_ID_RE.fullmatch(value):
        return value, False
    if EXTENDED_PDB_ID_RE.fullmatch(value):
        return value, True
    raise ValueError("PDB identifier must be an explicit 4-character or PDB_ identifier")


def rcsb_url(
    pdb_id: str,
    *,
    coordinate_format: str = "cif",
    assembly: int | None = None,
) -> str:
    """Return an official RCSB coordinate URL without selecting a structure."""
    identifier, is_extended = _normalize_pdb_id(pdb_id)
    file_format = str(coordinate_format or "").strip().casefold()
    if file_format not in {"cif", "pdb"}:
        raise ValueError("Coordinate format must be cif or pdb")
    if is_extended and file_format == "pdb":
        raise ValueError("Extended PDB identifiers require mmCIF")
    if assembly is not None and (
        not isinstance(assembly, int) or isinstance(assembly, bool) or assembly < 1
    ):
        raise ValueError("Assembly number must be a positive integer")
    if assembly is None:
        filename = f"{identifier}.{file_format}"
    elif file_format == "cif":
        filename = f"{identifier}-assembly{assembly}.cif"
    else:
        filename = f"{identifier}.pdb{assembly}"
    return f"{RCSB_DOWNLOAD_ROOT}/{filename}"


def _read_response(request: urllib.request.Request, *, opener, timeout: int) -> bytes:
    try:
        with opener(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ResolverError("Remote request failed; response content was not retained") from exc


def resolve_pdb(
    pdb_id: str,
    *,
    coordinate_format: str = "cif",
    assembly: int | None = None,
    execute: bool = False,
    output: str | Path | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    clock: Callable[[], str] = _utc_now,
) -> dict[str, Any]:
    """Plan or execute one explicit RCSB coordinate fetch."""
    url = rcsb_url(
        pdb_id, coordinate_format=coordinate_format, assembly=assembly
    )
    summary: dict[str, Any] = {
        "mode": "execute" if execute else "dry-run",
        "method": "GET",
        "url": url,
        "timeout_seconds": timeout,
    }
    if not execute:
        return summary
    if output is None:
        raise ValueError("Fetch execution requires an explicit --output path")
    output_path = Path(output).expanduser()
    if output_path.exists() and output_path.is_dir():
        raise ValueError("Output must be a file path")
    request = urllib.request.Request(url, method="GET")
    payload = _read_response(request, opener=opener, timeout=timeout)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    provenance_path = Path(str(output_path) + ".provenance.json")
    provenance = {
        "url": url,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "acquired_at": clock(),
    }
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary.update(
        {
            "output_file": str(output_path),
            "provenance_file": str(provenance_path),
            **provenance,
        }
    )
    return summary


def _uniprot_post(
    query: str,
    *,
    execute: bool,
    opener: Callable[..., Any],
    timeout: int,
    size: int,
) -> dict[str, Any]:
    fields = "accession,id,protein_name,gene_names,reviewed"
    summary: dict[str, Any] = {
        "mode": "execute" if execute else "dry-run",
        "method": "POST",
        "url": UNIPROT_SEARCH_URL,
        "query": query,
        "fields": fields,
        "size": size,
        "timeout_seconds": timeout,
    }
    if not execute:
        return summary
    body = urllib.parse.urlencode(
        {
            "query": query,
            "format": "json",
            "fields": fields,
            "size": str(size),
        }
    ).encode("ascii")
    request = urllib.request.Request(
        UNIPROT_SEARCH_URL,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    payload = _read_response(request, opener=opener, timeout=timeout)
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ResolverError("Remote response could not be decoded") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("results"), list):
        raise ResolverError("Remote response did not match the expected result shape")
    summary["results"] = decoded["results"]
    return summary


def query_uniprot_accession(
    accession: str,
    *,
    execute: bool = False,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Plan or run an exact accession query through the UniProt Search API."""
    value = str(accession or "").strip().upper()
    if not UNIPROT_ACCESSION_RE.fullmatch(value):
        raise ValueError("Invalid UniProt accession")
    result = _uniprot_post(
        f"accession:{value}",
        execute=execute,
        opener=opener,
        timeout=timeout,
        size=1,
    )
    result["query_kind"] = "accession_exact"
    return result


def query_uniprot_gene(
    gene: str,
    *,
    execute: bool = False,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    size: int = 25,
) -> dict[str, Any]:
    """Return API-ordered candidate identifiers without selecting a structure."""
    value = str(gene or "").strip().upper()
    if not GENE_SYMBOL_RE.fullmatch(value):
        raise ValueError("Invalid gene symbol")
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 100:
        raise ValueError("Gene query size must be between 1 and 100")
    result = _uniprot_post(
        f"gene_exact:{value}",
        execute=execute,
        opener=opener,
        timeout=timeout,
        size=size,
    )
    result["query_kind"] = "gene_candidate_identifiers"
    if execute:
        candidates = []
        for index, item in enumerate(result.pop("results"), start=1):
            identifier = item.get("primaryAccession") if isinstance(item, dict) else None
            if identifier:
                candidates.append(
                    {
                        "candidate_identifier": str(identifier),
                        "assessment_status": "unreviewed_candidate",
                        "api_order": index,
                    }
                )
        result["candidates"] = candidates
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve explicit structure identifiers; default is offline dry-run"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pdb_parser = subparsers.add_parser("pdb", help="Resolve an explicit RCSB PDB ID")
    pdb_parser.add_argument("pdb_id")
    pdb_parser.add_argument("--format", choices=("cif", "pdb"), default="cif")
    pdb_parser.add_argument("--assembly", type=int)
    pdb_parser.add_argument("--execute", action="store_true")
    pdb_parser.add_argument("--output")

    accession_parser = subparsers.add_parser(
        "uniprot-accession", help="Query one exact UniProt accession"
    )
    accession_parser.add_argument("accession")
    accession_parser.add_argument("--execute", action="store_true")

    gene_parser = subparsers.add_parser(
        "uniprot-gene", help="List API-ordered gene candidate identifiers"
    )
    gene_parser.add_argument("gene")
    gene_parser.add_argument("--size", type=int, default=25)
    gene_parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "pdb":
        result = resolve_pdb(
            args.pdb_id,
            coordinate_format=args.format,
            assembly=args.assembly,
            execute=args.execute,
            output=args.output,
        )
    elif args.command == "uniprot-accession":
        result = query_uniprot_accession(args.accession, execute=args.execute)
    else:
        result = query_uniprot_gene(
            args.gene, execute=args.execute, size=args.size
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
