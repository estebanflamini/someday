#!/usr/bin/env python3

import curses
import subprocess
import textwrap
import os
import re
import sys
from collections import namedtuple
from shutil import copyfile
import argparse
import termios
import readline
import signal

# A class for interacting with the calendar

class Calendar:
    def __init__(self):
        try:
            with open("%s/.when/preferences" % os.environ["HOME"]) as f:
                prefs = f.read()
            m = re.match(r"^\s*calendar\s*=\s*(.+)$", prefs, flags=re.MULTILINE)
            self._calendar = m.group(1).strip()
        except Exception:
            sys.exit("No calendar configuration for 'when' was found.")

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

        d = ["when", "--calendar=%s" % self._proxy_calendar, "--noheader", "--wrap=0"]
        if args.past is not None:
            d.append("--past=%s" % args.past)
        if args.future is not None:
            d.append("--future=%s" % args.future)

        tmp = subprocess.run(d, capture_output=True, text=True, check=True).stdout
        if tmp.startswith("*"):
            raise Exception("Invalid expression in calendar.")
        tmp = re.findall(r"^(.+)-(\d+)$", tmp, flags=re.MULTILINE)
        self._items = [x[0] for x in tmp]
        self._line_numbers = [int(x[1]) for x in tmp]

    def no_items(self):
        return not self._items

    def get_items(self):
        return self._items

    def get_item(self, index):
        return self._items[index]

    # Update the true calendar

    def write_calendar(self):
        if self._modified:
            copyfile(self._calendar, self._calendar + ".SOMEDAY.BAK")
            with open(self._calendar, "w") as f:
                for line in self._calendar_lines:
                    print(line, file=f)

    # Utilities on calendar entries

    def _get_date_part(self, selected_item):
        line_number = self._line_numbers[selected_item]
        line = self._calendar_lines[line_number]
        m = re.match(r"^(.+?)\s*,", line)
        return m.group(1).lstrip() if m else None

    def _get_event_part(self, selected_item):
        line_number = self._line_numbers[selected_item]
        line = self._calendar_lines[line_number]
        m = re.search(r",\s*(.+?)$", line)
        return m.group(1).rstrip() if m else None

    def _is_exact_date(self, selected_item):
        date = self._get_date_part(selected_item)
        if date is None: # just in case
            return False
        if self._is_literal(date):
            return True
        tmp = self._parse_expression(date)
        if tmp is None:
            return False
        if len(tmp) == 3:
            if tmp[0] == "=":
                if tmp[1] == "j" and tmp[2].isdigit():
                    return True
                elif tmp[2] == "j" and tmp[1].isdigit():
                    return True
                else:
                    return False
        return False

    def _is_literal(self, text):
        # Actually, bogus strings such as 'bla bla bla' will pass this test,
        # but we can assume that any string which is passed to this method
        # comes from a valid calendar containing only valid day and month names
        if self._parse_expression(text) is not None:
            return False
        tmp = text.split()
        if len(tmp) != 3:
            return False
        return "*" not in tmp

    def _parse_expression(self, text):
        # Invalid expressions such as 'xx = #$$%' will get parsed by this
        # method, but we can assume that any string which is passed to this
        # method comes from a valid calendar containing only valid expressions
        text = text.strip()
        if not self._wellnested(text):
            return None
        if len(text) > 2 and text[0] == "(" and text[-1] == ")":
            return self._parse_expression(text[1:-1])
        # Parse operators in reversed order of precedence
        for op in ["|", "&", "!", "=", "!=", "<", ">", "<=", ">=", "-", "%"]:
            if op in text:
                if op == "!":
                    tmp = self._parse_expression(text[1:])
                    return [op, tmp] if tmp else None
                n = text.index(op)
                tmp1 = self._parse_expression(text[0:n])
                tmp2 = self._parse_expression(text[n+1:])
                if tmp1 and tmp2:
                    return [op, tmp1, tmp2]
        return text if not " " in text else None

    def _wellnested(self, text):
        # Some invalid expressions, such as ()j = 1, will pass this test, but
        # we can assume that any string which is passed to this method comes
        # from a valid calendar containing only valid expressions
        n = 0
        for ch in text:
            if n < 0:
                return False
            elif ch == "(":
                n += 1
            elif ch == ")":
                n -= 1
        return n == 0

    def _is_advanceable(self, selected_item):
        date = self._get_date_part(selected_item)
        if len(re.findall(r"\bj\b", date)) != 1:
            return False
        tmp = self._parse_expression(date)
        if tmp is None:
            return False
        return self._search_j(tmp)

    def _search_j(self, expr):
        if len(expr) == 3 and expr[0] == ">" and expr[1] == "j" and expr[2].isdigit():
            return True
        elif expr[0] == "&":
            return self._search_j(expr[1]) or self._search_j(expr[2])
        else:
            return False

    # Actions on the calendar

    def edit(self, screen, selected_item, minrow, mincol, maxrow, maxcol):
        line_number = self._line_numbers[selected_item]
        line = self._calendar_lines[line_number]
        coro = get_input_outside_curses(line)
        while True:
            _input = next(coro).strip()
            if _input == line:
                coro.close()
                break
            else:
                if self._update_calendar_line(line_number, _input):
                    break
                else:
                    print()
                    print("It looks you entered a wrong calendar line. Try it again. To leave the item unchanged, use the cursor up key to get the original line and press Enter.")
                    print()

    def delete(self, screen, selected_item, minrow, mincol, maxrow, maxcol):
        line_number = self._line_numbers[selected_item]
        self._update_calendar_line(line_number, None)

    def can_delete(self, selected_item):
        return self._is_exact_date(selected_item)

    def comment(self, screen, selected_item, minrow, mincol, maxrow, maxcol):
        line_number = self._line_numbers[selected_item]
        self._update_calendar_line(line_number, '#' + self._calendar_lines[line_number])

    def can_comment(self, selected_item):
        return self._is_exact_date(selected_item)

    def reschedule(self, screen, selected_item, minrow, mincol, maxrow, maxcol):
        line_number = self._line_numbers[selected_item]
        line = self._calendar_lines[line_number]
        what = self._get_event_part(selected_item)
        date = self._get_date_part(selected_item)
        coro = get_input_outside_curses()
        print("Enter a date as YYYY MM DD or a number to indicate an interval from now.")
        print()
        while True:
            print("Enter a blank line to leave the date unchanged.")
            print()
            print(what)
            print()
            _input = next(coro).strip()
            if not _input:
                break
            else:
                if _input.isdigit():
                    today = get_julian_date()
                    if not today:
                        print("Strangely, there was an error while trying to compute the modified julian date corresponding to today. Enter an exact date instead of an interval.")
                        continue
                    if args.useYMD:
                        try:
                            date = subprocess.run(["date", "--date", "%s days" % int(_input), "+%Y %m %d"], capture_output=True, text=True, check=True).stdout.strip()
                        except Exception:
                            print("There was an error while trying to compute the new date. Enter an exact date instead of an interval.")
                            continue
                    else:
                        date = "j=%s" % (today + int(_input))
                else:
                    date = _input
                if self._update_calendar_line(line_number, "%s , %s" % (date, what)):
                    break
                else:
                    print()
                    print("It looks you entered a wrong date. Try it again.")
                    print()
        coro.close()

    def can_reschedule(self, selected_item):
        return self._is_exact_date(selected_item)

    JULIAN_THRESHOLD = r"\bj\s*>\s*(\d+)\b"

    def advance(self, screen, selected_item, minrow, mincol, maxrow, maxcol):
        line_number = self._line_numbers[selected_item]
        line = self._calendar_lines[line_number]
        today = get_julian_date()
        self._update_calendar_line(line_number, re.sub(self.JULIAN_THRESHOLD, "j>%s" % today, line))

    DATE_IN_LISTING = r"^\S+\s+(\S+\s+\S+\s+\S+)"

    def can_advance(self, selected_item):
        if self._is_advanceable(selected_item):
            m = re.match(self.DATE_IN_LISTING, self._items[selected_item])
            if m is None:
                return False
            return get_julian_date(m.group(1)) <= get_julian_date()

    def _update_calendar_line(self, line_number, what):
        old_value = self._calendar_lines[line_number]
        if what is not None:
            self._calendar_lines[line_number] = what
        else:
            del self._calendar_lines[line_number]
        try:
            self.generate_proxy_calendar()
            self._modified = True
            return True
        except Exception:
            if what is not None:
                self._calendar_lines[line_number] = old_value
            else:
                self._calendar_lines.insert(line_number, old_value)
            return False

    URL = r"https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)"

    def open_url(self, screen, selected_item, minrow, mincol, maxrow, maxcol):
        m = re.search("(%s)" % self.URL, self._items[selected_item])
        if m is not None:
            url = m.group(1)
            subprocess.run(["xdg-open", url])

    def can_open_url(self, selected_item):
        return re.search("(%s)" % self.URL, self._items[selected_item]) is not None

def get_date():
    return subprocess.run(["when", "d"], capture_output=True, text=True).stdout.strip()

_julian_dates = {}

def get_julian_date(now=None):
    if now in _julian_dates:
        return _julian_dates[now]
    d = ["when", "j"]
    if now is not None:
        # The date is not surrounded by ', because no shell processing will be
        # done and we must pass the string as it will be received by when
        d.append("--now=%s" % now)
    tmp = subprocess.run(d, capture_output=True, text=True).stdout.strip()
    m = re.search(r"(\d{5})\.$", tmp)
    j = int(m.group(1)) if m else None
    _julian_dates[now] = j
    return j

# A class for browsing the calendar's items

class List:
    def __init__(self, calendar, screen, minrow, mincol, maxrow, maxcol):
        self._calendar = calendar
        self._screen = screen
        self._minrow = minrow
        self._mincol = mincol
        self._height = maxrow - minrow + 1
        self._width = maxcol - mincol + 1
        self._items = []
        self._first_item = 0
        self._selected_row = 0

    def show(self):
        self._items = calendar.get_items()
        if self._items:
            self._adjust_selected_item()
            for i, item in enumerate(self._items[self._first_item:]):
                if i >= self._height:
                    break
                color = 2 if i == self._selected_row else 1
                self._screen.addstr(self._minrow + i, self._mincol, item[:self._width], curses.color_pair(color))
        else:
            self._screen.addstr(self._minrow, self._mincol, "No items were found for today (and surrounding dates).")

    def _adjust_selected_item(self):
        while self._first_item + self._selected_row >= len(self._items):
            self.up()

    def up(self):
        if self._selected_row > 0:
            self._selected_row -= 1
        elif self._first_item > 0:
            self._first_item -= 1

    def down(self):
        if self._first_item + self._selected_row < len(self._items) - 1:
          if self._selected_row < self._height - 1:
            self._selected_row += 1
          else:
            self._first_item += 1

    def selected_item(self):
        self._adjust_selected_item()
        return self._first_item + self._selected_row

    def selected_row(self):
        self._adjust_selected_item()
        return self._selected_row

# A class for showing the menu and keeping track of available actions

Action = namedtuple("Action", ["key", "name", "action"])

class Menu:
    def __init__(self, calendar, item_list, screen, minrow, mincol, maxrow, maxcol):
        self._calendar = calendar
        self._item_list = item_list
        self._screen = screen
        self._minrow = minrow
        self._mincol = mincol
        self._maxrow = maxrow
        self._maxcol = maxcol
        self._height = maxrow - minrow + 1
        self._width = maxcol - mincol + 1
        self._menu = []
        self._key_bindings =  {}
        self._selected_action = 0

    def show(self):
        self._menu = []
        self._key_bindings = {}
        if self._calendar.get_items():
            selected_item = self._item_list.selected_item()
            self._menu.append(Action("e", "Edit", calendar.edit))
            if calendar.can_delete(selected_item):
                self._menu.append(Action("d", "Done (delete)", self._calendar.delete))
                self._key_bindings[curses.KEY_DC] = self._calendar.delete
            if calendar.can_reschedule(selected_item):
                self._menu.append(Action("r", "Reschedule", self._calendar.reschedule))
            if calendar.can_comment(selected_item):
                self._menu.append(Action("c", "Comment", self._calendar.comment))
            if calendar.can_advance(selected_item):
                self._menu.append(Action("a", "Advance", self._calendar.advance))
            if calendar.can_open_url(selected_item):
                self._menu.append(Action("b", "Browse url", self._calendar.open_url))
            self._key_bindings |= {ord(x.key.lower()): x.action for x in self._menu}
            self._key_bindings |= {ord(x.key.upper()): x.action for x in self._menu}
        else:
            self._menu = []
            self._key_bindings = {}
        for i, action in enumerate(self._menu):
            color = 2 if i == self._selected_action else 1
            self._screen.addstr(self._minrow, i * (self._width // len(self._menu)), action.name, curses.color_pair(color))

    def dispatch_key(self, key, selected_item, minrow, mincol, maxrow, maxcol):
        if not calendar.get_items():
            return
        elif key == 32:
            action = self._menu[self._selected_action].action
        elif key in self._key_bindings:
            action = self._key_bindings[key]
        else:
            return

        action(self._screen, selected_item, minrow, mincol, maxrow, maxcol)

    def left(self):
        if self._selected_action > 0:
            self._selected_action -= 1

    def right(self):
        if self._selected_action < len(self._menu) - 1:
            self._selected_action += 1

def get_args():
    parser = argparse.ArgumentParser(prog="someday")
    parser.add_argument("--past", type=int, default=None)
    parser.add_argument("--future", type=int, default=None)
    parser.add_argument("--useYMD", action='store_true', default=False)
    return parser.parse_args()

def expand(item, minrow, mincol, maxrow, maxcol):
    minrow -= 1
    width = maxcol - mincol + 1
    lines = textwrap.wrap(item, width-2)
    height = len(lines) + 2
    pad = curses.newpad(height, width)
    pad.border()
    for i, line in enumerate(lines):
        pad.addstr(i+1, 1, line)
    minrow = min(minrow, maxrow - height + 1)
    pad.refresh(0, 0, minrow, mincol, maxrow, maxcol)
    pad.getch()

def get_input_outside_curses(line=None):
    gen = _get_input_outside_curses(line)
    next(gen)
    return gen

def _get_input_outside_curses(line=None):
    screen.clear()
    screen.refresh()
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, _shell_tty_settings)
    curses.curs_set(_shell_cursor)
    old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    readline.clear_history()
    if line is not None:
        readline.add_history(line)
    _input = line

    yield

    try:
        while True:
            if _input is not None:
                readline.set_startup_hook(lambda: readline.insert_text(_input))
            _input = input()
            readline.set_startup_hook()
            yield _input
    finally:
        signal.signal(signal.SIGINT, old_handler)
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, _prog_tty_settings)
        curses.curs_set(0)

# This is the main function for browsing and updating the list of items

def main(stdscr, calendar):
    global _prog_tty_settings
    global _shell_cursor
    global screen

    screen = stdscr
    # Initialize curses
    _shell_cursor = curses.curs_set(0)

    curses.use_default_colors()
    curses.init_pair(1, -1, -1)
    curses.init_pair(2, curses.COLOR_RED, curses.COLOR_WHITE)

    # Have curses interpret special input
    stdscr.keypad(True)

    _prog_tty_settings = termios.tcgetattr(sys.stdin.fileno())

    # Get the size of the window and define areas for the list of items and menu
    height, width = stdscr.getmaxyx()
    first_row = 2
    last_row = height - 3
    menu_row = height - 1

    # Create an onscreen list for showing the items
    item_list = List(calendar, stdscr, first_row, 0, last_row, width-1)

    # Generate the menu and key bindings
    menu = Menu(calendar, item_list, stdscr, menu_row, 0, menu_row, width-1)

    # Main loop for handling key inputs
    while True:

        stdscr.clear()

        # Show the date at top of the screen

        stdscr.addstr(0, 0, "%s - Julian date: %s" % (get_date(), get_julian_date()))

        # Draw the list of items
        item_list.show()

        # Draw the menu of actions
        menu.show()

        stdscr.refresh()

        # Get the key input
        key = stdscr.getch()

        # Handle the cursor keys to navigate the list of items and the menu of actions
        if key == curses.KEY_UP:
            item_list.up()
        elif key == curses.KEY_DOWN:
            item_list.down()
        elif key == curses.KEY_LEFT:
            menu.left()
        elif key == curses.KEY_RIGHT:
            menu.right()
        elif chr(key).lower() == 'q':
            break
        else:
            selected_item = item_list.selected_item()
            item = calendar.get_item(selected_item)
            row = first_row + item_list.selected_row()
            if key == 10:
                expand(item, row, 0, last_row, width-1)
            else:
                menu.dispatch_key(key, selected_item, row, 0, last_row, width-1)

if __name__ == "__main__":
    args = get_args()
    calendar = Calendar()
    # The following line will call sys.exit(...) if the proxy calendar already existed. That is why it goes uncatched, so we don't cleanup the calendar if we didn't create it
    calendar.check_no_proxy_calendar_exists()
    _shell_tty_settings = termios.tcgetattr(sys.stdin.fileno())
    try:
        calendar.generate_proxy_calendar()
        curses.wrapper(main, calendar)
        calendar.write_calendar()
    finally:
        calendar.cleanup_proxy_calendar()
