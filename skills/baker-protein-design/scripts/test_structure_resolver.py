#!/usr/bin/env python3
"""Offline tests for the clean-room structure resolver."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import structure_resolver as resolver


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self) -> bytes:
        return self.payload


class StructureResolverTests(unittest.TestCase):
    def test_standard_pdb_urls_cover_cif_pdb_and_assembly(self):
        self.assertEqual(
            resolver.rcsb_url("4WKQ", coordinate_format="cif"),
            "https://files.rcsb.org/download/4WKQ.cif",
        )
        self.assertEqual(
            resolver.rcsb_url("4wkq", coordinate_format="pdb"),
            "https://files.rcsb.org/download/4WKQ.pdb",
        )
        self.assertEqual(
            resolver.rcsb_url("4WKQ", coordinate_format="cif", assembly=1),
            "https://files.rcsb.org/download/4WKQ-assembly1.cif",
        )

    def test_extended_identifier_supports_mmcif_only(self):
        self.assertEqual(
            resolver.rcsb_url("pdb_00004wkq", coordinate_format="cif"),
            "https://files.rcsb.org/download/PDB_00004WKQ.cif",
        )
        with self.assertRaises(ValueError):
            resolver.rcsb_url("pdb_00004wkq", coordinate_format="pdb")

    def test_invalid_pdb_identifier_is_rejected(self):
        for value in ("", "../4WKQ", "123", "4WKQ?download=1", "pdb_123"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolver.rcsb_url(value)

    def test_dry_run_never_calls_network(self):
        calls = []

        def forbidden_open(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("network must not be called")

        summary = resolver.resolve_pdb(
            "4WKQ",
            coordinate_format="cif",
            execute=False,
            opener=forbidden_open,
        )
        self.assertEqual(calls, [])
        self.assertEqual(summary["mode"], "dry-run")
        self.assertEqual(summary["method"], "GET")

    def test_fetch_writes_payload_and_provenance(self):
        payload = b"data_4WKQ\n#\n"
        calls = []

        def fake_open(request, timeout):
            calls.append((request.full_url, timeout))
            return FakeResponse(payload)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "4WKQ.cif"
            result = resolver.resolve_pdb(
                "4WKQ",
                coordinate_format="cif",
                execute=True,
                output=output,
                opener=fake_open,
                clock=lambda: "2026-07-24T00:00:00Z",
            )
            provenance_path = Path(str(output) + ".provenance.json")
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))

            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(calls, [(resolver.rcsb_url("4WKQ"), 30)])
            self.assertEqual(result["provenance_file"], str(provenance_path))
            self.assertEqual(provenance["url"], resolver.rcsb_url("4WKQ"))
            self.assertEqual(provenance["bytes"], len(payload))
            self.assertEqual(
                provenance["sha256"], hashlib.sha256(payload).hexdigest()
            )
            self.assertEqual(provenance["acquired_at"], "2026-07-24T00:00:00Z")

    def test_execute_requires_explicit_output(self):
        with self.assertRaises(ValueError):
            resolver.resolve_pdb("4WKQ", execute=True)

    def test_uniprot_accession_uses_exact_post_query(self):
        captured = {}
        response = {"results": [{"primaryAccession": "P00533"}]}

        def fake_open(request, timeout):
            captured["method"] = request.get_method()
            captured["url"] = request.full_url
            captured["body"] = request.data.decode("ascii")
            captured["timeout"] = timeout
            return FakeResponse(json.dumps(response).encode("utf-8"))

        result = resolver.query_uniprot_accession(
            "P00533", execute=True, opener=fake_open
        )
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], resolver.UNIPROT_SEARCH_URL)
        self.assertIn("query=accession%3AP00533", captured["body"])
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(result["query_kind"], "accession_exact")
        self.assertEqual(result["results"][0]["primaryAccession"], "P00533")

    def test_uniprot_accession_dry_run_is_offline(self):
        calls = []

        def forbidden_open(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("network must not be called")

        result = resolver.query_uniprot_accession(
            "P00533", execute=False, opener=forbidden_open
        )
        self.assertEqual(calls, [])
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(result["method"], "POST")
        self.assertEqual(result["query"], "accession:P00533")

    def test_gene_candidates_are_unreviewed_and_preserve_api_order(self):
        response = {
            "results": [
                {"primaryAccession": "Q22222", "entryType": "reviewed"},
                {"primaryAccession": "A11111", "entryType": "unreviewed"},
            ]
        }

        def fake_open(request, timeout):
            return FakeResponse(json.dumps(response).encode("utf-8"))

        result = resolver.query_uniprot_gene("EGFR", execute=True, opener=fake_open)
        self.assertEqual(
            [item["candidate_identifier"] for item in result["candidates"]],
            ["Q22222", "A11111"],
        )
        self.assertTrue(
            all(
                item["assessment_status"] == "unreviewed_candidate"
                for item in result["candidates"]
            )
        )
        self.assertEqual([item["api_order"] for item in result["candidates"]], [1, 2])

    def test_outputs_never_use_claimed_selection_language(self):
        source = Path(resolver.__file__).read_text(encoding="utf-8").casefold()
        prohibited = "b" + "est"
        self.assertNotIn(prohibited, source)


if __name__ == "__main__":
    unittest.main()
