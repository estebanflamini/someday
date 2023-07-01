#!/usr/bin/env python3

import os
import re
import sys
import shlex
import curses
import gettext
import termios
import textwrap
import argparse
import readline
import subprocess
from time import sleep
from shutil import copyfile
from collections import namedtuple

DOMAIN = "someday"
if (
  "TEXTDOMAINDIR" in os.environ
  and gettext.find(DOMAIN, os.environ["TEXTDOMAINDIR"])
):
    gettext.install(DOMAIN, os.environ["TEXTDOMAINDIR"])
elif gettext.find(DOMAIN, sys.path[0]):
    gettext.install(DOMAIN, sys.path[0])
else:
    gettext.install(DOMAIN)

# These globals will be populated and used below
_prog_tty_settings = None
_shell_tty_settings = None
_shell_cursor = None
screen = None
args = None

def get_args(args=None):
    parser = argparse.ArgumentParser(prog="someday")
    parser.add_argument("--calendar", type=str, default=None)
    parser.add_argument("--past", type=int, default=None)
    parser.add_argument("--future", type=int, default=None)
    parser.add_argument("--past-for-search", type=int, default=None)
    parser.add_argument("--future-for-search", type=int, default=None)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--search", type=str, default=None)
    group.add_argument("--regex", type=str, default=None)
    parser.add_argument("--diff", action="store_true", default=False)
    return parser.parse_args(args)

def get_search_pattern(args):
    if args.search:
        return re.compile(re.escape(args.search), flags=re.IGNORECASE)
    elif args.regex:
        return re.compile(args.regex, flags=re.IGNORECASE)
    else:
        return None

# Some data types used by the program

# Views restrict the calendar items shown by the program to a certain date
# range and possibly a search pattern (regex)

View = namedtuple("View", ["past", "future", "search_pattern"])

# UserViewModes are defined in an external configuration file which specifies
# the --past, --future, and --search/--regex arguments. A function which reads
# the configuration file will translate those arguments to a View

UserViewMode = namedtuple("UserViewMode", ["name", "args", "view"])

# InternalViewModes are defined from functions which may implement an
# interaction with the user (e.g., entering a search string)

InternalViewMode = namedtuple("InternalViewMode", ["name", "func"])

# MenuItems contain a function to be called to perform some action, and map
# keypresses to those functions.

MenuItem = namedtuple("MenuItem", ["key", "name", "func"])

# A class to map different exceptions that can be thrown during program
# execution to a single type

class InternalException(Exception):
    pass

# A class for interacting with the calendar

class Calendar:
    def __init__(self):
        self._calendar = args.calendar or self._get_default_calendar()
        self._proxy_calendar = self._calendar + ".SOMEDAY"
        self._backup_calendar = self._calendar + ".SOMEDAY.BAK"

        self._check_no_proxy_calendar_exists()

        with open(self._calendar) as infile:
            self._calendar_lines = infile.read().splitlines()

        self._line_numbers = []
        self._modified = False
        self._created_backup = False

        self._view_mode = View(None, None, None)

        self._last_modified = os.path.getmtime(self._calendar)

    def _get_default_calendar(self):
        try:
            with open("%s/.when/preferences" % os.environ["HOME"]) as f:
                prefs = f.read()
            m = re.match(r"^\s*calendar\s*=\s*(.+)$", prefs, flags=re.MULTILINE)
            return m.group(1).strip()
        except (FileNotFoundError, AttributeError):
            sys.exit(_("No calendar configuration for 'when' was found."))

    def _check_no_proxy_calendar_exists(self):
        if os.path.exists(self._proxy_calendar):
            sys.exit(_("The program seems to be already running. If you are sure this is not the case, delete the file %s and try again.") % self._proxy_calendar)

    def cleanup_proxy_calendar(self):
        os.unlink(self._proxy_calendar)

    def set_view_mode(self, mode):
        self._view_mode = mode
        self._update_view()

    # Copy the when's calendary to a temporary file where each non-empty line is line-numbered, and get upcoming items from there

    def _update_view(self):
        with open(self._proxy_calendar, "w") as outfile:
            for i, line in enumerate(self._calendar_lines):
                tmp_line = "%s-%s" % (line, i) if line.strip() else line
                print(tmp_line, file=outfile)

        d = ["when", "--calendar=%s" % self._proxy_calendar, "--noheader", "--wrap=0"]

        if self._view_mode.past is not None:
            d.append("--past=%s" % self._view_mode.past)
        if self._view_mode.future is not None:
            d.append("--future=%s" % self._view_mode.future)

        try:
            tmp = subprocess.run(d, capture_output=True, text=True, check=True).stdout
        except subprocess.CalledProcessError:
            raise InternalException
        if tmp.startswith("*"):
            raise InternalException
        if self._view_mode.search_pattern is not None:
            tmp = tmp.splitlines()
            tmp = list(filter(lambda x: self._search(x), tmp))
            tmp = "\n".join(tmp)
        tmp = re.findall(r"^(.+)-(\d+)$", tmp, flags=re.MULTILINE)
        self._shown_items = [x[0] for x in tmp]
        self._line_numbers = [int(x[1]) for x in tmp]

    def _search(self, item):
        try:
            m = re.match(r"^\s*(?:\S+\s+){4}(.+?)-\d+$", item)
            return self._view_mode.search_pattern.search(m.group(1)) is not None
        except AttributeError:
            sys.exit(_("Internal error: could not process the output of when"))

    def get_items(self):
        return self._shown_items

    def get_item(self, index):
        return self._shown_items[index]

    def get_source_line(self, index):
        line_number = self._line_numbers[index]
        return self._calendar_lines[line_number]

    def modified(self):
        return self._modified

    # Check to see if writing the calendar could overwrite changes done by an external process

    def conflicting_changes(self):
        return self._last_modified != os.path.getmtime(self._calendar)

    # Update the true calendar

    def write_calendar(self):
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
            self._update_view()
            self._modified = True
            return True
        except InternalException:
            self._calendar_lines[line_number] = old_value
            return False

    def delete_source_line(self, index):
        line_number = self._line_numbers[index]
        old_value = self._calendar_lines[line_number]
        del self._calendar_lines[line_number]
        try:
            self._update_view()
            self._modified = True
            return True
        except InternalException: # This should never happen, but just in case...
            self._calendar_lines.insert(line_number, old_value)
            return False

    def add_source_line(self, what):
        self._calendar_lines.append(what)
        try:
            self._update_view()
            self._modified = True
            return True
        except InternalException:
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
            screen.addstr(minrow, mincol, _("No items were found for the specified dates."))

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

class Menu:
    def __init__(self):
        self._menu = []
        self._key_bindings =  {}
        self._selected_index = None
        self._selected_menu_item = None

    # Delete the menu, but keep the last defined _selected_menu_item, to be
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
                self._key_bindings[ord(key.lower())] = what
                self._key_bindings[ord(key.upper())] = what
            else:
                self._key_bindings[key] = what

    def show(self, screen, minrow, mincol, maxrow, maxcol):
        if not self._menu:
            return
        width = maxcol - mincol + 1
        item_width = width // len(self._menu)
        squeeze = any(len(x.name)+1 >= item_width for x in self._menu)
        if squeeze:
            lengths = [len(x.name)+1 for x in self._menu[:-1]] + [len(self._menu[-1].name)]
        else:
            lengths = [item_width for x in self._menu]
        overflow = sum(lengths) > width
        self._adjust_selected_item()
        col = 2 if overflow else 0
        first_item = 0
        if overflow:
            aux = 2 if self._selected_index < len(self._menu)-1 else 0
            while col + sum(lengths[first_item:self._selected_index+1]) + aux > width:
                first_item += 1
        if first_item > 0:
             # Override bug when writing to the lower right corner
            try:
                screen.addstr(minrow, 0, "<", curses.color_pair(1))
            except curses.error:
                pass
        i = first_item
        for item in self._menu[first_item:]:
            if col + len(item.name) > width or i < len(self._menu)-1 and col + len(item.name) + 2 >= width:
                screen.addstr(minrow, col, ">", curses.color_pair(1))
                break
            elif squeeze and i>first_item:
                screen.addstr(minrow, col-1, "|", curses.color_pair(1))
            color = 2 if self._selected_index == i else 1
             # Override bug when writing to the lower right corner
            try:
                screen.addstr(minrow, col, item.name, curses.color_pair(color))
            except curses.error:
                pass
            col += lengths[i]
            if col >= width:
                break
            i += 1

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

    def get_selected_item(self, key):
        if key == 32:
            return self._selected_menu_item
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

_YMD_dates = {}

def get_YMD_date(julian_date):
    if julian_date in _YMD_dates:
        return _YMD_dates[julian_date]
    try:
        today = get_julian_date()
        delta = julian_date - today
        date = subprocess.run(["date", "--date", "%s days" % delta, "+%Y %m %d"], capture_output=True, text=True, check=True).stdout.strip()
        _YMD_dates[julian_date] = date
        return date
    except (subprocess.CalledProcessError, InternalException):
        say(_("There was an error while trying to compute the new date. Enter an exact date instead of an interval."))
        return None

_julian_dates = {}

def get_julian_date(now=None):
    if now in _julian_dates:
        return _julian_dates[now]
    d = ["when", "j"]
    if now is not None:
        # The date is not surrounded by ', because no shell processing will be
        # done and we must pass the string as it will be received by when
        d.append("--now=%s" % now)
    try:
        tmp = subprocess.run(d, capture_output=True, text=True, check=True).stdout.strip()
        m = re.search(r"(\d{5})\.$", tmp)
        j = int(m.group(1)) if m else None
        _julian_dates[now] = j
        return j
    except (subprocess.CalledProcessError, AttributeError):
        raise InternalException

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

# An utility function to enter a date either as YMD or days from now
def my_date_input():
    say(_("Enter a date as YYYY MM DD or how many days from now (negative number=days in the past), or press Enter to return."))
    while True:
        _input = my_input()
        if not _input:
            return None
        try:
            if is_numeric(_input):
                _input = _input[1:] if _input.startswith("+") else _input
                return get_julian_date() + int(_input)
            elif re.match(r"\S+\s+\S+\s+\S+", _input):
                return get_julian_date(_input)
            else:
                say(_("Wrong format!"))
        except (re.error, InternalException):
            say(_("It looks you've entered a wrong date, or there was some underlying error. If the problem persists, try entering the date as a number of days from now instead."))

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
                say(_("It looks you entered a wrong calendar line. Try it again. To leave the item unchanged, use the cursor up key to get the original line and press Enter."))

def delete(calendar, selected_item):
    calendar.delete_source_line(selected_item)

def can_delete(calendar, selected_item):
    return calendar.happens_only_once(selected_item)

def comment(calendar, selected_item):
    calendar.update_source_line(selected_item, "#" + calendar.get_source_line(selected_item))

def can_comment(calendar, selected_item):
    return calendar.happens_only_once(selected_item)

@outside_curses
def reschedule(calendar, selected_item):
    what = calendar.get_event(selected_item)
    date = calendar.get_date_expression(selected_item)
    say(what)
    while True:
        j = my_date_input()
        if not j:
            return
        date = get_YMD_date(j)
        if calendar.update_source_line(selected_item, "%s , %s" % (date, what)):
            break
        else:
            say(_("It looks you entered a wrong date/interval, or something has gone wrong. Try it again."))

def can_reschedule(calendar, selected_item):
    return calendar.happens_only_once(selected_item)

def is_numeric(text):
    text = text.strip()
    return text.isdigit() or text[0] in "+-" and text[1:].isdigit()

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
    except (re.error, AttributeError):
        screen.clear()
        screen.refresh()
        say(_("There has been an error while trying to calculate the advanced date. Press any key to return to the listing."))
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
    say(_("What?:"))
    what = my_input()
    _input = None
    while what:
        j = my_date_input()
        if not j:
            return
        date = get_YMD_date(j)
        if calendar.add_source_line("%s , %s" % (date, what)):
            break
        else:
            say(_("It looks you entered a wrong date/interval, or something has gone wrong. Try it again."))

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

_user_view_modes = None

def choose_view_mode(calendar, item_list):
    global _user_view_modes

    internal_modes = []
    _search = "--search=%s" % args.search if args.search else None
    _regex = "--regex=%s" % args.regex if args.regex else None
    _args = "%s %s %s" % ("--past=%s " % args.past if args.past else "", "--future=%s " % args.future if args.future else "", _search or _regex or "")
    _args = _args.strip() or _("No command-line arguments were given")
    internal_modes.append(InternalViewMode(_("Use given arguments: %s") % _args, lambda: View(args.past, args.future, get_search_pattern(args))))
    internal_modes.append(InternalViewMode(_("Use when's defaults"), lambda: View(None, None, None)))
    internal_modes.append(InternalViewMode(_("Enter a date range"), lambda: create_view(False, False)))
    internal_modes.append(InternalViewMode(_("Search a string"), lambda: create_view(True, False)))
    internal_modes.append(InternalViewMode(_("Search a regex"), lambda: create_view(True, True)))
    screen.clear()
    screen.refresh()
    screen.addstr(0, 0, _("Choose a view mode:"))
    i = 0
    row = 2
    for mode in internal_modes:
        screen.addstr(row, 0, "%s: %s" % (i+1, internal_modes[i][0]))
        i += 1
        row += 1
    if _user_view_modes is None:
        conf_file = sys.path[0] + "/someday.viewmodes"
        try:
            _user_view_modes = get_user_view_modes(conf_file)
        except InternalException:
            row += 1
            screen.addstr(row, 0, _("There was an error while trying to read user view modes from file %s") % conf_file)
            row += 2
            _user_view_modes = []
    key_for_special_modes = "u"
    for j, mode in enumerate(_user_view_modes):
        if j == 9:
            break
        screen.addstr(row, 0, "%s%s: %s = %s" % (key_for_special_modes, j+1, mode.name, mode.args))
        row += 1
    screen.addstr(row, 0, "q: " + _("Back"))
    choosing_user_mode = False
    message_row = row + 2
    while True:
        try:
            key = screen.getkey()
        except KeyboardInterrupt:
            break
        if key.lower() == "q":
            if choosing_user_mode:
                choosing_user_mode = False
                curses.setsyx(message_row, 0)
                screen.deleteln()
                continue
            else:
                break
        elif key.lower() == key_for_special_modes and _user_view_modes:
            choosing_user_mode = True
            screen.addstr(message_row, 0, _("Enter the number of the user view mode or press q to go back."))
            continue
        elif not key.isdigit():
            continue
        i = int(key) - 1
        if i < 0:
            continue
        elif choosing_user_mode and i < len(_user_view_modes):
            view = _user_view_modes[i].view
        elif not choosing_user_mode and i < len(internal_modes):
            view = internal_modes[i].func()
            if view is None:
                break
        else:
            continue
        calendar.set_view_mode(view)
        item_list.top()
        break

def get_user_view_modes(conf_file):
    modes = []
    if os.path.exists(conf_file):
        try:
            with open(conf_file) as f:
                conf = map(str.strip, f.read().splitlines())
            conf = filter(lambda x: not x.startswith("#"), conf)
            conf = "\n".join(conf)
            tmp = re.findall(r"^(.+?)\s*=\s*(.+)\s*$", conf, flags=re.MULTILINE)
            for mode in tmp:
                args = get_args(shlex.split(mode[1]))
                pattern = get_search_pattern(args)
                modes.append(UserViewMode(mode[0], mode[1], View(args.past, args.future, pattern)))
        except (FileNotFoundError, re.error):
            raise InternalException
    return modes

@outside_curses
def create_view(include_search, is_regex):
    if include_search:
        say(_("Enter a regular expression, without delimiters") if is_regex else _("Search what:"))
        while True:
            what = my_input()
            if not what:
                return None
            if is_regex:
                try:
                    pattern = re.compile(what, flags=re.IGNORECASE)
                    break
                except re.error:
                    say(_("It looks like you've entered a wrong regex. Try it again."))
            else:
                pattern = re.compile(re.escape(what), flags=re.IGNORECASE)
                break
    else:
        pattern = None
    if args.past_for_search is None:
        say(_("From date:"))
        j = my_date_input()
        if not j:
            return None
        past = j - get_julian_date()
    else:
        past = args.past_for_search
    if args.future_for_search is None:
        say(_("To date:"))
        j = my_date_input()
        if not j:
            return None
        future = j - get_julian_date()
    else:
        future = args.future_for_search
    return View(past, future, pattern)

def show_calendar():
    _show_monthly_calendar()
    screen.getch()

@outside_curses
def _show_monthly_calendar():
    subprocess.run(["when", "--calendar_today_style=bgred", "c"])
    print()
    print(_("Press any key to go back."))

def recreate_menu(menu, calendar, item_list):
    menu.clear()
    if calendar.get_items():
        selected_item = item_list.selected_item()
        menu.add(MenuItem(_("e"), _("Edit"), edit))
        if can_delete(calendar, selected_item):
            menu.add(MenuItem([_("d"), curses.KEY_DC], _("Done/delete"), delete))
        if can_reschedule(calendar, selected_item):
            menu.add(MenuItem(_("r"), _("Reschedule"), reschedule))
        if can_comment(calendar, selected_item):
            menu.add(MenuItem(_("c"), _("Comment"), comment))
        if can_advance(calendar, selected_item):
            menu.add(MenuItem(_("a"), _("Advance"), advance))
        if can_open_url(calendar, selected_item):
            menu.add(MenuItem(_("b"), _("Browse url"), open_url))
        menu.add(MenuItem(_("u"), _("dUplicate"), duplicate))
    menu.add(MenuItem(_("n"), _("New"), new))
    menu.add(MenuItem(_("v"), _("View"), choose_view_mode))
    menu.add(MenuItem(_("m"), _("Monthly cal."), show_calendar))

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
    except InternalException:
        julian_date = _("Could not be determined.")

    # Main loop for handling key inputs
    while True:

        stdscr.clear()

        # Get the size of the window and define areas for the list of items and menu
        width, height = os.get_terminal_size()
        first_row = 2
        last_row = height - 3
        menu_row = height - 1

        # Show the date at top of the screen

        stdscr.addstr(0, 0, _("%s - Julian date: %s") % (get_date(), julian_date))

        # Draw the list of items
        item_list.show(stdscr, first_row, 0, last_row, width-1)

        # Update and draw the menu of actions
        recreate_menu(menu, calendar, item_list)
        menu.show(stdscr, menu_row, 0, menu_row, width-1)

        stdscr.refresh()

        # Flush the input stream
        curses.flushinp()

        # Get the key input
        key = stdscr.getch()

        # Handle the cursor keys to navigate the list of items and the menu of actions
        if key < 0: # Resizing of window generates a negative code
            width, height = os.get_terminal_size()
            curses.resizeterm(height, width)
        elif key == curses.KEY_UP:
            item_list.up()
        elif key == curses.KEY_DOWN:
            item_list.down()
        elif key == curses.KEY_LEFT:
            menu.left()
        elif key == curses.KEY_RIGHT:
            menu.right()
        elif chr(key).lower() == "q":
            break
        else:
            if key == 10:
                func = expand
            else:
                menu_item = menu.get_selected_item(key)
                func = menu_item.func if menu_item else None
            if func is None:
                pass
            elif func is choose_view_mode:
                func(calendar, item_list)
            elif func in [new, show_calendar]:
                func()
            elif calendar.get_items():
                selected_item = item_list.selected_item()
                item = calendar.get_item(selected_item)
                row = first_row + item_list.selected_row()
                if func is expand:
                    func(item, row, 0, last_row, width-1)
                else:
                    func(calendar, selected_item)

if __name__ == "__main__":
    args = get_args()
    calendar = Calendar()
    try:
        calendar.set_view_mode(View(args.past, args.future, get_search_pattern(args)))
    except re.error:
        sys.exit(_("Wrong regular expression given."))
    _shell_tty_settings = termios.tcgetattr(sys.stdin.fileno())
    try:
        while True:
            curses.wrapper(main, calendar)
            if not calendar.modified():
                break
            elif calendar.conflicting_changes():
                print()
                say(_("It appears that the calendar file was modified by other process since the program was opened. Are you sure you want to overwrite it? y/[n]: "))
                if input().lower().strip() == _("y"):
                    calendar.write_calendar()
                    break
            else:
                calendar.write_calendar()
                break
    except KeyboardInterrupt:
        print(_("Exiting without changes."))
    finally:
        calendar.cleanup_proxy_calendar()
        if args.diff:
            calendar.diff()
