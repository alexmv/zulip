#!/usr/bin/env python3
import gzip
import os
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import log

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts.lib.setup_path import setup_path

setup_path()

os.environ["DJANGO_SETTINGS_MODULE"] = "zproject.settings"
from typing import Deque, Dict, List, Optional, Set, TextIO, Tuple

import django

django.setup()

import sentry_sdk

sentry_sdk.init()

from zerver.models import Attachment


@dataclass
class FileMetadata:
    name: str
    size: int
    create_time: datetime
    last_seen: Optional[datetime] = None


@dataclass
class CacheSimulator:
    seconds: int
    max_size: int = 0
    current_bytes: int = 0
    max_bytes: int = 0
    pointer: int = -1
    cached_path_ids: Dict[str, int] = field(default_factory=lambda: defaultdict(int))


class Stats:
    def __init__(self) -> None:
        self.start_time: Optional[datetime] = None

        self.regex = re.compile(
            r'(\S+) \S+ \S+ \[([^\]]+)\] "GET /user_uploads/(?:download/)?([^? ]+)[^"]+" (\d+) (\d+) "[^"]+" "([^"]+)"'
        )
        self.metadata: Dict[str, FileMetadata] = {}
        self.hit_rate_log: List[int] = []
        self.total_reqs = 0

        self.cache_stream: Deque[Tuple[str, datetime]] = deque()
        self.cache_simulators: List[CacheSimulator] = []
        for seconds in [
            1,
            5,
            15,
            30,
            60,
            90,
            120,
            180,
            240,
            300,
            600,
            900,
            1200,
            1800,
            60 * 60 * 1,
            60 * 60 * 2,
            60 * 60 * 6,
            60 * 60 * 12,
            60 * 60 * 24,
        ]:
            self.cache_simulators.append(CacheSimulator(seconds))

        self.client_cache: Set[Tuple[str, str, str]] = set()
        self.client_bytes_no_cache = 0
        self.client_bytes_with_cache = 0

    def parse_line(self, line: str) -> None:
        match = self.regex.match(line)
        if match is None:
            return
        (ip_addr, timestamp, path_id, status_code, served_size, user_agent) = match.groups()
        if not status_code.startswith(("2", "3")):
            return
        if self.start_time is None:
            self.start_time = datetime.strptime(timestamp, "%d/%b/%Y:%H:%M:%S %z")

        if path_id not in self.metadata:
            attachment = Attachment.objects.filter(path_id=path_id).order_by("id").first()
            if attachment is None:
                # Attachments can be deleted, in which case we have no idea
                return
            self.metadata[path_id] = FileMetadata(path_id, attachment.size, attachment.create_time)

        if self.metadata[path_id].create_time < self.start_time:
            return

        size = self.metadata[path_id].size

        if (path_id, ip_addr, user_agent) in self.client_cache:
            self.client_bytes_with_cache += 0
            if status_code == "304":
                # We don't return, here, because we would need to
                # fetch the resource to know to return a 304 -- and we
                # count this as no cache savings, since the client
                # didn't get any bytes
                self.client_bytes_no_cache += 0
            else:
                self.client_bytes_no_cache += size
                # We want to pretend that this request didn't happen,
                # because the client ideally cached it.
                return
        else:
            self.client_bytes_with_cache += size
            self.client_bytes_no_cache += size
            self.client_cache.add((path_id, ip_addr, user_agent))

        parsed_date = datetime.strptime(timestamp, "%d/%b/%Y:%H:%M:%S %z")

        self.total_reqs += 1
        last_seen = self.metadata[path_id].last_seen
        if last_seen is not None:
            self.hit_rate_log.append(int((parsed_date - last_seen) / timedelta(seconds=1)))
        self.metadata[path_id].last_seen = parsed_date
        self.update_caches(path_id, parsed_date)

    def update_caches(self, path_id: str, parsed_date: datetime) -> None:
        self.cache_stream.appendleft((path_id, parsed_date))
        max_pointer = 0
        for cs in self.cache_simulators:
            cs.pointer += 1
            if cs.cached_path_ids[path_id] == 0:
                cs.current_bytes += self.metadata[path_id].size
            cs.cached_path_ids[path_id] += 1
            cutoff = parsed_date - timedelta(seconds=cs.seconds)
            while cs.pointer >= 0 and self.cache_stream[cs.pointer][1] < cutoff:
                dropping_path_id = self.cache_stream[cs.pointer][0]
                cs.cached_path_ids[dropping_path_id] -= 1
                if cs.cached_path_ids[dropping_path_id] == 0:
                    del cs.cached_path_ids[dropping_path_id]
                    cs.current_bytes -= self.metadata[dropping_path_id].size
                cs.pointer -= 1
            cs.max_size = max(cs.max_size, len(cs.cached_path_ids))
            cs.max_bytes = max(cs.max_bytes, cs.current_bytes)

            max_pointer = max(max_pointer, cs.pointer)
        for _ in range(max_pointer + 1, len(self.cache_stream)):
            self.cache_stream.pop()


def maybe_gzip(logfile_name: str) -> TextIO:
    if logfile_name.endswith(".gz"):
        return gzip.open(logfile_name, "rt")
    return open(logfile_name)


def main() -> None:
    s = Stats()

    for filename in sys.argv:
        with maybe_gzip(filename) as logfile:
            for line_no, line in enumerate(logfile):
                if line_no % 100 == 0:
                    print(".", end="")
                    sys.stdout.flush()
                s.parse_line(line)
    print("")
    s.hit_rate_log.sort()
    last = 0.0
    for i, delta in enumerate(s.hit_rate_log, 1):
        if delta > 60 * 60 * 24:
            break
        if delta > 0 and log(delta) > last + 0.5:
            print(f"{delta}\t{100 * i / s.total_reqs}")
            last = log(delta)

    print("\n\n\n")
    for cs in s.cache_simulators:
        print(f"{cs.seconds}\t\t{cs.max_bytes/1024/1024}\t{cs.max_size}")

    print("\n\n\n")
    print(
        f"Ideal client-side caching would save: {100 - 100 * s.client_bytes_with_cache / s.client_bytes_no_cache}%"
    )


if __name__ == "__main__":
    main()
