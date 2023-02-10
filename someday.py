#!/usr/bin/env python3

import curses
import subprocess
import textwrap
import os
import re
import sys

calendar = None
proxy_calendar = None
calendar_lines = []

def initialize():
    global calendar
    global proxy_calendar

    with open("%s/.when/preferences" % os.environ["HOME"]) as f:
        prefs = f.read()
    m = re.match(r"^\s*calendar\s*=\s*(.+)$", prefs, flags=re.MULTILINE)
    if m is not None:
        calendar = m.group(1).strip()
    else:
        sys.exit("No calendar configuration for 'when' was found.")
    proxy_calendar = calendar + ".SOMEDAY"
    if os.path.exists(proxy_calendar):
        sys.exit("The calendar seems to be in edition. Delete the file %s and try again." % proxy_calendar)

def cleanup():
    os.unlink(proxy_calendar)

# Copy the when's calendary to a temporary file where each non-empty line is line-numbered

def generate_proxy_calendar():
    calendar_lines.clear()
    with open(calendar) as infile:
        lines = infile.read().splitlines()
    i = 0
    with open(proxy_calendar, "w") as outfile:
        for line in lines:
            tmp_line = "%s-%s" % (line, i) if line.strip() else line
            print(tmp_line, file=outfile)
            calendar_lines.append(tmp_line)
            i += 1

# Use the temporary file created above as input for when to get a list of items along with their line numbers

def get_items():
    tmp = subprocess.run(["when", "--calendar=%s" % proxy_calendar, "--noheader", "--wrap=0"],
                         capture_output=True, text=True, check=True).stdout
    tmp = re.findall(r"^(.+)-(\d+)$", tmp, flags=re.MULTILINE)
    items = [x[0] for x in tmp]
    line_numbers = [x[1] for x in tmp]

    return items, line_numbers

# This is the main function for browsing and updating the list of items

def main(stdscr):
    # Initialize curses
    curses.curs_set(0)

    curses.use_default_colors()
    curses.init_pair(1, -1, -1)
    curses.init_pair(2, curses.COLOR_RED, curses.COLOR_WHITE)

    # Have curses interpret special input
    stdscr.keypad(True)

    # Get the size of the window and define areas for the list of items and menu
    height, width = stdscr.getmaxyx()
    first_row = 2
    last_row = height - 3
    menu_row = height - 1

    # Get the list of items
    items, _ = get_items()

    # Define a menu of actions to be applied to the selected item
    menu = ["Action 1", "Action 2", "Action 3"]

    # Initialize the selected item and action
    selected_item = 0
    selected_action = 0

    # Initialize the first shown item
    first_item = 0

    # Main loop for handling key inputs
    while True:

        stdscr.clear()

        # Show the date at top of the screen

        stdscr.addstr(0, 0, get_date())

        # Draw the list of items
        if not items:
            stdscr.addstr(first_row, 0, "No items were found for today (and surrounding dates).")
        else:
            row = first_row
            last_item = -1
            for i, item in enumerate(items[first_item:]):
                if row > last_row:
                    break
                color = 2 if i == selected_item else 1
                stdscr.addstr(row, 0, item[:width], curses.color_pair(color))
                last_item = i
                row += 1

        # Draw the menu of actions
        for i, action in enumerate(menu):
            color = 2 if i == selected_action else 1
            stdscr.addstr(menu_row, i * (width // len(menu)), action, curses.color_pair(color))

        stdscr.refresh()

        # Get the key input
        key = stdscr.getch()

        # Handle the cursor keys to navigate the list of items and the menu of actions
        if key == curses.KEY_UP:
            if selected_item > 0:
                selected_item -= 1
            elif first_item > 0:
                first_item -= 1
        elif key == curses.KEY_DOWN:
            if selected_item < last_item:
                selected_item += 1
            elif first_item + selected_item < len(items) - 1:
                first_item += 1
        elif key == curses.KEY_LEFT and selected_action > 0:
            selected_action -= 1
        elif key == curses.KEY_RIGHT and selected_action < len(menu) - 1:
            selected_action += 1
        elif key == 10:
            expand_item(items[first_item+selected_item], first_row + selected_item, last_row, width)
        elif chr(key).lower() == "q":
            break

def get_date():
    return subprocess.run(["when", "d"], capture_output=True).stdout

def expand_item(item, row, last_row, width):
    row -= 1
    lines = textwrap.wrap(item, width-2)
    rows = len(lines) + 2
    pad = curses.newpad(rows, width)
    pad.border()
    for i, line in enumerate(lines):
        pad.addstr(i+1, 1, line)
    row = min(row, last_row - rows + 1)
    pad.refresh(0, 0, row, 0, last_row, width)
    pad.getch()

if __name__ == "__main__":
    try:
        initialize()
        generate_proxy_calendar()
        curses.wrapper(main)
    finally:
        cleanup()
