#!/usr/bin/env python3

import curses
import subprocess
import textwrap
import os
import re
import sys
from collections import namedtuple

calendar = None
proxy_calendar = None
calendar_lines = []

items = []
line_numbers = []

Action = namedtuple("Action", ["key", "name", "action"])

def tmp(selected_item, minrow, mincol, maxrow, maxcol):
    minrow -= 1
    width = maxcol - mincol + 1
    item = items[selected_item]
    lines = textwrap.wrap(item, width-2)
    line_number = line_numbers[selected_item]
    lines.insert(0, str(line_number))
    height = len(lines) + 2
    pad = curses.newpad(height, width)
    pad.border()
    for i, line in enumerate(lines):
        pad.addstr(i+1, 1, line)
    minrow = min(minrow, maxrow - height + 1)
    pad.refresh(0, 0, minrow, mincol, maxrow, maxcol)
    pad.getch()

menu = [Action("t", "Tmp", tmp)]
key_bindings = {x.key: x.action for x in menu}

def expand_item(selected_item, minrow, mincol, maxrow, maxcol):
    minrow -= 1
    width = maxcol - mincol + 1
    item = items[selected_item]
    lines = textwrap.wrap(item, width-2)
    height = len(lines) + 2
    pad = curses.newpad(height, width)
    pad.border()
    for i, line in enumerate(lines):
        pad.addstr(i+1, 1, line)
    minrow = min(minrow, maxrow - height + 1)
    pad.refresh(0, 0, minrow, mincol, maxrow, maxcol)
    pad.getch()

def get_date():
    return subprocess.run(["when", "d"], capture_output=True).stdout

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
    global items
    global line_numbers

    tmp = subprocess.run(["when", "--calendar=%s" % proxy_calendar, "--noheader", "--wrap=0"],
                         capture_output=True, text=True, check=True).stdout
    tmp = re.findall(r"^(.+)-(\d+)$", tmp, flags=re.MULTILINE)
    items = [x[0] for x in tmp]
    line_numbers = [int(x[1]) for x in tmp]

# An utility class for showing a browsable list

class List:
    def __init__(self, items, screen, minrow, mincol, maxrow, maxcol):
        self._items = items
        self._screen = screen
        self._minrow = minrow
        self._mincol = mincol
        self._height = maxrow - minrow + 1
        self._width = maxcol - mincol + 1
        self._first_item = 0
        self._selected_row = 0

    def show(self):
        for i, item in enumerate(self._items[self._first_item:]):
            if i >= self._height:
                break
            color = 2 if i == self._selected_row else 1
            self._screen.addstr(self._minrow + i, self._mincol, item[:self._width], curses.color_pair(color))

    def up(self):
        if self._selected_row > 0:
            self._selected_row -= 1
        elif self._first_item > 0:
            self._first_item -= 1

    def down(self):
        if self._selected_row < self._height - 1:
            self._selected_row += 1
        elif self._first_item + self._selected_row < len(self._items) - 1:
            self._first_item += 1

    def selected_item(self):
        return self._first_item + self._selected_row

    def selected_row(self):
        return self._selected_row

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
    get_items()

    # Create an onscreen list for showing the items
    item_list = List(items, stdscr, first_row, 0, last_row, width-1)

    # Initialize the selected action
    selected_action = 0

    # Main loop for handling key inputs
    while True:

        stdscr.clear()

        # Show the date at top of the screen

        stdscr.addstr(0, 0, get_date())

        # Draw the list of items
        if not items:
            stdscr.addstr(first_row, 0, "No items were found for today (and surrounding dates).")
        else:
            item_list.show()

        # Draw the menu of actions
        for i, action in enumerate(menu):
            color = 2 if i == selected_action else 1
            stdscr.addstr(menu_row, i * (width // len(menu)), action.name, curses.color_pair(color))

        stdscr.refresh()

        # Get the key input
        key = stdscr.getch()

        # Handle the cursor keys to navigate the list of items and the menu of actions
        if key == curses.KEY_UP:
            item_list.up()
        elif key == curses.KEY_DOWN:
            item_list.down()
        elif key == curses.KEY_LEFT and selected_action > 0:
            selected_action -= 1
        elif key == curses.KEY_RIGHT and selected_action < len(menu) - 1:
            selected_action += 1
        else:
            row = first_row + item_list.selected_row()
            selected_item = item_list.selected_item()
            if key == 10:
                expand_item(selected_item, row, 0, last_row, width-1)
            else:
                key = chr(key).lower()
                if key == "q":
                    break
                elif key == " ":
                    menu[selected_action].action(selected_item, row, 0, last_row, width-1)
                elif key in key_bindings:
                    key_bindings[key](selected_item, row, 0, last_row, width-1)

if __name__ == "__main__":
    try:
        initialize()
        generate_proxy_calendar()
        curses.wrapper(main)
    finally:
        cleanup()
