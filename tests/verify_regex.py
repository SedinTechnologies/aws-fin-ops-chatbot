import re
import json

def test_regex():
    patterns = [
        r"```json_suggestions\s*([\s\S]*?)\s*```",  # Current strict pattern
        r"(?:```json_suggestions)?\s*(\[\s*\{[\s\S]*?\}\s*\])\s*(?:```)?", # More flexible pattern
    ]

    test_cases = [
        # Case 1: Standard format with backticks
        """
        Some text.
        ```json_suggestions
        [
            {"question": "q1", "label": "l1"}
        ]
        ```
        """,
        # Case 2: Missing closing backtick
        """
        Some text.
        ```json_suggestions
        [
            {"question": "q1", "label": "l1"}
        ]
        """,
        # Case 3: No backticks, just the tag
        """
        Some text.
        json_suggestions
        [
            {"question": "q1", "label": "l1"}
        ]
        """,
        # Case 4: The case from the user screenshot (raw text)
        """
        I can proceed with any of the above...

        json_suggestions
        [
        {"question": "Show CPU...", "label": "Utilization", "description": "Fetch CW...", "icon": "📈"},
        {"question": "Get per-instance...", "label": "Cost", "description": "More accurate...", "icon": "💰"}
        ]
        """
    ]

    print(f"Testing {len(patterns)} patterns against {len(test_cases)} cases...\n")

    for p_idx, pattern in enumerate(patterns):
        print(f"Pattern {p_idx + 1}: {pattern}")
        success_count = 0
        for i, case in enumerate(test_cases):
            match = re.search(pattern, case)
            if match:
                try:
                    # For the flexible pattern, we might need to be careful about which group we grab
                    # The flexible pattern has one capturing group for the JSON content
                    content = match.group(1)
                    # Clean up any potential leading/trailing non-json chars if the regex was loose
                    # But the regex (\\[ ... \\]) should capture the array.
                    
                    json.loads(content)
                    print(f"  Case {i + 1}: MATCH & VALID JSON")
                    success_count += 1
                except json.JSONDecodeError as e:
                    print(f"  Case {i + 1}: MATCH but INVALID JSON: {e}")
            else:
                print(f"  Case {i + 1}: NO MATCH")
        print(f"  Success Rate: {success_count}/{len(test_cases)}\n")

if __name__ == "__main__":
    test_regex()
