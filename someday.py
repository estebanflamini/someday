#!/usr/bin/env python3

import curses
import subprocess
import textwrap
import os
import re
import sys
from collections import namedtuple
from shutil import copyfile

# A singleton for interacting with the calendar

class Calendar:
    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(Calendar, cls).__new__(cls)
            cls.instance._initialize()
        return cls.instance

    def _initialize(self):
        with open("%s/.when/preferences" % os.environ["HOME"]) as f:
            prefs = f.read()
        m = re.match(r"^\s*calendar\s*=\s*(.+)$", prefs, flags=re.MULTILINE)
        if m is not None:
            self._calendar = m.group(1).strip()
        else:
            sys.exit("No calendar configuration for 'when' was found.")

# TODO: Eliminar esta línea cuando ya estés seguro de que anda bien
        self._calendar = "%s/prog/someday/calendar" % os.environ["HOME"]

        self._proxy_calendar = self._calendar + ".SOMEDAY"

        with open(self._calendar) as infile:
            self._calendar_lines = infile.read().splitlines()

        self._line_numbers = []
        self._modified = False

    def check_no_proxy_calendar_exists(self):
        if os.path.exists(self._proxy_calendar):
            sys.exit("The calendar seems to be in edition. Delete the file %s and try again." % self._proxy_calendar)

    def cleanup_proxy_calendar(self):
        os.unlink(self._proxy_calendar)

    # Copy the when's calendary to a temporary file where each non-empty line is line-numbered, and get upcoming items from there

    def generate_proxy_calendar(self):
        i = 0
        with open(self._proxy_calendar, "w") as outfile:
            for line in self._calendar_lines:
                tmp_line = "%s-%s" % (line, i) if line.strip() else line
                print(tmp_line, file=outfile)
                i += 1

        tmp = subprocess.run(["when", "--calendar=%s" % self._proxy_calendar, "--noheader", "--wrap=0"],
                             capture_output=True, text=True, check=True).stdout
        tmp = re.findall(r"^(.+)-(\d+)$", tmp, flags=re.MULTILINE)
        self._items = [x[0] for x in tmp]
        self._line_numbers = [int(x[1]) for x in tmp]

    def no_items(self):
        return not self._items

    def get_items(self):
        return self._items

    # Update the true calendar

    def write_calendar(self):
        if self._modified:
            copyfile(self._calendar, self._calendar + ".SOMEDAY.BAK")
            with open(self._calendar, "w") as f:
                for line in self._calendar_lines:
                    print(line, file=f)

    # Actions on the calendar

    def erase(self, selected_item, minrow, mincol, maxrow, maxcol):
        line_number = self._line_numbers[selected_item]
        del self._calendar_lines[line_number]
        self.generate_proxy_calendar()
        self._modified = True

Action = namedtuple("Action", ["key", "name", "action"])

def generate_menu(calendar):
    menu = [Action("e", "Erase", calendar.erase)]
    key_bindings = {x.key: x.action for x in menu}
    key_bindings[curses.KEY_DC] = calendar.erase
    return menu, key_bindings

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

# An utility class for browsing the calendar's items

class List:
    def __init__(self, calendar, screen, minrow, mincol, maxrow, maxcol):
        self._calendar = calendar
        self._screen = screen
        self._minrow = minrow
        self._mincol = mincol
        self._height = maxrow - minrow + 1
        self._width = maxcol - mincol + 1
        self._first_item = 0
        self._selected_row = 0

    def show(self):
        for i, item in enumerate(calendar.get_items()[self._first_item:]):
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

def main(stdscr, calendar):

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

    # Create an onscreen list for showing the items
    item_list = List(calendar, stdscr, first_row, 0, last_row, width-1)

    # Generate the menu and key bindings
    menu, key_bindings = generate_menu(calendar)

    # Initialize the selected action
    selected_action = 0

    # Main loop for handling key inputs
    while True:

        stdscr.clear()

        # Show the date at top of the screen

        stdscr.addstr(0, 0, get_date())

        # Draw the list of items
        if calendar.no_items():
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
            elif key in key_bindings:
                key_bindings[key](selected_item, row, 0, last_row, width-1)
            elif key in key_bindings:
                key_bindings[key](selected_item, row, 0, last_row, width-1)
            else:
                key = chr(key).lower()
                if key == "q":
                    break
                elif key == " ":
                    menu[selected_action].action(selected_item, row, 0, last_row, width-1)
                elif key in key_bindings:
                    key_bindings[key](selected_item, row, 0, last_row, width-1)

if __name__ == "__main__":
    calendar = Calendar()
    # The following line will call sys.exit(...) if the proxy calendar already existed. That is why it goes uncatched, so we don't cleanup the calendar if we didn't create it
    calendar.check_no_proxy_calendar_exists()
    # Okay, proceed to create proxy calendar (also uncatched, so if something goes wrong while creating the proxy calendar, we don't try to delete it
    calendar.generate_proxy_calendar()
    try:
        curses.wrapper(main, calendar)
        calendar.write_calendar()
    finally:
        calendar.cleanup_proxy_calendar()
