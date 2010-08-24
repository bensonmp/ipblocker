#!/usr/bin/env python
import httplib2
import os

from ipblocker import model, logger
from ipblocker.config import config
from ipblocker import util
from ipblocker.source_blocker import SourceBlocker
import GeoIP

class ZeusBlocker(SourceBlocker):
    blocker = 'zeus'
    duration = 60*60*24*30
    must_exist_in_source = True
    flag_traffic = True


    def get_records(self):
        h = httplib2.Http(os.path.expanduser("~/.httplib2_cache"))
        logger.debug("Fetching IP list from the zeus tracker")
        resp, content = h.request("https://zeustracker.abuse.ch/blocklist.php?download=ipblocklist")
        ips = content.split("\n\n")[1].split()
        ips = self.remove_US(ips)
        records = [{'ip': ip} for ip in ips]
        return records

    def remove_US(self, ips):
        g=GeoIP.new(GeoIP.GEOIP_STANDARD)
        ok = [ip for ip in ips if g.country_code_by_addr(ip) != 'US']
        skipped = len(ips) - len(ok)
        logger.debug("removed %d US ips" % skipped)
        return ok
        
    def serialize_record(self, record):
        return "zeusbot C&Cs"

def get_records():
    return ZeusBlocker(None).get_records()

def main():
    b = ZeusBlocker(model)
    b.block()

if __name__ == "__main__":
    main()