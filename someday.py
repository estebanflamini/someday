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
    parser.add_argument("--search", type=str, default=None)
    parser.add_argument("--useYMD", action='store_true', default=False)
    parser.add_argument("--diff", action='store_true', default=False)
    return parser.parse_args()

# A class for interacting with the calendar

View = namedtuple("View", ["past", "future", "search"])

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

        self._view_mode = View(None, None, None)

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

    def set_view_mode(self, mode):
        self._view_mode = mode

    # Copy the when's calendary to a temporary file where each non-empty line is line-numbered, and get upcoming items from there

    def generate_proxy_calendar(self):
        i = 0
        with open(self._proxy_calendar, "w") as outfile:
            for line in self._calendar_lines:
                tmp_line = "%s-%s" % (line, i) if line.strip() else line
                print(tmp_line, file=outfile)
                i += 1

        d = ["when", "--calendar=%s" % self._proxy_calendar, "--noheader", "--wrap=0"]

        if self._view_mode.past is not None:
            d.append("--past=%s" % self._view_mode.past)
        if self._view_mode.future is not None:
            d.append("--future=%s" % self._view_mode.future)

        tmp = subprocess.run(d, capture_output=True, text=True, check=True).stdout
        if tmp.startswith("*"):
            raise Exception("Invalid expression in calendar.")
        if args.search is not None:
            tmp = tmp.splitlines()
            tmp = list(filter(lambda x: self._search(x, args.search), tmp))
            tmp = "\n".join(tmp)
        tmp = re.findall(r"^(.+)-(\d+)$", tmp, flags=re.MULTILINE)
        self._items = [x[0] for x in tmp]
        self._line_numbers = [int(x[1]) for x in tmp]

    def _search(self, item, text):
        try:
            m = re.match(r"^\s*(?:\S+\s+){4}(.+?)-\d+$", item)
            return text in m.group(1)
        except:
            sys.exit("Internal error: could not process the output of when")

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
        what = str(what).strip()
        if not what:
            return self.delete_source_line(index)
        line_number = self._line_numbers[index]
        old_value = self._calendar_lines[line_number]
        self._calendar_lines[line_number] = what
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
        self._adjust_selected_item()
        if self._items:
            for i, item in enumerate(self._items[self._first_item:]):
                if i >= self._height:
                    break
                color = 2 if i == self._selected_row else 1
                screen.addstr(minrow + i, mincol, item[:width], curses.color_pair(color))
        else:
            screen.addstr(minrow, mincol, "No items were found for the specified dates.")

    def _adjust_selected_item(self):
        if self._items:
            while self._first_item + self._selected_row >= len(self._items):
                self.up()
        else:
            self.top()

    def top(self):
        self._selected_row = 0
        self._first_item = 0

    def up(self):
        if not self._items:
            return
        elif self._selected_row > 0:
            self._selected_row -= 1
        elif self._first_item > 0:
            self._first_item -= 1

    def down(self):
        if not self._items:
            return
        elif self._first_item + self._selected_row < len(self._items) - 1:
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
    def __init__(self):
        self._menu = []
        self._key_bindings =  {}
        self._selected_index = None
        self._selected_menu_item = None

    # Delete the menu, but keeps the last defined _selected_menu_item, to be
    # used by _adjust_selected_item(), below.
    def clear(self):
        self._menu = []
        self._key_bindings =  {}
        self._selected_index = None

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
        if not self._menu:
            return
        self._adjust_selected_item()
        width = maxcol - mincol + 1
        for i, item in enumerate(self._menu):
            color = 2 if self._selected_index == i else 1
            screen.addstr(minrow, i * (width // len(self._menu)), item.name, curses.color_pair(color))

    # This method seeks compliance with the 'principle of least surprise':
    # when the menu is recreated by recreate_menu() below, as a result of
    # selecting another calendar entry, we try to maintain the selection on the
    # same menu entry that was selected before (as identified by name), if it
    # still exists; otherwise, we reset the pointer to the first menu entry.
    def _adjust_selected_item(self):
        self._selected_index = 0
        if self._selected_menu_item:
            for i, item in enumerate(self._menu):
                if item.name == self._selected_menu_item.name:
                    self._selected_index = i
                    return
        self._selected_menu_item = self._menu[self._selected_index]

    def get_action(self, key):
        if key == 32:
            return self._selected_menu_item.action
        elif key in self._key_bindings:
            return self._key_bindings[key]
        else:
            return None

    def left(self):
        if self._selected_index > 0:
            self._selected_index -= 1
        self._selected_menu_item = self._menu[self._selected_index]

    def right(self):
        if self._selected_index < len(self._menu) - 1:
            self._selected_index += 1
        self._selected_menu_item = self._menu[self._selected_index]

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

# An utility function that extends input() to allow passing an initial value to
# be edited
def my_input(value_to_edit=None):
    if value_to_edit is not None:
        readline.set_startup_hook(lambda: readline.insert_text(value_to_edit))
        n = readline.get_current_history_length()
        if n == 0 or readline.get_history_item(n) != value_to_edit:
            # This is to ensure the initial value is in the history
            readline.add_history(value_to_edit)
        else:
            # This is to avoid having to press the up key twice to get to the
            # previous (different) value in history
            readline.remove_history_item(n-1)
    try:
        return input().strip()
    except KeyboardInterrupt:
        return None
    finally:
        if value_to_edit is not None:
            readline.set_startup_hook()
        print()

# A decorator for functions that need to run outside curses
def outside_curses(func):
    def wrapped(*args, **kwargs):
        screen.clear()
        screen.refresh()
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, _shell_tty_settings)
        curses.curs_set(_shell_cursor)
        try:
            return func(*args, **kwargs)
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, _prog_tty_settings)
            curses.curs_set(0)
    return wrapped

# Actions on the calendar

@outside_curses
def edit(calendar, selected_item):
    line = calendar.get_source_line(selected_item)
    _input = line
    while True:
        _input = my_input(_input)
        if _input is None or _input == line:
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

@outside_curses
def reschedule(calendar, selected_item):
    what = calendar.get_event(selected_item)
    date = calendar.get_date_expression(selected_item)
    _input = None
    while True:
        say("Enter a date as YYYY MM DD or a number (negative, zero, or positive) to indicate that many days from now.")
        say("Enter a blank line to leave the date unchanged.")
        say(what)
        _input = my_input(_input)
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
YEARLY_THRESHOLD = r"\by\s*>\s*(\d+)\b"
DATE_IN_LISTING = r"^\S+\s+(\S+\s+\S+\s+\S+)"
YEAR_IN_LISTING = r"^\S+\s+(\d+)"

def advance(calendar, selected_item):
    item = calendar.get_item(selected_item)
    date = calendar.get_date_expression(selected_item)
    variable_to_replace = _variable_to_replace(date)
    if variable_to_replace is None:
        return
    line = calendar.get_source_line(selected_item)
    try:
        if variable_to_replace == "j":
            m = re.match(DATE_IN_LISTING, item)
            regex = JULIAN_THRESHOLD
            repl = get_julian_date(m.group(1))
        else:
            m = re.match(YEAR_IN_LISTING, item)
            regex = YEARLY_THRESHOLD
            repl = m.group(1)
    except Exception:
        screen.clear()
        screen.refresh()
        say("There has been an error while trying to calculate the advanced date.")
        screen.getch()
        return
    calendar.update_source_line(selected_item, re.sub(regex, "%s>%s" % (variable_to_replace, repl), line))

def can_advance(calendar, selected_item):
    date = calendar.get_date_expression(selected_item)
    variable_to_replace = _variable_to_replace(date)
    if variable_to_replace is None:
        return False
    tmp = calendar.parse_expression(date)
    if tmp is None:
        return False
    return _search_var(tmp, variable_to_replace)

def _search_var(expr, var):
    if len(expr) == 3 and expr[0] == ">" and expr[1] == var and expr[2].isdigit():
        return True
    elif expr[0] == "&":
        return _search_var(expr[1], var) or _search_var(expr[2], var)
    else:
        return False

def _has_julian_threshold(date):
    return len(re.findall(JULIAN_THRESHOLD, date)) == 1

def _has_yearly_threshold(date):
    return len(re.findall(YEARLY_THRESHOLD, date)) == 1

def _variable_to_replace(date):
    if _has_julian_threshold(date):
        return "j"
    elif _has_yearly_threshold(date):
        return "y"
    else:
        return None

def duplicate(calendar, selected_item):
    date = calendar.get_date_expression(selected_item).strip()
    what = calendar.get_event(selected_item).strip()
    new_line = "%s , [+] %s" % (date, what)
    calendar.add_source_line(new_line)

@outside_curses
def new():
    say("What?:")
    what = my_input()
    _input = None
    while what:
        say("When? (Enter a date as YYYY MM DD, a number (negative, zero, or positive) to indicate that many days from now or a valid when\'s expression:")
        _input = my_input(_input)
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

URL = r"https?:\/\/(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)"

def open_url(calendar, selected_item):
    urls = re.findall(URL, calendar.get_item(selected_item))
    for url in urls:
        subprocess.run(["xdg-open", url])
        sleep(1)

def can_open_url(calendar, selected_item):
    return re.search(URL, calendar.get_item(selected_item)) is not None

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

def choose_view_mode(calendar, item_list):
    modes = []
    modes.append(("Use when’s defaults", lambda: View(None, None, None)))
    modes.append(("Enter a date range", lambda: enter_date_range()))
    _args = "%s %s %s" % ("--past=%s " % args.past if args.past else "", "--future=%s " % args.future if args.future else "", "")
    _args = _args.strip()
    if _args:
        modes.append(("Use given arguments: %s" % _args, lambda: View(args.past, args.future, None)))
    screen.clear()
    screen.refresh()
    screen.addstr(0, 0, "Choose a view mode:")
    i = 0
    row = 2
    for mode in modes:
        screen.addstr(row, 0, "%s: %s" % (i+1, modes[i][0]))
        i += 1
        row += 1
    screen.addstr(row, 0, "q: Back")
    while True:
        key = screen.getkey()
        if key.lower() == "q":
            break
        elif not key.isdigit():
            continue
        i = int(key) - 1
        if i < len(modes):
            mode = modes[i][1]()
            if mode is not None:
                calendar.set_view_mode(mode)
                calendar.generate_proxy_calendar()
                item_list.top()
            break

@outside_curses
def enter_date_range():
    say("From date:")
    _from = my_input()
    if not _from:
        return None
    say("To date:")
    _to = my_input()
    if not _to:
        return None
    return View(_from, _to, None)

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
    menu.add(Action("v", "View", choose_view_mode))

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

    # Create an object to store the menu and key bindings
    menu = Menu()

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
            action = expand if key == 10 else menu.get_action(key)
            if action is None:
                pass
            elif action is choose_view_mode:
                 action(calendar, item_list)
            elif action is new:
                action()
            elif calendar.get_items():
                selected_item = item_list.selected_item()
                item = calendar.get_item(selected_item)
                row = first_row + item_list.selected_row()
                if action is expand:
                    action(item, row, 0, last_row, width-1)
                else:
                    action(calendar, selected_item)

if __name__ == "__main__":
    args = get_args()
    calendar = Calendar()
    # The following line will call sys.exit(...) if the proxy calendar already existed. That is why it goes uncatched, so we don't cleanup the calendar if we didn't create it
    calendar.check_no_proxy_calendar_exists()
    calendar.set_view_mode(View(args.past, args.future, args.search))
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
