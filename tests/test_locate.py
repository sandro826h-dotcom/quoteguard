from decimal import Decimal as D

from quoteguard.locate import verify, verify_terms
from quoteguard.schema import Field, QuoteTerms, Span

TEXT = "整票  21kg起计，\n体积重除以 6000；燃油附加费10%。"


def test_verify_whitespace_normalised():
    assert verify("整票 21kg起计，", TEXT)
    assert verify("  体积重除以\t6000 ", TEXT)
    assert verify("起计，\n体积重", TEXT)


def test_verify_rejects_absent_and_empty():
    assert not verify("体积重除以 5000", TEXT)
    assert not verify("", TEXT)
    assert not verify("   ", TEXT)
    assert not verify("abc", "")


def _terms():
    return QuoteTerms(
        dim_divisor=Field(D(6000), "stated", [Span("e", "text", "体积重除以 6000")]),
        fuel_pct=Field(D("0.1"), "stated", [Span("e", "text", "燃油 20%"), Span("e", "text", "燃油附加费10%")]),
        min_kg_shipment=Field(D(21), "stated", [Span("e", "text", "最低 99kg")]),
        broker_fee_cny=Field(D(150), "stated", []),
        isf_fee_cny=Field(None, "missing", []))


def test_verify_terms_downgrades_only_when_all_fragments_fail():
    t, down = verify_terms(_terms(), TEXT)
    assert down == ["min_kg_shipment"]
    assert t.min_kg_shipment.status == "missing" and t.min_kg_shipment.value is None
    assert t.dim_divisor.value == D(6000)
    assert t.fuel_pct.status == "stated"          # one of two fragments verifies
    assert t.broker_fee_cny.status == "stated"     # no fragment: kept


def test_verify_terms_require_quotes_and_input_untouched():
    src = _terms()
    t, down = verify_terms(src, TEXT, require_quotes=True)
    assert set(down) == {"min_kg_shipment", "broker_fee_cny"}
    assert src.min_kg_shipment.status == "stated"   # input not modified


def test_verify_terms_conflicting_keeps_when_one_fragment_found():
    t = QuoteTerms(dim_divisor=Field([D(5000), D(6000)], "conflicting",
                                     [Span("e", "text", "除以 5000"), Span("e", "text", "除以 6000")]))
    out, down = verify_terms(t, TEXT)
    assert down == [] and out.dim_divisor.status == "conflicting"


def test_verify_tolerates_moved_spaces():
    src = "报价有效期2026-10-01 至2026-10-31"
    assert verify("报价有效期2026-10-01 至 2026-10-31", src)
    assert verify("报价有效期 2026-10-01至2026-10-31", src)
    assert verify("Whole lot最低计费重", "| Whole lot 最低计费重 | 不设 |")
    assert not verify("报价有效期2026-10-01 至 2026-11-30", src)
    t = QuoteTerms(dim_divisor=Field(D(6000), "stated", [Span("e", "text", "体积重 除以6000")]))
    out, down = verify_terms(t, "体积重除以 6000")
    assert down == [] and out.dim_divisor.status == "stated"


def test_verify_space_between_digits_is_not_dropped():
    assert not verify("12 30", "1230 kg")
    assert not verify("1230", "12 30 kg")
    assert not verify("A B", "AB")
    # spaces next to Chinese text may differ
    assert verify("整票21kg 起计", "整票 21kg起计")
    assert verify("最低 21 kg", "最低21 kg")
    assert verify("12 30", "共 12  30 件")
