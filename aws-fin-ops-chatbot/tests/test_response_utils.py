import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.response_utils import parse_structured_response


def test_parse_structured_response_full_schema():
    content = "Title::Here is **markdown**::[{\"icon\":\"💡\",\"question\":\"Next?\"}]"

    title, markdown, next_questions = parse_structured_response(content)

    assert title == "Title"
    assert markdown == "Here is **markdown**"
    assert next_questions == [{"icon": "💡", "question": "Next?"}]


def test_parse_structured_response_with_trailing_json_block():
    content = (
        "RDS – October 2025 cost by region::"
        "Highlights:\n- item one\n- item two\n"
        "[{\"icon\":\"📊\",\"question\":\"Show details\"}]"
    )

    title, markdown, next_questions = parse_structured_response(content)

    assert title == "RDS – October 2025 cost by region"
    assert markdown.endswith("item two")
    assert next_questions == [{"icon": "📊", "question": "Show details"}]


def test_parse_structured_response_handles_invalid_json():
    content = "Only markdown without questions"

    title, markdown, next_questions = parse_structured_response(content)

    assert title is None
    assert markdown == content
    assert next_questions == []


def test_parse_structured_response_converts_html_lists():
    content = "title::<ul><li><b>Item</b> A</li><li>Second</li></ul>::[]"

    _, markdown, _ = parse_structured_response(content)

    assert "- **Item** A" in markdown
    assert "- Second" in markdown
    assert "<" not in markdown


def test_parse_structured_response_realistic_block():
    content = (
        "Title::<ul> <li>Total EC2 cost (Oct 1–31, 2025): <b>$1,247.89</b> UnblendedCost</li>"
        "<li>Change vs September 2025: <b>+ $132.45 (+11.9%)</b></li></ul>::[]"
    )

    _, markdown, _ = parse_structured_response(content)

    assert "**$1,247.89**" in markdown
    assert "<ul>" not in markdown
    assert markdown.count("- ") >= 2
