from quoteguard.inputs import QuoteInput, TaskInput
from quoteguard.questions import GENERIC, TEMPLATES, make_questions, question_for


def _ti():
    return TaskInput("T", "x", [QuoteInput(1, "C1", "text", "a", [], True), QuoteInput(2, "C2", "text", "b", [], True)])


def test_dim_divisor_question():
    assert make_questions(["C2.dim_divisor"], _ti()) == ["报价2的计泡系数是 5000 还是 6000？"]


def test_every_template_renders_for_its_term():
    for name, tpl in TEMPLATES.items():
        q = question_for(f"C1.{name}", _ti())
        assert q.startswith("报价1") and "{" not in q


def test_chinese_term_name_and_unknown_term():
    assert question_for("C1.清关费", _ti()) == question_for("C1.broker_fee_cny", _ti())
    q = question_for("C2.goods_class", _ti())
    assert q == GENERIC.format(q="报价2", name="goods_class")


def test_dedupe_and_order_and_bare_name():
    qs = make_questions(["C2.ddp", "C1.fuel_pct", "C2.ddp"], _ti())
    assert len(qs) == 2 and qs[0].startswith("报价2") and qs[1].startswith("报价1")
    assert question_for("dim_divisor", _ti()).startswith("该报价")
    assert make_questions([], _ti()) == []
