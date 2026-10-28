# -*- coding: utf-8 -*-

import logging
import sys
import socket
import time
import warnings
from collections import OrderedDict
from copy import copy, deepcopy
from functools import wraps
from itertools import filterfalse
import urllib.request

from feeluown.i18n import human_readable_number

logger = logging.getLogger(__name__)


def win32_is_port_binded(host, port):
    """
    sock.connect_ex may block for 2 second if port is not used on Windows.
    This may be the fastest way to check if a port is bined on Windows.
    Remember that (127.0.0.1, port) and (0.0.0.0, port) are different for `sock.bind`.
    """
    import errno  # pylint: disable=import-outside-toplevel

    inuse = False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except socket.error as e:
        if e.errno == errno.EADDRINUSE:
            inuse = True
        else:
            raise
    finally:
        sock.close()
    return inuse


def is_port_inuse(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    rv = sock.connect_ex(("127.0.0.1", port))
    sock.close()
    return rv == 0


def parse_ms(ms):
    minute = int(ms / 60000)
    second = int((ms % 60000) / 1000)
    return minute, second


def log_exectime(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        t = time.process_time()
        result = func(*args, **kwargs)
        elapsed_time = time.process_time() - t
        logger.debug(
            "function %s executed time: %f ms", func.__name__, elapsed_time * 1000
        )
        return result

    return wrapper


def elfhash(s):
    """
    :param string: bytes

    >>> import base64
    >>> s = base64.b64encode(b'hello world')
    >>> elfhash(s)
    224648685
    """
    hash = 0
    x = 0
    for c in s:
        hash = (hash << 4) + c
        x = hash & 0xF0000000
        if x:
            hash ^= x >> 24
            hash &= ~x
    return hash & 0x7FFFFFFF


class DedupList(list):
    """List that doesn't contain duplicate items

    The item should properly implement __hash__ and __eq__. If items
    are not equal to each other, they must not have same hash.
    """

    def _get_index(self, index):
        """project idx into range(len)"""
        if index <= -len(self):
            return 0
        if index < 0:
            return index + len(self)
        if index >= len(self):
            return len(self)
        return index

    @staticmethod
    def dic():
        """return a dict that remembers insertion order"""
        return {} if sys.version_info[1] > 5 else OrderedDict()

    def __init__(self, seq=(), dedup=True):
        if dedup:
            seq = self.dic().fromkeys(seq)
        # Each cell records its index and the front removals already applied.
        self._front_pops = 0
        self._defer_front = True
        self._map = {obj: [idx, 0] for obj, idx in zip(seq, range(len(seq)))}
        super().__init__(seq)

    def _cell_index(self, cell):
        shift = self._front_pops - cell[1]
        if shift and cell[0] > 0:
            cell[0] = max(0, cell[0] - shift)
        cell[1] = self._front_pops
        return cell[0]

    def _flush_indices(self):
        front_pops = self._front_pops
        if front_pops:
            for cell in self._map.values():
                shift = front_pops - cell[1]
                if shift and cell[0] > 0:
                    cell[0] = max(0, cell[0] - shift)
                cell[1] = 0
            self._front_pops = 0

    def __getitem__(self, item):
        result = super().__getitem__(item)
        if isinstance(item, slice):
            # Always return a DedupList when slicing to avoid accidentally
            # convert a DedupList to normal list.
            return DedupList(result, dedup=False)
        else:
            return result

    def __add__(self, other):
        if isinstance(other, list):
            result = copy(self)
            result.extend(other)
            return result
        raise TypeError("can only concatenate list to DedupList")

    def __radd__(self, other):
        if isinstance(other, list):
            if isinstance(other, DedupList):
                result = copy(other)
            else:
                # To avoid accidentally convert a DedupList to normal list.
                result = DedupList(other)
            result.extend(self)
            return result
        raise TypeError("invalid concat")

    def __setitem__(self, key, value):
        if value in self._map:
            raise ValueError("item already exists in DedupList")
        self._map.pop(self[key])
        if type(key) is not int:
            self._flush_indices()
            self._defer_front = False
        self._map[value] = [key, self._front_pops]
        super().__setitem__(key, value)

    def __contains__(self, item):
        return item in self._map

    def __copy__(self):
        result = DedupList(self, dedup=False)
        return result

    def __deepcopy__(self, memo):
        inter_list = [deepcopy(item) for item in self]
        result = DedupList(inter_list, dedup=False)
        memo[id(self)] = result
        return result

    def swap(self, idx_1, idx_2):
        item_1 = self[idx_1]
        item_2 = self[idx_2]
        if type(idx_1) is not int or type(idx_2) is not int:
            self._flush_indices()
            self._defer_front = False
        self._map[item_1] = [idx_2, self._front_pops]
        self._map[item_2] = [idx_1, self._front_pops]
        super().__setitem__(idx_1, item_2)
        super().__setitem__(idx_2, item_1)

    def sort(self, *args, **kwargs):
        super().sort(*args, **kwargs)
        self._map = {obj: [idx, 0] for obj, idx in zip(self, range(len(self)))}
        self._front_pops = 0
        self._defer_front = True

    def append(self, obj):
        # Subclasses can override __len__; preserve its evaluation order.
        if type(self) is not DedupList:
            if obj not in self._map:
                self._map[obj] = [len(self), self._front_pops]
                super().append(obj)
            return
        cell = [len(self), self._front_pops]
        if self._map.setdefault(obj, cell) is cell:
            list.append(self, obj)

    def extend(self, iterable):
        length = len(self)
        append_list = list(filterfalse(self._map.__contains__, iterable))  # dedup
        self._map.update((obj, [idx, self._front_pops])
                         for idx, obj in enumerate(append_list, length))
        super().extend(append_list)

    def index(self, object, start: int = None, stop: int = None):
        cell = self._map.get(object)
        if cell is None:
            raise ValueError("not in list")
        # get idx from _map directly
        idx = self._cell_index(cell)
        if start:
            if not stop:
                stop = len(self)
            if idx not in range(start, stop):
                raise ValueError("not in list")
        return idx

    def insert(self, index: int, obj):
        if obj not in self._map:
            # insert accepts out-of-range indices, but we don't want them in our _map
            index = self._get_index(index)
            self._flush_indices()
            # all idx in _map after 'index' should be changed
            for cell in self._map.values():
                if cell[0] >= index:
                    cell[0] = cell[0] + 1
            if type(index) is not int:
                self._defer_front = False
            self._map[obj] = [index, 0]
            super().insert(index, obj)

    def pop(self, index: int = None):
        index = index if index is not None else -1
        item = super().pop(index)
        # list.pop() returns an index, so no need to calculate manually like 'insert'
        cell = self._map.pop(item)
        front_pops = self._front_pops
        index = cell[0]
        if self._defer_front and 0 <= index <= front_pops - cell[1]:
            self._front_pops = front_pops + 1
            return item
        if front_pops and self._defer_front:
            index = self._cell_index(cell)
            # Normalize deferred shifts and apply this removal in one pass.
            for cell in self._map.values():
                position = cell[0]
                shift = front_pops - cell[1]
                if shift and position > 0:
                    position = max(0, position - shift)
                if position > index:
                    position -= 1
                cell[0] = position
                cell[1] = 0
            self._front_pops = 0
        else:
            index = self._cell_index(cell)
            self._flush_indices()
            # all idx in _map after 'index' should be changed
            for cell in self._map.values():
                if cell[0] > index:
                    cell[0] = cell[0] - 1
        return item

    def remove(self, item):
        cell = self._map.get(item)
        if cell is None:
            # raise same exception as list
            raise ValueError("list.remove(x): x not in list")
        idx = self._cell_index(cell)
        self.pop(idx)

    def clear(self):
        self._map.clear()
        super().clear()
        self._front_pops = 0
        self._defer_front = True


def int_to_human_readable(i: int) -> str:
    warnings.warn(
        "Since FeelUOwn 5.1, it's recommended to use "
        "feeluown.i18n.human_readable_number for L10N support.",
        DeprecationWarning,
        stacklevel=2,
    )
    return human_readable_number(n=i)


def detect_proxy() -> dict:
    """
    Detect system proxy settings.

    :return: Dict of proxies read from system, or empty dict if no proxy detected.
    """
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        proxies = {}
    # getproxies may include a 'no' key from NO_PROXY; it is not a real proxy.
    proxies.pop("no", None)
    return proxies
