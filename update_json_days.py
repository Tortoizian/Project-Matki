import json
import re
from pathlib import Path

def parse_days(text: str) -> int:
    if not isinstance(text, str):
        return 3 * 365
    
    t = text.lower()
    
    # Life or death
    if "life" in t or "death" in t:
        return 20 * 365  # Cap at 20 years for life calculation
        
    # Extract digits for years/months
    years_match = re.search(r"(\d+)\s+years?", t)
    months_match = re.search(r"(\d+)\s+months?", t)
    
    days = 0
    if years_match:
        days += int(years_match.group(1)) * 365
    if months_match:
        days += int(months_match.group(1)) * 30
        
    # If we found explicit time
    if days > 0:
        return days
        
    # Fallback to 3 years if it says "Same as..." or has no clear numbers
    return 3 * 365

def main():
    json_path = Path("bns_bail_mapping.json")
    
    if not json_path.exists():
        print(f"Error: {json_path} not found.")
        return
        
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    updated_count = 0
    for key, value in data.items():
        if key == "_metadata":
            continue
            
        if isinstance(value, dict):
            punishment_text = value.get("punishment", "")
            # Calculate and add the new field
            value["max_sentence_days"] = parse_days(punishment_text)
            updated_count += 1
            
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        
    print(f"Successfully processed {updated_count} offences and added 'max_sentence_days' to bns_bail_mapping.json")

if __name__ == "__main__":
    main()
