import re
import json

# Text from the user's screenshot (approximate)
content_valid = """
• regions to include (default: all regions),
• whether to include Savings Plans / Reserved usage in analysis.

json_suggestions
[
{"question":"Run the EC2 cost + CloudWatch metric queries now for the last 30 days?","label":"Run queries","description":"I will query Cost Explorer and CloudWatch, return a table of top instances by cost with utilization and concrete recommendations.","icon":"▶️"},
{"question":"Which regions/accounts should I include?","label":"Select scope","description":"Specify regions (e.g., us-east-1, eu-west-1) or say 'all' to scan all regions in the account.","icon":"📍"},
{"question":"Do you use Cost Allocation tags for instances (e.g., Project, Owner)?","label":"Tagging info","description":"If yes, provide tag keys to produce per-tag cost breakdowns and map costs to teams/projects.","icon":"🏷️"}
]
"""

content_truncated = """
• regions to include (default: all regions),
• whether to include Savings Plans / Reserved usage in analysis.

json_suggestions
[
{"question":"Run the EC2 cost + CloudWatch metric queries now for the last 30 days?","label":"Run queries","description":"I will query Cost Explorer and CloudWatch, return a table of top instances by cost with utilization and concrete recommendations.","icon":"▶️"},
{"question":"Which regions/accounts should I include?","label":"Select scope","description":"Specify regions (e.g., us-east-1, eu-west-1) or say 'all' to scan all regions in the account.","icon":"📍"},
{"question":"Do you use Cost Allocation tags for instances (e.g., Project, Owner)?","label":"Tagging info","description":"If yes, provide tag keys to produce per-tag cost breakdowns and map costs to teams/projects.","icon":"🏷️"}
""" # Missing closing bracket

def process_content(text, name):
    print(f"\n--- Processing {name} ---")
    # Find the start of the suggestions block
    # Matches 'json_suggestions' or '```json_suggestions' at the start of a line or after a newline
    start_pattern = r"(?:(?:\n|^)json_suggestions|```json_suggestions)"
    match = re.search(start_pattern, text)

    if match:
        print(f"Found start at index {match.start()}")
        # Extract the potential block
        block = text[match.start():]
        print(f"Block to strip (first 50 chars): {block[:50]!r}...")
        
        # Try to extract JSON from the block
        json_match = re.search(r"(\[\s*\{[\s\S]*)", block)
        if json_match:
            json_text = json_match.group(1)
            # Remove potential closing backticks if present
            json_text = re.sub(r"\s*```\s*$", "", json_text)
            
            try:
                suggestions = json.loads(json_text)
                print("JSON parse successful.")
            except json.JSONDecodeError:
                print("JSON parse failed (likely truncated).")
        else:
            print("No JSON array found in block.")
            
        # STRIP THE BLOCK REGARDLESS
        new_content = text[:match.start()].strip()
        print(f"New content ends with: {new_content[-50:]!r}")
    else:
        print("No suggestions block found.")

process_content(content_valid, "Valid Content")
process_content(content_truncated, "Truncated Content")
