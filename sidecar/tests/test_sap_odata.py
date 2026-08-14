"""
Tests for the GTS OData V2 read path.

Payloads use the real field names from LLS_BPADDR_MNG_SRV's $metadata and the
real V2 envelope shapes, so these fail loudly if SAP's model shifts under us
rather than silently reading the wrong field.
"""

import json
from datetime import datetime, timezone

import httpx
import pytest

from core.models.schemas import IntakePath, ListSeverity
from core.sap.schemas import ScreenedPartnerAddress, _parse_sap_datetime
from core.sap.odata import ENTITY_SET, SERVICE_PATH, GtsODataClient, ODataError, _odata_datetime
from core.sap.mapper import (
    case_id_for,
    list_severity_for,
    network_evidence_notes,
    to_hit_inputs,
)

BASE = "https://agpapp.sap.pwc.com:8001"


def screened_row(**overrides):
    """A V2 root row with two expanded hits and network evidence."""
    row = {
        "LegalRegulation": "SDNUSA",
        "AddressID": "0000012345",
        "BusinessPartner": "0010000108",
        "LogicalSystemGroup": "AGPCLNT300",
        "GTSBusinessPartnerType": "BP",
        "GTSBusinessPartnerExternalID": "0010000108",
        "ForeignTradeOrganization": "FTO1",
        "SPLAuditTrailUUID": "3f2b9c14-5d7e-4a81-9c2f-1e6b8d4a7c30",
        "SPLScreenedAddressIsBlocked": True,
        "SPLScreeningBlockIsIndirect": False,
        "SPLScrngBPHasBlkdAssocdBank": False,
        "SPLScreeningCheckStatus": "02",
        "SPLScreeningCheckStatus_Text": "Blocked",
        "SPLScrngBlockProcessingStatus": "",
        "SPLScrngResultSystemDecision": "B",
        "SPLScreeningResultUserDecision": "",
        "SPLCheckDateTime": "/Date(1786060800000)/",
        "BusinessPartnerName": "Schmidt & Weber GmbH",
        "PersonFullName": "",
        "StreetName": "Grosse Elbstrasse 14",
        "CityName": "Hamburg",
        "Country": "DE",
        "Country_Text": "Germany",
        "to_SPLHitsDetailSet": {
            "results": [
                {
                    "AddressID": "0000012345",
                    "ItemNumber": "000001",
                    "LegalRegulation": "SDNUSA",
                    "BusinessPartner": "0010000108",
                    "SPLListType": "SDN",
                    "SPLListTypeDesc": "Specially Designated Nationals",
                    "SPLEntity": "0000028451",
                    "MatchedName": "X",
                    "MatchedAddress": "",
                    "MatchedId": "",
                    "GTSDataProvider": "DESCARTES",
                    "SPLGroup": "IRAN",
                    "SPLGroupDesc": "Iran Sanctions Programme",
                },
                {
                    "AddressID": "0000012345",
                    "ItemNumber": "000002",
                    "LegalRegulation": "SDNUSA",
                    "BusinessPartner": "0010000108",
                    "SPLListType": "ADV",
                    "SPLListTypeDesc": "Advisory",
                    "SPLEntity": "0000099001",
                    "MatchedName": "X",
                    "MatchedAddress": "X",
                    "MatchedId": "",
                    "GTSDataProvider": "DESCARTES",
                },
            ]
        },
        "to_SPLScrngBPIdentification": {
            "results": [
                {
                    "BusinessPartner": "0010000108",
                    "BPIdentificationType": "BUP002",
                    "BPIdentificationNumber": "HRB 54321",
                    "BPIdentificationTypeText": "Commercial Register",
                    "Country": "DE",
                },
                {
                    "BusinessPartner": "0010000108",
                    "BPIdentificationType": "VAT01",
                    "BPIdentificationNumber": "DE123456789",
                    "BPIdentificationTypeText": "VAT Registration Number",
                    "Country": "DE",
                },
            ]
        },
        "to_SPLScrngBPAssocdBlkdObj": {"results": []},
        "to_SPLScrngBPAssociatedBank": {"results": []},
    }
    row.update(overrides)
    return row


def bp_row(**overrides):
    """A V2 keyed-read response body for I_BusinessPartner."""
    row = {
        "BusinessPartner": "0010000108",
        "BusinessPartnerCategory": "2",  # 2 = Organization
        "BusinessPartnerType": "",
        "FirstName": "",
        "LastName": "",
        "BirthDate": None,
        "BusinessPartnerBirthName": "",
        "BusinessPartnerBirthplaceName": "",
        "BusPartNationality": "",
        "OrganizationFoundationDate": "/Date(852076800000)/",  # 1997-01-01
        "Industry": "MACH",
        "BusinessPartnerIsBlocked": False,
    }
    row.update(overrides)
    return row


def is_bp_request(request) -> bool:
    return "I_BusinessPartner" in str(request.url)


def make_client(handler, bp=None) -> GtsODataClient:
    """
    Wraps the handler so I_BusinessPartner requests get a BP master payload.

    Without this the screened-row handler answers BP reads too, and since the
    models ignore unknown fields, `master` gets silently populated with the
    wrong entity — a bug that hides rather than fails.
    """

    def routed(request):
        if is_bp_request(request):
            return httpx.Response(200, json={"d": bp if bp is not None else bp_row()})
        return handler(request)

    transport = httpx.MockTransport(routed)
    http = httpx.Client(transport=transport, headers={"Accept": "application/json"})
    return GtsODataClient(BASE, "TECHUSER", "pw", http_client=http)


class TestSapDateDecoding:
    """
    V2 sends /Date(ms)/ not ISO 8601. A misparse here silently shifts the
    watermark, which would drop or replay hits, so pin the exact values.
    """

    def test_epoch_millis_decoded_to_utc(self):
        # 1786579200000 ms == 2026-08-13T00:00:00Z
        assert _parse_sap_datetime("/Date(1786579200000)/") == datetime(
            2026, 8, 13, 0, 0, 0, tzinfo=timezone.utc
        )

    def test_positive_offset_applied_in_minutes(self):
        assert _parse_sap_datetime("/Date(1786579200000+0120)/") == datetime(
            2026, 8, 13, 2, 0, 0, tzinfo=timezone.utc
        )

    def test_negative_offset_applied_in_minutes(self):
        assert _parse_sap_datetime("/Date(1786579200000-0060)/") == datetime(
            2026, 8, 12, 23, 0, 0, tzinfo=timezone.utc
        )

    def test_empty_and_none_become_none(self):
        assert _parse_sap_datetime("") is None
        assert _parse_sap_datetime(None) is None

    def test_iso_string_passes_through(self):
        """Guards the V4 migration path, where dates arrive as ISO."""
        parsed = ScreenedPartnerAddress.model_validate(
            {**screened_row(), "SPLCheckDateTime": "2026-08-13T00:00:00Z"}
        )
        assert parsed.spl_check_datetime == datetime(2026, 8, 13, 0, 0, 0, tzinfo=timezone.utc)

    def test_watermark_round_trip(self):
        """Decoded value must re-serialise into a valid V2 filter literal."""
        dt = _parse_sap_datetime("/Date(1786579200000)/")
        assert _odata_datetime(dt) == "datetime'2026-08-13T00:00:00'"


class TestDateTimeLiteral:
    def test_naive_datetime(self):
        assert _odata_datetime(datetime(2026, 8, 13, 14, 30, 0)) == "datetime'2026-08-13T14:30:00'"

    def test_aware_datetime_normalised_to_utc_without_suffix(self):
        """V2 rejects an offset or trailing Z in a datetime literal."""
        dt = datetime(2026, 8, 13, 14, 30, 0, tzinfo=timezone.utc)
        out = _odata_datetime(dt)
        assert out == "datetime'2026-08-13T14:30:00'"
        assert "Z" not in out and "+" not in out


class TestQueryConstruction:
    def test_blocked_filter_always_applied(self):
        seen = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"d": {"results": []}})

        with make_client(handler) as c:
            list(c.iter_blocked_addresses())

        assert "SPLScreenedAddressIsBlocked eq true" in seen["params"]["$filter"]
        assert seen["params"]["sap-client"] == "300"
        assert seen["params"]["$format"] == "json"

    def test_watermark_added_to_filter(self):
        seen = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"d": {"results": []}})

        with make_client(handler) as c:
            list(c.iter_blocked_addresses(since=datetime(2026, 8, 13, 0, 0, 0)))

        assert "SPLCheckDateTime gt datetime'2026-08-13T00:00:00'" in seen["params"]["$filter"]

    def test_expand_covers_all_navigation_properties(self):
        seen = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"d": {"results": []}})

        with make_client(handler) as c:
            list(c.iter_blocked_addresses())

        expand = seen["params"]["$expand"]
        for nav in (
            "to_SPLHitsDetailSet",
            "to_SPLScrngBPIdentification",
            "to_SPLScrngBPAssocdBlkdObj",
            "to_SPLScrngBPAssociatedBank",
        ):
            assert nav in expand

    def test_unprocessed_only_filter(self):
        seen = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"d": {"results": []}})

        with make_client(handler) as c:
            list(c.iter_blocked_addresses(unprocessed_only=True))

        assert "SPLScrngBlockProcessingStatus eq ''" in seen["params"]["$filter"]


class TestParsing:
    def test_root_and_children_parsed(self):
        def handler(request):
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        with make_client(handler) as c:
            recs = list(c.iter_blocked_addresses())

        assert len(recs) == 1
        r = recs[0]
        assert r.partner_name == "Schmidt & Weber GmbH"
        assert r.is_blocked is True
        assert len(r.hits) == 2
        assert len(r.identifications) == 2
        assert r.hits[0].spl_entity == "0000028451"

    def test_deferred_navigation_yields_empty_children(self):
        """An unexpanded nav property comes back as __deferred, not data."""
        row = screened_row(to_SPLHitsDetailSet={"__deferred": {"uri": "http://x/y"}})

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [row]}})

        with make_client(handler) as c:
            recs = list(c.iter_blocked_addresses(expand=False))

        assert recs[0].hits == []

    def test_unmapped_sap_fields_ignored(self):
        row = screened_row(SomeFieldSapAddedInAnUpgrade="x", AnotherOne=42)

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [row]}})

        with make_client(handler) as c:
            recs = list(c.iter_blocked_addresses())

        assert recs[0].business_partner == "0010000108"

    def test_case_key_prefers_audit_trail_uuid(self):
        def handler(request):
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        with make_client(handler) as c:
            r = next(iter(c.iter_blocked_addresses()))

        assert r.case_key == "3f2b9c14-5d7e-4a81-9c2f-1e6b8d4a7c30"

    def test_case_key_falls_back_to_composite_when_uuid_absent(self):
        row = screened_row()
        del row["SPLAuditTrailUUID"]

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [row]}})

        with make_client(handler) as c:
            r = next(iter(c.iter_blocked_addresses()))

        assert r.case_key == "AGPCLNT300|BP|0010000108|0010000108|0000012345|SDNUSA"


class TestPaging:
    """Only page requests are counted; BP enrichment issues its own calls."""

    def test_stops_on_short_page(self):
        calls = []

        def handler(request):
            calls.append(dict(request.url.params))
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        with make_client(handler) as c:
            recs = list(c.iter_blocked_addresses(page_size=10))

        assert len(recs) == 1
        assert len(calls) == 1

    def test_follows_pages_until_exhausted(self):
        calls = []

        def handler(request):
            params = dict(request.url.params)
            calls.append(params)
            skip = int(params.get("$skip", 0))
            rows = [screened_row()] * 2 if skip < 4 else []
            return httpx.Response(200, json={"d": {"results": rows}})

        with make_client(handler) as c:
            recs = list(c.iter_blocked_addresses(page_size=2))

        assert len(recs) == 4
        assert [int(p["$skip"]) for p in calls] == [0, 2, 4]


class TestBpEnrichment:
    def test_master_attached_and_entity_type_authoritative(self):
        """BusinessPartnerCategory beats the PersonFullName heuristic."""

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        with make_client(handler) as c:
            rec = next(iter(c.iter_blocked_addresses()))

        assert rec.master is not None
        assert rec.master.is_natural_person is False
        assert rec.entity_type == "Legal Entity (Organization)"

    def test_natural_person_category(self):
        def handler(request):
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        bp = bp_row(
            BusinessPartnerCategory="1",
            FirstName="Hans-Peter",
            LastName="Schmidt",
            BirthDate="/Date(-372470400000)/",  # 1958-03-14
            BusPartNationality="RU",
            BusinessPartnerBirthplaceName="Moscow",
        )
        with make_client(handler, bp=bp) as c:
            rec = next(iter(c.iter_blocked_addresses()))

        assert rec.master.is_natural_person is True
        assert rec.entity_type == "Natural Person"
        assert rec.master.birth_date.date().isoformat() == "1958-03-14"
        assert rec.master.nationality == "RU"

    def test_unclassified_category_is_neutral_not_guessed(self):
        """§3.1 #3 — an unclassified BP must not be forced into either bucket."""

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        with make_client(handler, bp=bp_row(BusinessPartnerCategory="")) as c:
            rec = next(iter(c.iter_blocked_addresses()))

        assert rec.master.is_natural_person is None
        assert rec.master.entity_type == ""

    def test_select_limits_fields_requested(self):
        seen = {}

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        def routed(request):
            if is_bp_request(request):
                seen["params"] = dict(request.url.params)
                return httpx.Response(200, json={"d": bp_row()})
            return handler(request)

        http = httpx.Client(transport=httpx.MockTransport(routed))
        with GtsODataClient(BASE, "u", "p", http_client=http) as c:
            list(c.iter_blocked_addresses())

        # Tight $select rather than pulling all 81 properties
        assert "BusinessPartnerCategory" in seen["params"]["$select"]
        assert "BirthDate" in seen["params"]["$select"]
        assert len(seen["params"]["$select"].split(",")) < 20

    def test_bp_fetched_once_per_partner(self):
        """One BP with several blocked addresses must not refetch master."""
        bp_calls = {"n": 0}

        def routed(request):
            if is_bp_request(request):
                bp_calls["n"] += 1
                return httpx.Response(200, json={"d": bp_row()})
            skip = int(dict(request.url.params).get("$skip", 0))
            rows = [screened_row(AddressID=f"000001234{i}") for i in range(2)] if skip == 0 else []
            return httpx.Response(200, json={"d": {"results": rows}})

        http = httpx.Client(transport=httpx.MockTransport(routed))
        with GtsODataClient(BASE, "u", "p", http_client=http) as c:
            recs = list(c.iter_blocked_addresses(page_size=2))

        assert len(recs) == 2
        assert bp_calls["n"] == 1

    def test_bp_failure_degrades_to_none(self, monkeypatch):
        """Enrichment is additive; a missing BP must not fail the adjudication."""
        monkeypatch.setattr("core.sap.odata.time.sleep", lambda *_: None)

        def routed(request):
            if is_bp_request(request):
                return httpx.Response(404, text="Not Found")
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        http = httpx.Client(transport=httpx.MockTransport(routed))
        with GtsODataClient(BASE, "u", "p", http_client=http) as c:
            rec = next(iter(c.iter_blocked_addresses()))

        assert rec.master is None
        # Falls back to the heuristic rather than crashing
        assert rec.entity_type == "Legal Entity (BP)"

    def test_enrichment_can_be_disabled(self):
        bp_calls = {"n": 0}

        def routed(request):
            if is_bp_request(request):
                bp_calls["n"] += 1
                return httpx.Response(200, json={"d": bp_row()})
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        http = httpx.Client(transport=httpx.MockTransport(routed))
        with GtsODataClient(BASE, "u", "p", http_client=http) as c:
            rec = next(iter(c.iter_blocked_addresses(enrich_bp=False)))

        assert bp_calls["n"] == 0
        assert rec.master is None

    def test_discriminators_reach_hit_input(self):
        """§4.1 fields must land on HitInput for the extractor to see them."""

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        bp = bp_row(
            BusinessPartnerCategory="1",
            BirthDate="/Date(-372470400000)/",
            BusPartNationality="RU",
            BusinessPartnerBirthplaceName="Moscow",
            BusinessPartnerBirthName="Schmidt",
            Industry="MACH",
        )
        with make_client(handler, bp=bp) as c:
            hits = to_hit_inputs(next(iter(c.iter_blocked_addresses())))

        h = hits[0]
        assert h.bp_date_of_birth == "1958-03-14"
        assert h.bp_nationality == "RU"
        assert h.bp_birthplace == "Moscow"
        assert h.bp_name_at_birth == "Schmidt"
        assert h.bp_industry == "MACH"
        assert h.bp_entity_type == "Natural Person"
        # Full identifier set, not just the primary (§3.2)
        assert len(h.bp_all_identifiers) == 2

    def test_missing_master_leaves_discriminators_empty_not_fabricated(self):
        def handler(request):
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        with make_client(handler) as c:
            rec = next(iter(c.iter_blocked_addresses(enrich_bp=False)))

        h = to_hit_inputs(rec)[0]
        assert h.bp_date_of_birth == ""
        assert h.bp_nationality == ""
        assert h.bp_foundation_date == ""

    def test_date_only_no_spurious_time_component(self):
        """A DOB rendered with 00:00:00 implies precision we don't have."""

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        with make_client(handler) as c:
            h = to_hit_inputs(next(iter(c.iter_blocked_addresses())))[0]

        assert h.bp_foundation_date == "1997-01-01"
        assert ":" not in h.bp_foundation_date


class TestErrorHandling:
    def test_401_gives_actionable_message(self):
        def handler(request):
            return httpx.Response(401, text="Unauthorized")

        with make_client(handler) as c:
            with pytest.raises(ODataError, match="Authentication/authorization failed"):
                list(c.iter_blocked_addresses())

    def test_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr("core.sap.odata.time.sleep", lambda *_: None)
        attempts = {"n": 0}

        def handler(request):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return httpx.Response(503, text="busy")
            return httpx.Response(200, json={"d": {"results": []}})

        with make_client(handler) as c:
            list(c.iter_blocked_addresses())

        assert attempts["n"] == 3

    def test_gives_up_after_max_retries(self, monkeypatch):
        monkeypatch.setattr("core.sap.odata.time.sleep", lambda *_: None)

        def handler(request):
            return httpx.Response(503, text="busy")

        with make_client(handler) as c:
            with pytest.raises(ODataError, match="Giving up"):
                list(c.iter_blocked_addresses())


class TestMapper:
    def _record(self):
        def handler(request):
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        with make_client(handler) as c:
            return next(iter(c.iter_blocked_addresses()))

    def test_one_hit_input_per_matched_entry(self):
        hits = to_hit_inputs(self._record())
        assert len(hits) == 2
        assert {h.spl_entry_id for h in hits} == {"0000028451", "0000099001"}

    def test_match_basis_is_descriptive_not_numeric(self):
        hits = to_hit_inputs(self._record())
        assert hits[0].match_basis == "Matched on name"
        assert hits[1].match_basis == "Matched on name + address"
        # §6.4: no score is reported by this service; never fabricate one
        assert all(h.match_percentage == 0.0 for h in hits)

    def test_list_severity_per_hit(self):
        hits = to_hit_inputs(self._record())
        by_entry = {h.spl_entry_id: h for h in hits}
        assert by_entry["0000028451"].list_severity == ListSeverity.BLOCKING
        assert by_entry["0000099001"].list_severity == ListSeverity.ADVISORY

    def test_unknown_list_type_defaults_to_blocking(self):
        """§3.1 #1 — an unclassified list must not lower the evidence bar."""
        assert list_severity_for("SOMETHING_NEW") == ListSeverity.BLOCKING
        assert list_severity_for("") == ListSeverity.BLOCKING

    def test_registration_prefers_commercial_register(self):
        hits = to_hit_inputs(self._record())
        assert "HRB 54321" in hits[0].bp_registration_no

    def test_entity_type_uses_authoritative_bp_category(self):
        """Enriched records report BP category, not the gts_bp_type heuristic."""
        assert to_hit_inputs(self._record())[0].bp_entity_type == "Legal Entity (Organization)"

    def test_intake_path_is_bp_block(self):
        assert all(h.intake_path == IntakePath.BP_BLOCK for h in to_hit_inputs(self._record()))

    def test_case_id_is_deterministic(self):
        r = self._record()
        first = case_id_for(r, r.hits[0])
        second = case_id_for(r, r.hits[0])
        assert first == second
        assert first.endswith("::SDN::0000028451")

    def test_case_id_differs_per_spl_entry(self):
        r = self._record()
        assert case_id_for(r, r.hits[0]) != case_id_for(r, r.hits[1])

    def test_spl_entry_name_extracted_from_matched_name(self):
        """
        MatchedName carries the sanctioned party's own name as HTML, not a flag.
        Verified against a real AGP payload:
            '<strong>Baring</strong> <strong>Buyeers</strong><br>'
        Aliases, DOB, nationality and identifiers are still absent and still
        need a Z view over /SAPSLL/TSPL*.
        """
        row = screened_row()
        row["to_SPLHitsDetailSet"]["results"][0]["MatchedName"] = (
            "<strong>Baring</strong> <strong>Buyeers</strong><br>"
        )
        row["to_SPLHitsDetailSet"]["results"][0]["MatchedAddress"] = (
            "Av. de Arag&oacute;n,;   Madrid<br>"
        )

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [row]}})

        with make_client(handler) as c:
            hit = to_hit_inputs(next(iter(c.iter_blocked_addresses())))[0]

        assert hit.spl_entry_name == "Baring Buyeers"
        assert hit.spl_entry_address == "Av. de Aragón, Madrid"
        # Matched tokens are named, since token rarity is part of the basis (§3.1 #6)
        assert "Baring" in hit.match_basis and "Buyeers" in hit.match_basis


class TestNetworkEvidence:
    def test_indirect_block_surfaced(self):
        row = screened_row(SPLScreeningBlockIsIndirect=True)

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [row]}})

        with make_client(handler) as c:
            notes = network_evidence_notes(next(iter(c.iter_blocked_addresses())))

        assert any("INDIRECT" in n for n in notes)

    def test_blocked_bank_flag_and_detail(self):
        row = screened_row(
            SPLScrngBPHasBlkdAssocdBank=True,
            to_SPLScrngBPAssociatedBank={
                "results": [
                    {
                        "BankBusinessPartner": "0010009999",
                        "BusinessPartnerName": "Bank Melli Iran",
                        "Country": "IR",
                        "CountryShortName": "Iran",
                        "SPLScreeningCheckStatus": "02",
                        "SPLScreeningCheckStatus_Text": "Blocked",
                    }
                ]
            },
        )

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [row]}})

        with make_client(handler) as c:
            notes = network_evidence_notes(next(iter(c.iter_blocked_addresses())))

        assert any("blocked associated bank" in n for n in notes)
        assert any("Bank Melli Iran" in n for n in notes)

    def test_associated_blocked_object_surfaced(self):
        row = screened_row(
            to_SPLScrngBPAssocdBlkdObj={
                "results": [
                    {
                        "BusinessPartner1": "0010007777",
                        "BusinessPartnerName": "Shared Address Trading",
                        "CityName": "Dubai",
                        "CountryShortName": "United Arab Emirates",
                        "SPLScreeningCheckStatus_Text": "Blocked",
                        "SPLScrngBPIndrctBlkObjType_Text": "Shared Address",
                    }
                ]
            }
        )

        def handler(request):
            return httpx.Response(200, json={"d": {"results": [row]}})

        with make_client(handler) as c:
            notes = network_evidence_notes(next(iter(c.iter_blocked_addresses())))

        assert any("Shared Address Trading" in n and "Dubai" in n for n in notes)

    def test_no_notes_when_no_associations(self):
        def handler(request):
            return httpx.Response(200, json={"d": {"results": [screened_row()]}})

        with make_client(handler) as c:
            assert network_evidence_notes(next(iter(c.iter_blocked_addresses()))) == []


class TestCount:
    def test_count_parses_plain_text(self):
        def handler(request):
            assert request.url.path.endswith("/$count")
            return httpx.Response(200, text="1274")

        with make_client(handler) as c:
            assert c.count_blocked() == 1274
