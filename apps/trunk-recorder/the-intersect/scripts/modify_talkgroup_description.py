#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pyfiglet",
# ]
# ///

# uv add --script letterkenny.py <package>

from pyfiglet import Figlet
import csv

def main():
    slant_font = Figlet(font="slant")
    print(slant_font.renderText("tgmod"))


    new_entries = []
    headers = []
    with open('psern_talkgroups_orig.csv', 'r') as file:
        reader = csv.DictReader(file)
        headers = reader.fieldnames
        data = list(reader)
        
        for row in data:
            desc = generate_new_description(row)
            row['Description'] = desc
            new_entries.append(row)

    # Write the new entries to a CSV file
    with open('psern_talkgroups_mod.csv', 'w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(new_entries)

def generate_new_description(row):
    return f'{row["Category"]} ({row["Tag"]}) - {row["Description"]}'


if __name__ == "__main__":
    main()
    
