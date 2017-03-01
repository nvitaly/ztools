#!/usr/bin/env python3

#  TODO PLAN
#   1. config file (global and personal)
#        zabbix creds, min priority
#   - NOT DOING IT 2. Fitlering by hostname pattern, description
#   3. IP Address
#   4. Datacenter
#   5. Make sound for new events
#   6. test blinking on raspberry
#   7. Condensed view for last events
#   8. keyboard commands
#        h - help, a - ACK/NO ACK,
#        NO l - last events condenced or plan list,
#        1,2,3,4,5,6 - choose min priority
#        f - enable/diable filter (I do not think we need filter!)

import os
import configparser
from pyzabbix import ZabbixAPI
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

from curses import wrapper
import curses
from datetime import datetime
from collections import defaultdict, namedtuple

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

priority_map = [
    {"name": "N/A"},
    {"name": "INFO"},
    {"name": "Warning"},
    {"name": "Average"},
    {"name": "HIGH"},
    {"name": "Disaster"}]


def add_end(str1, str2, max_x):
    return str1 + " " * (max_x - len(str1) - len(str2)) + str2

def fill_line(str, max_x):
    if len(str) >= max_x:
        return str[:max_x]

    return str + " " * (max_x - len(str))

def time_since(unixtime):
    sec = int((datetime.now() - datetime.fromtimestamp(unixtime)).total_seconds())

    days = round(sec / (24 * 60 * 60))
    if days > 0:
        return "{}d".format(days)

    hours = round(sec / (60 * 60))
    if hours > 0:
        return "{}h".format(hours)

    minutes = round(sec / 60)
    if minutes > 0:
        return "{}m".format(minutes)

    return "{}s".format(sec)


def mk_ts(unixtime):
    return datetime.fromtimestamp(unixtime).strftime("%Y-%m-%d %H:%M:%S")

def draw_screen_help(s, lastkey, data):
    s.clear()
    s.addstr(2, 2, "a - to toggle ACK/unACK events")
    s.addstr(3, 2, "c - to toggle compact last event")
    s.addstr(4, 2, "q - to exit")

    s.timeout(10 * 1000)
    try:
        key = s.getkey()
    except:
        return

def draw_screen(s, adata, hdata, priority, ack, compact):
    refresh_time = 10000
    max_y, max_x = s.getmaxyx()

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    s.addstr(0,
             0,
             add_end("Active Problems: {} ACKed: {}".format(
                        len([ x for x in adata if x.ack == 0 ]),
                        len([ x for x in adata if x.ack == 1 ])
                     ), ts, max_x))

    i = 0
    for i, el in enumerate([ x for x in adata if x.ack == 0]):
        if i > max_y/3:
            s.addstr(2+i, 2, "Skipping....")
            break
        s.addstr(2+i, 0,
                 fill_line("{ts} {age:>4} {p:>8} {h:<{mh}} {d}".format(
                    ts=mk_ts(el.ptime),
                    age=time_since(el.ptime),
                    p=priority_map[el.priority]["name"],
                    h=el.host[:32],
                    d=el.description,
                    mh=32), max_x-1) ,
                 curses.color_pair(5) | curses.A_BOLD)
    s.addstr(2+i+1, 0, " " * max_x)
    s.addstr(2+i+2, 0, fill_line("Last events:", max_x), curses.A_BLINK)

    for ii, el in enumerate(hdata):
        if 2+i+3+ii >= max_y:
            break

        s.addstr(2+i+3+ii, 0,
                 fill_line("{pts} {rts} {age:>4} {p:>4} {h:<{mh}} {d}".format(
                    pts=mk_ts(el.ptime) if el.ptime else "        N/A        ",
                    rts=mk_ts(el.rtime) if el.rtime else "        N/A        " ,
                    age=23,
                    p=priority_map[el.priority]["name"],
                    h=el.host,
                    d=el.description,
                    mh=32), max_x-1),
                 curses.color_pair(el.priority))

    s.refresh()

    s.timeout(400)
    blink = 0
    while 1:
        try:
            key = s.getkey()
            return key
        except curses.error as err:
            refresh_time = refresh_time - 400
            if refresh_time <= 0:
               return ""
            if i > 0: # that bad and not readble - FIX IT
                if blink == 0:
                    s.addstr(0, 0, "Active Problems:")
                    blink = 1
                else:
                    s.addstr(0, 0, "Active Problems:",
                             curses.color_pair(5) | curses.A_REVERSE)
                    blink = 0
                s.refresh()
        except KeyboardInterrupt:
            return "exit"


def zabbix_get_data(z, ack):
    data = dict()

    aparams = {
        "filter": {"value": 1},
        "active": True,
        "monitored": True,
        "expandDescription": True,
        "sortfield": ["priority", "lastchange"],
        "sortorder": "DESC",
        "selectHosts": ["name"],
        "selectLastEvent": ["acknowledged"]
    }
    hparams = {
        "limit": 200,
        "output": "extend",
        "selectRelatedObject": ["description", "priority"],
        "expandDescription": True,
        "sortfield": ["clock"],
        "sortorder": "DESC",
        "selectHosts": ["name"]    
    }

    data["active"] = z.trigger.get(**aparams)
    data["history"] = [x for x in z.event.get(**hparams) 
                       if len(x["hosts"]) >= 1]

    return data

def process_active(data):
    """Get raw events from zabbix API and produce list:
       problem time, host, priority, acknowledged, description """
    elist = list()
    for p in data:
        elist.append(namedtuple('adata', ["ptime", "host", "priority", "ack", "description"])(
                int(p["lastchange"]),
                p["hosts"][0]["name"],
                int(p["priority"]),
                int(p["lastEvent"]["acknowledged"]),
                p["description"]
            ))

    return elist


def process_history(data):
    """Get raw events from zabbix API and produce list:
    [problem time, recovery time, host, priority, description]"""
    events = defaultdict(dict)
    for ev in data:
        # value == 0 is OK, 1 is PROBLEM
        if ev["value"] == "0":
            events[ev["eventid"]]["OK"] = ev
        elif ev["value"] == "1":
            if ev["r_eventid"] == "0":
                events[ev["eventid"]]["PROBLEM"] = ev
            else:
                events[ev["r_eventid"]]["PROBLEM"] = ev

    elist = list()

    for p in sorted(events.keys(), key=lambda x: int(x), reverse=True):
        elist.append(namedtuple('adata', ["ptime", "rtime", "host", "priority", "description"])(
            int(events[p]["PROBLEM"]["clock"]) if "PROBLEM" in events[p] else None,
            int(events[p]["OK"]["clock"]) if "OK" in events[p] else None,
            events[p]["OK"]["hosts"][0]["name"] 
                if "OK" in events[p] else 
                    events[p]["PROBLEM"]["hosts"][0]["name"],
            int(events[p]["OK"]["relatedObject"]["priority"]) 
                if "OK" in events[p] else 
                    int(events[p]["PROBLEM"]["relatedObject"]["priority"]),
            events[p]["OK"]["relatedObject"]["description"] 
                if "OK" in events[p] else 
                    events[p]["PROBLEM"]["relatedObject"]["description"],
           ))
    return elist    

def main(s, config):
    curses.curs_set(0)

    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_BLUE, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(6, curses.COLOR_RED, curses.COLOR_BLACK)

    z = ZabbixAPI(config.get("server", "url"))
    z.session.auth = (
        config.get("server", "user"),
        config.get("server", "pass"))
    z.session.verify = False
    z.timeout = 10
    z.login(
        config.get("server", "user"),
        config.get("server", "pass"))

    ack = True
    compact = True
    priority = 4
    lastkey = "none"
    while(1):
        data = zabbix_get_data(z, ack)

        lastkey = draw_screen(s,
                              process_active(data["active"]),
                              process_history(data["history"]),
                              priority,
                              ack,
                              compact)

        if lastkey in ["q", "exit"]:
            break
        if lastkey == "a":
            if ack:
                ack = False
            else:
                ack = True
        if lastkey == "h":
            draw_screen_help(s, lastkey, data)

config = configparser.ConfigParser()
config.read([
    os.path.dirname(os.path.realpath(__file__)) + "/pyzabbix.conf",
    os.getenv("HOME") + "/pyzabbix.conf",
    "/etc/pyzabbix.conf"])
wrapper(main, config)
