"""Column storage for benchmark samples; materialize dictionaries after playback.

Numeric payloads contain no Python object references for GC to traverse. Text columns retain only
immutable strings. Every sample is retained, so tail statistics and request
correlation do not silently become a sample of the final few seconds.
"""

from array import array
import math
from threading import Lock, local


class ProbeRecords:
    def __init__(self, integers=(), floats=(), texts=(), flags=()):
        self.columns = {name: array('q') for name in integers}
        self.columns.update({name: array('d') for name in floats})
        self.columns.update({name: [] for name in texts})
        self.columns.update({name: array('b') for name in flags})
        self.flags = set(flags)
        self.lock = Lock()

    def __len__(self):
        return len(next(iter(self.columns.values())))

    def append(self, value):
        # Phase samples arrive from several parent threads. Keep column writes
        # together; GIL switching between columns must not corrupt correlation.
        with self.lock:
            index = len(self)
            for name, column in self.columns.items():
                column.append(self.encode(column, value.get(name)))
            return index

    def update(self, index, values):
        with self.lock:
            for name, value in values.items():
                column = self.columns[name]
                column[index] = self.encode(column, value)

    @staticmethod
    def encode(column, value):
        if isinstance(column, list):
            return value
        if value is None:
            return math.nan if column.typecode == 'd' else -1
        return value

    def __iter__(self):
        for index in range(len(self)):
            value = {}
            for name, column in self.columns.items():
                item = column[index]
                if isinstance(column, array):
                    missing = math.isnan(item) if column.typecode == 'd' else item == -1
                    if missing:
                        continue
                if name in self.flags:
                    item = bool(item)
                value[name] = item
            # Request-less phases still participate in phase summaries.
            if 'request_id' in self.columns:
                value.setdefault('request_id', None)
            yield value

    def storage_bytes(self):
        # Numeric payload plus string references; excludes text contents and
        # array/list spare capacity. This is not process RSS.
        return sum(
            len(c) * (c.itemsize if isinstance(c, array) else 8) for c in self.columns.values()
        )


class ThreadProbeRecords:
    """Separate columns per producer; no shared hot-path recorder lock.

    Registration is a single list append in this CPython benchmark. Export only
    after workers close. The returned token identifies the receiving thread's
    exact row for the subsequent request-ID decode update.
    """

    def __init__(self, **schema):
        self.schema = schema
        self.local = local()
        self.stores = []

    def append(self, value):
        store = getattr(self.local, 'store', None)
        if store is None:
            store = self.local.store = ProbeRecords(**self.schema)
            self.stores.append(store)
        return store, store.append(value)

    def update(self, token, values):
        store, index = token
        store.update(index, values)

    def __iter__(self):
        for store in self.stores:
            yield from store

    def storage_bytes(self):
        return sum(store.storage_bytes() for store in self.stores)
