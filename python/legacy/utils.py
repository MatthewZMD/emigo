#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Utility functions for Emigo, primarily focused on Emacs communication.

This module provides helper functions used across the Emigo Python backend.
Its main role is to facilitate communication from Python back to the Emacs
Lisp frontend using the EPC (Emacs Process Communication) protocol.

Key Features:
- Initialization and management of the EPC client connection to Emacs.
- Functions (`eval_in_emacs`, `get_emacs_func_result`, `get_emacs_var`, etc.)
  to execute Elisp code or retrieve variables from Emacs, both synchronously
  and asynchronously.
- Argument transformation helpers (`epc_arg_transformer`) to bridge Python
  data types and Elisp S-expressions.
- Basic file/path utilities (`path_to_uri`, `read_file_content`).
- OS detection (`get_os_name`).
"""

# Copyright (C) 2022 Andy Stewart
#
# Author:     Andy Stewart <lazycat.manatee@gmail.com>
# Maintainer: Andy Stewart <lazycat.manatee@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import functools
from typing import Optional
from urllib.parse import urlparse

import sexpdata
import logging
import pathlib
import platform
import sys
import re

from epc.client import EPCClient

import orjson as json_parser

epc_client: Optional[EPCClient] = None

# initialize logging, default to STDERR and INFO level
logger = logging.getLogger("emigo")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())


def init_epc_client(emacs_server_port):
    global epc_client

    if epc_client is None:
        try:
            epc_client = EPCClient(("127.0.0.1", emacs_server_port), log_traceback=True)
        except ConnectionRefusedError:
            import traceback
            logger.error(traceback.format_exc())


def close_epc_client():
    if epc_client is not None:
        epc_client.close()


def eval_in_emacs(method_name, *args):
    # Construct the list for the S-expression directly with Python types
    sexp_list = [sexpdata.Symbol(method_name)] + list(args)
    # Let sexpdata.dumps handle conversion and escaping of Python types (str, int, etc.)
    sexp = sexpdata.dumps(sexp_list)

    logger.debug("Eval in Emacs: %s", sexp)
    # Call eval-in-emacs elisp function.
    epc_client.call("eval-in-emacs", [sexp])    # type: ignore


def message_emacs(message: str):
    """Message to Emacs with prefix."""
    eval_in_emacs("message", "[Emigo] " + message)


def epc_arg_transformer(arg):
    """Transform elisp object to python object
    1                          => 1
    "string"                   => "string"
    (list :a 1 :b 2)           => {"a": 1, "b": 2}
    (list :a 1 :b (list :c 2)) => {"a": 1, "b": {"c": 2}}
    (list 1 2 3)               => [1 2 3]
    (list 1 2 (list 3 4))      => [1 2 [3 4]]
    """
    if not isinstance(arg, list):
        return arg

    # NOTE: Empty list elisp can be treated as both empty python dict/list
    # Convert empty elisp list to empty python dict due to compatibility.

    # check if we can tranform arg to python dict instance
    type_dict_p = len(arg) % 2 == 0
    if type_dict_p:
        for v in arg[::2]:
            if (not isinstance(v, sexpdata.Symbol)) or not v.value().startswith(":"):
                type_dict_p = False
                break

    if type_dict_p:
        # transform [Symbol(":a"), 1, Symbol(":b"), 2] to dict(a=1, b=2)
        ret = dict()
        for i in range(0, len(arg), 2):
            ret[arg[i].value()[1:]] = epc_arg_transformer(arg[i + 1])
        return ret
    else:
        return list(map(epc_arg_transformer, arg))


def convert_emacs_bool(symbol_value, symbol_is_boolean):
    if symbol_is_boolean == "t":
        return symbol_value is True
    else:
        return symbol_value

def get_emacs_vars(args):
    return list(map(lambda result: convert_emacs_bool(result[0], result[1]) if result != [] else False,
                    epc_client.call_sync("get-emacs-vars", args)))    # type: ignore


def get_emacs_var(var_name):
    symbol_value, symbol_is_boolean = epc_client.call_sync("get-emacs-var", [var_name])    # type: ignore

    return convert_emacs_bool(symbol_value, symbol_is_boolean)


def get_emacs_func_result(method_name, *args):
    """Call eval-in-emacs elisp function synchronously and return the result."""
    result = epc_client.call_sync(method_name, args)    # type: ignore
    return result


def get_command_result(command_string, cwd):
    import subprocess

    process = subprocess.Popen(command_string, cwd=cwd, shell=True, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding="utf-8")
    ret = process.wait()
    return "".join((process.stdout if ret == 0 else process.stderr).readlines()).strip()    # type: ignore


def generate_request_id():
    import random
    return abs(random.getrandbits(16))


# modified from Lib/pathlib.py
def _make_uri_win32(path):
    from urllib.parse import quote_from_bytes as urlquote_from_bytes
    # Under Windows, file URIs use the UTF-8 encoding.
    drive = path.drive
    if len(drive) == 2 and drive[1] == ':':
        # It's a path on a local drive => 'file:///c:/a/b'
        rest = path.as_posix()[2:].lstrip('/')
        return 'file:///%s%%3A/%s' % (
            drive[0], urlquote_from_bytes(rest.encode('utf-8')))
    else:
        # It's a path on a network drive => 'file://host/share/a/b'
        return 'file:' + urlquote_from_bytes(path.as_posix().encode('utf-8'))

def path_to_uri(path):
    path = pathlib.Path(path)
    if get_os_name() != "windows":
        uri = path.as_uri()
    else:
        if not path.is_absolute():
            raise ValueError("relative path can't be expressed as a file URI")
        # encode uri to 'file:///c%3A/project/xxx.js' like vscode does
        uri = _make_uri_win32(path)
    return uri


def uri_to_path(uri):
    from urllib.parse import unquote
    # parse first, '#' may be part of filepath(encoded)
    parsed = urlparse(uri)
    # for example, ts-ls return 'file:///c%3A/lib/ref.js'
    path = unquote(parsed.path)
    if sys.platform == "win32":
        path = path[1:]
    return path


def path_as_key(path):
    key = path
    # NOTE: (buffer-file-name) return "d:/Case/a.go", gopls return "file:///D:/Case/a.go"
    if sys.platform == "win32":
        path = pathlib.Path(path).as_posix()
        key = path.lower()
    return key


def add_to_path_dict(path_dict, filepath, value):
    path_dict[path_as_key(filepath)] = value


def is_in_path_dict(path_dict, path):
    path_key = path_as_key(path)
    return path_key in path_dict


def remove_from_path_dict(path_dict, path):
    del path_dict[path_as_key(path)]


def get_from_path_dict(path_dict, filepath):
    return path_dict[path_as_key(filepath)]


def log_time(message):
    import datetime
    logger.info("\n--- [{}] {}".format(datetime.datetime.now().time(), message))

@functools.lru_cache(maxsize=None)
def get_emacs_version():
    return get_emacs_func_result("get-emacs-version")


def get_os_name():
    return platform.system().lower()

def parse_json_content(content):
    return json_parser.loads(content)

def read_file_content(abs_path: str) -> str:
    """Reads the content of a file."""
    # Basic implementation, consider adding error handling for encoding etc.
    # like in repomapper.read_text
    try:
        # Try UTF-8 first, the most common encoding
        with open(abs_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        # If UTF-8 fails, try the system's default encoding or latin-1 as fallback
        try:
            with open(abs_path, 'r', encoding=sys.getdefaultencoding()) as f:
                return f.read()
        except UnicodeDecodeError:
            # As a last resort, try latin-1, which rarely fails but might misinterpret chars
            with open(abs_path, 'r', encoding='latin-1') as f:
                return f.read()
    except Exception as e:
        print(f"Error reading file {abs_path}: {e}", file=sys.stderr)
        raise # Re-raise for the agent handler to catch and format

def touch(path):
    import os

    if not os.path.exists(path):
        basedir = os.path.dirname(path)

        if not os.path.exists(basedir):
            os.makedirs(basedir)

        with open(path, 'a'):
            os.utime(path)


# --- Filtering Helper ---
def _filter_context(text: str) -> str:
    """Removes <context>...</context> blocks from text."""
    if not isinstance(text, str): # Handle potential non-string content
        return text
    # Use re.DOTALL to make '.' match newlines, make it non-greedy
    return re.sub(r"<context>.*?</context>\s*", "\n", text, flags=re.DOTALL)
