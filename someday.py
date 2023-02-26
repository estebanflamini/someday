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
from time import sleep

# These globals will be populated and used below
_prog_tty_settings = None
_shell_tty_settings = None
_shell_cursor = None
screen = None
args = None

def get_args():
    parser = argparse.ArgumentParser(prog="someday")
    parser.add_argument("--calendar", type=str, default=None)
    parser.add_argument("--past", type=int, default=None)
    parser.add_argument("--future", type=int, default=None)
    parser.add_argument("--useYMD", action='store_true', default=False)
    parser.add_argument("--diff", action='store_true', default=False)
    return parser.parse_args()

# A class for interacting with the calendar

class Calendar:
    def __init__(self):
        self._calendar = args.calendar or self._get_default_calendar()
        self._proxy_calendar = self._calendar + ".SOMEDAY"
        self._backup_calendar = self._calendar + ".SOMEDAY.BAK"

        with open(self._calendar) as infile:
            self._calendar_lines = infile.read().splitlines()

        self._line_numbers = []
        self._modified = False
        self._created_backup = False

    def _get_default_calendar(self):
        try:
            with open("%s/.when/preferences" % os.environ["HOME"]) as f:
                prefs = f.read()
            m = re.match(r"^\s*calendar\s*=\s*(.+)$", prefs, flags=re.MULTILINE)
            return m.group(1).strip()
        except Exception:
            sys.exit("No calendar configuration for 'when' was found.")

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

    def get_items(self):
        return self._items

    def get_item(self, index):
        return self._items[index]

    def get_source_line(self, index):
        line_number = self._line_numbers[index]
        return self._calendar_lines[line_number]

    # Update the true calendar

    def write_calendar(self):
        if self._modified:
            copyfile(self._calendar, self._backup_calendar)
            self._created_backup = True
            with open(self._calendar, "w") as f:
                for line in self._calendar_lines:
                    print(line, file=f)

    # Show differences between the calendar and the generated backup
    def diff(self):
        if self._created_backup:
            _diff = subprocess.run(["diff", self._calendar, self._backup_calendar], capture_output=True, text=True).stdout
            if _diff:
                subprocess.run(["less", "-F"], input=_diff, text=True)

    # Utilities on calendar entries

    def get_date_expression(self, index):
        line = self.get_source_line(index)
        m = re.match(r"^(.+?)\s*,", line)
        return m.group(1).lstrip() if m else None

    def get_event(self, index):
        line = self.get_source_line(index)
        m = re.search(r",\s*(.+?)$", line)
        return m.group(1).rstrip() if m else None

    def happens_only_once(self, index):
        date = self.get_date_expression(index)
        if date is None: # just in case
            return False
        if self._is_literal(date):
            return True
        tmp = self.parse_expression(date)
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
        if self.parse_expression(text) is not None:
            return False
        tmp = text.split()
        if len(tmp) != 3:
            return False
        return "*" not in tmp

    def parse_expression(self, text):
        # Invalid expressions such as 'xx = #$$%' will get parsed by this
        # method, but we can assume that any string which is passed to this
        # method comes from a valid calendar containing only valid expressions
        text = text.strip()
        if not self._wellnested(text):
            return None
        if len(text) > 2 and text[0] == "(" and text[-1] == ")":
            return self.parse_expression(text[1:-1])
        # Parse operators in reversed order of precedence
        for op in ["|", "&", "!", "=", "!=", "<", ">", "<=", ">=", "-", "%"]:
            if op in text:
                if op == "!":
                    tmp = self.parse_expression(text[1:])
                    return [op, tmp] if tmp else None
                n = text.index(op)
                tmp1 = self.parse_expression(text[0:n])
                tmp2 = self.parse_expression(text[n+1:])
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

    def update_source_line(self, index, what):
        line_number = self._line_numbers[index]
        old_value = self._calendar_lines[line_number]
        self._calendar_lines[line_number] = str(what).strip()
        try:
            self.generate_proxy_calendar()
            self._modified = True
            return True
        except Exception:
            self._calendar_lines[line_number] = old_value
            return False

    def delete_source_line(self, index):
        line_number = self._line_numbers[index]
        old_value = self._calendar_lines[line_number]
        del self._calendar_lines[line_number]
        try:
            self.generate_proxy_calendar()
            self._modified = True
            return True
        except Exception: # This should never happen, but just in case...
            self._calendar_lines.insert(line_number, old_value)
            return False

    def add_source_line(self, what):
        self._calendar_lines.append(what)
        try:
            self.generate_proxy_calendar()
            self._modified = True
            return True
        except Exception:
            del self._calendar_lines[-1]
            return False

# A class for browsing the calendar's items

class List:
    def __init__(self, calendar):
        self._calendar = calendar
        self._items = []
        self._first_item = 0
        self._selected_row = 0

    def show(self, screen, minrow, mincol, maxrow, maxcol):
        self._items = calendar.get_items()
        self._height = maxrow - minrow + 1
        width = maxcol - mincol + 1
        if self._items:
            self._adjust_selected_item()
            for i, item in enumerate(self._items[self._first_item:]):
                if i >= self._height:
                    break
                color = 2 if i == self._selected_row else 1
                screen.addstr(minrow + i, mincol, item[:width], curses.color_pair(color))
        else:
            screen.addstr(minrow, mincol, "No items were found for the specified dates.")

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
    def __init__(self, calendar, item_list):
        self._calendar = calendar
        self._item_list = item_list
        self._menu = []
        self._key_bindings =  {}
        self._selected_action = 0

    def clear(self):
        self._menu = []
        self._key_bindings = {}

    def add(self, what):
        self._menu.append(what)
        keys = what.key if isinstance(what.key, list) else [what.key]
        for key in keys:
            if isinstance(key, str):
                self._key_bindings[ord(key.lower())] = what.action
                self._key_bindings[ord(key.upper())] = what.action
            else:
                self._key_bindings[key] = what.action

    def show(self, screen, minrow, mincol, maxrow, maxcol):
        width = maxcol - mincol + 1
        for i, action in enumerate(self._menu):
            color = 2 if i == self._selected_action else 1
            screen.addstr(minrow, i * (width // len(self._menu)), action.name, curses.color_pair(color))

    def get_action(self, key):
        if not self._calendar.get_items():
            return None
        elif key == 32:
            return self._menu[self._selected_action].action
        elif key in self._key_bindings:
            return self._key_bindings[key]
        else:
            return None

    def left(self):
        if self._selected_action > 0:
            self._selected_action -= 1

    def right(self):
        if self._selected_action < len(self._menu) - 1:
            self._selected_action += 1

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
    tmp = subprocess.run(d, capture_output=True, text=True, check=True).stdout.strip()
    m = re.search(r"(\d{5})\.$", tmp)
    j = int(m.group(1)) if m else None
    _julian_dates[now] = j
    return j

def say(what):
    width = os.get_terminal_size()[0]
    what = textwrap.wrap(what, width)
    for line in what:
        print(line)
    print()

def my_input(value_to_edit=None):
    if value_to_edit is not None:
        readline.set_startup_hook(lambda: readline.insert_text(value_to_edit))
    r = input()
    if value_to_edit is not None:
        readline.set_startup_hook()
    print()
    return r

# Actions on the calendar

def edit(calendar, selected_item):
    line = calendar.get_source_line(selected_item)
    coro = get_input_outside_curses(line)
    while True:
        _input = next(coro).strip()
        if _input == line:
            coro.close()
            break
        else:
            if calendar.update_source_line(selected_item, _input):
                break
            else:
                say("It looks you entered a wrong calendar line. Try it again. To leave the item unchanged, use the cursor up key to get the original line and press Enter.")

def delete(calendar, selected_item):
    calendar.delete_source_line(selected_item)

def can_delete(calendar, selected_item):
    return calendar.happens_only_once(selected_item)

def comment(calendar, selected_item):
    calendar.update_source_line(selected_item, '#' + calendar.get_source_line(selected_item))

def can_comment(calendar, selected_item):
    return calendar.happens_only_once(selected_item)

def reschedule(calendar, selected_item):
    what = calendar.get_event(selected_item)
    date = calendar.get_date_expression(selected_item)
    coro = get_input_outside_curses()
    say("Enter a date as YYYY MM DD or a number (negative, zero, or positive) to indicate that many days from now.")
    while True:
        say("Enter a blank line to leave the date unchanged.")
        say(what)
        _input = next(coro).strip()
        if not _input:
            break
        else:
            if is_interval(_input):
                date = get_interval(_input)
                if date is None:
                    continue
            else:
                date = _input
            if calendar.update_source_line(selected_item, "%s , %s" % (date, what)):
                break
            else:
                say("It looks you entered a wrong date/interval. Try it again.")
    coro.close()

def can_reschedule(calendar, selected_item):
    return calendar.happens_only_once(selected_item)

def is_interval(text):
    text = text.strip()
    return text.isdigit() or text.startswith("-") and text[1:].isdigit()

def get_interval(text):
    try:
        today = get_julian_date()
    except Exception:
        say("Strangely, there was an error while trying to compute the modified julian date corresponding to today. Enter an exact date instead of an interval.")
        return None
    delta = int(text)
    if args.useYMD:
        try:
            return subprocess.run(["date", "--date", "%s days" % int(text), "+%Y %m %d"], capture_output=True, text=True, check=True).stdout.strip()
        except Exception:
            say("There was an error while trying to compute the new date. Enter an exact date instead of an interval.")
            return None
    else:
        return "j=%s" % (today + delta)

JULIAN_THRESHOLD = r"\bj\s*>\s*(\d+)\b"
DATE_IN_LISTING = r"^\S+\s+(\S+\s+\S+\s+\S+)"

def advance(calendar, selected_item):
    line = calendar.get_source_line(selected_item)
    m = re.match(DATE_IN_LISTING, calendar.get_item(selected_item))
    if m is None: # Just in case
        return
    try:
        date = get_julian_date(m.group(1))
    except Exception:
        screen.clear()
        screen.refresh()
        say("There has been an error while trying to calculate the advanced date.")
        screen.getch()
        return
    calendar.update_source_line(selected_item, re.sub(JULIAN_THRESHOLD, "j>%s" % date, line))

def can_advance(calendar, selected_item):
    date = calendar.get_date_expression(selected_item)
    if len(re.findall(JULIAN_THRESHOLD, date)) != 1:
        return False
    tmp = calendar.parse_expression(date)
    if tmp is None:
        return False
    return _search_j(tmp)

def _search_j(expr):
    if len(expr) == 3 and expr[0] == ">" and expr[1] == "j" and expr[2].isdigit():
        return True
    elif expr[0] == "&":
        return _search_j(expr[1]) or _search_j(expr[2])
    else:
        return False

def duplicate(calendar, selected_item):
    date = calendar.get_date_expression(selected_item).strip()
    what = calendar.get_event(selected_item).strip()
    new_line = "%s , [+] %s" % (date, what)
    calendar.add_source_line(new_line)

def new(calendar, selected_item):
    coro = get_input_outside_curses()
    say("What?:")
    what = next(coro).strip()
    coro.close()
    coro = get_input_outside_curses(clear_screen=False)
    while what:
        say("When? (Enter a date as YYYY MM DD, a number (negative, zero, or positive) to indicate that many days from now or a valid when\'s expression:")
        _input = next(coro).strip()
        if not _input:
            break
        else:
            date = None
            if is_interval(_input):
                date = get_interval(_input)
            elif calendar.parse_expression(_input):
                date = _input
            else:
                try:
                    get_julian_date(_input)
                    date = _input
                except Exception:
                    pass
            if date and calendar.add_source_line("%s , %s" % (date, what)):
                break
            else:
                say("It looks you entered a wrong date/interval/expression (or there was an error while trying to calculate the corresponding julian date). Try it again.")
    coro.close()

URL = r"https?:\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)"

def open_url(calendar, selected_item):
    urls = re.findall(URL, calendar.get_item(selected_item))
    for url in urls:
        subprocess.run(["xdg-open", url])
        sleep(1)

def can_open_url(calendar, selected_item):
    return re.search(URL, calendar.get_item(selected_item)) is not None

def get_input_outside_curses(line=None, clear_screen=True):
    gen = _get_input_outside_curses(line, clear_screen)
    next(gen)
    return gen

def _get_input_outside_curses(line=None, clear_screen=True):
    if clear_screen:
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
            _input = my_input()
            readline.set_startup_hook()
            yield _input
    finally:
        signal.signal(signal.SIGINT, old_handler)
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, _prog_tty_settings)
        curses.curs_set(0)

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

def recreate_menu(menu, calendar, item_list):
    menu.clear()
    if calendar.get_items():
        selected_item = item_list.selected_item()
        menu.add(Action("e", "Edit", edit))
        if can_delete(calendar, selected_item):
            menu.add(Action(["d", curses.KEY_DC], "Done (delete)", delete))
        if can_reschedule(calendar, selected_item):
            menu.add(Action("r", "Reschedule", reschedule))
        if can_comment(calendar, selected_item):
            menu.add(Action("c", "Comment", comment))
        if can_advance(calendar, selected_item):
            menu.add(Action("a", "Advance", advance))
        if can_open_url(calendar, selected_item):
            menu.add(Action("b", "Browse url", open_url))
        menu.add(Action("u", "dUplicate", duplicate))
    menu.add(Action("n", "New", new))

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

    # Create an onscreen list for showing the items
    item_list = List(calendar)

    # Generate the menu and key bindings
    menu = Menu(calendar, item_list)

    try:
        julian_date = get_julian_date()
    except Exception:
        julian_date = "Unable to determine."

    # Main loop for handling key inputs
    while True:

        stdscr.clear()

        # Get the size of the window and define areas for the list of items and menu
        width, height = os.get_terminal_size()
        first_row = 2
        last_row = height - 3
        menu_row = height - 1

        # Show the date at top of the screen

        stdscr.addstr(0, 0, "%s - Julian date: %s" % (get_date(), julian_date))

        # Draw the list of items
        item_list.show(stdscr, first_row, 0, last_row, width-1)

        # Update and draw the menu of actions
        recreate_menu(menu, calendar, item_list)
        menu.show(stdscr, menu_row, 0, menu_row, width-1)

        stdscr.refresh()

        # Get the key input
        key = stdscr.getch()

        # Handle the cursor keys to navigate the list of items and the menu of actions
        if key < 0:
            pass
        elif key == curses.KEY_UP:
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
                action = menu.get_action(key)
                if action is not None:
                    action(calendar, selected_item)

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
    except KeyboardInterrupt:
        print("Exiting without changes.")
    finally:
        calendar.cleanup_proxy_calendar()
        if args.diff:
            calendar.diff()
