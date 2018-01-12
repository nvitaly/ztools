#!/usr/bin/env python3

#  TODO PLAN
#   1. DONE config file (global and personal)
#        zabbix creds, min priority
#   3. IP Address
#   5. Make sound for new events 
#   6. test blinking on raspberry < go with BLINK!
#   7. Condensed view for last events
#   8. keyboard commands
#        h - help, a - ACK/NO ACK, <- DONE
#        NO l - last events condenced or plan list,

import os
import sys
import configparser
from pyzabbix import ZabbixAPI
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
import subprocess
import syslog

from curses import wrapper
import curses
from datetime import datetime
import time
from collections import defaultdict, namedtuple

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

priority_map = [
    {"name": "N/A"},
    {"name": "INFO"},
    {"name": "Warning"},
    {"name": "Average"},
    {"name": "HIGH"},
    {"name": "Disaster"}]

global_last_event_clock = 0
global_last_active_clock = 0
global_ack_active_clock = 0
global_active_led = 0
global_debug = True

def log(msg):
    if not global_debug:
        return
    syslog.syslog(msg)
    


def add_end(str1, str2, max_x):
    return str1 + " " * (max_x - len(str1) - len(str2)) + str2

def fill_line(str, max_x):
    if len(str) >= max_x:
        return str[:max_x]

    return str + " " * (max_x - len(str))

def time_since(start, end=time.time()):
    sec = end - start

    days = round(sec / (24 * 60 * 60))
    if days > 0:
        return "{}d".format(days)

    hours = round(sec / (60 * 60))
    if hours > 0:
        return "{}h".format(hours)

    minutes = round(sec / 60)
    if minutes > 0:
        return "{}m".format(minutes)

    return "<1m"


def mk_ts(unixtime):
    return datetime.fromtimestamp(unixtime).strftime("%Y-%m-%d %H:%M:%S")

def led_action_off():
    log("LED OFF")
    led_off_cmd = config.get("ztop", "led_off", fallback=None)
    if led_off_cmd and led_off_cmd != "":
        subprocess.call(led_off_cmd.split(" "))

def led_action(hdata, adata):
    global global_last_event_clock
    global global_last_active_clock
    global global_ack_active_clock
    global global_active_led

    last_event_clock = max( [x.ptime for x in hdata if x.ptime] + [x.rtime for x in hdata if x.rtime]  )
    if global_last_event_clock < last_event_clock:
        global_last_event_clock = last_event_clock
        led_new_cmd = config.get("ztop", "led_new_events", fallback=None)
        if led_new_cmd and led_new_cmd != "":
            log("LED BLINK")
            subprocess.call(led_new_cmd.split(" "))
            global_active_led = 0
    global_last_active_clock = max([x.ptime for x in adata if x.ptime] + [0]) 
    if global_last_active_clock > global_ack_active_clock:
        sev = max([x.priority for x in adata if x.ptime > global_ack_active_clock])
        if global_active_led >= sev:
            return
        global_active_led = sev
        led_x_cmd = config.get("ztop", "led_{}".format(sev), fallback=None)
        if led_x_cmd and led_x_cmd != "" :
            log("LED ON :{}".format(sev))
            subprocess.call(led_x_cmd.split(" "))

def draw_screen_help(s, lastkey, data):
    s.erase()
    s.addstr(2, 2, "a - to toggle ACK/unACK events")
    s.addstr(3, 2, "c - to toggle compact last event")
    s.addstr(4, 2, "q - to exit")

    ipinfo = subprocess.check_output(["/sbin/ip", "addr"], universal_newlines=True).split("\n")
    for i, line in enumerate(ipinfo):
        s.addstr(10+i, 2, line)




    s.timeout(10 * 1000)
    try:
        key = s.getkey()
        return
    except:
        return

def draw_screen(s, adata, hdata, priority, ack, compact):
    refresh_time = 10000
    max_y, max_x = s.getmaxyx()

    max_hhost = max([len(x.host) for x in hdata] + [0])
    max_ahost = max([len(x.host) for x in adata] + [0])

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    s.erase()

    s.addstr(0,
             0,
             add_end("Active Problems: {} ACKed: {}".format(
                        len([ x for x in adata if x.ack == 0 ]),
                        len([ x for x in adata if x.ack == 1 ])
                     ), ts, max_x))
    i = None
    for i, el in enumerate([ x for x in adata if not ack or x.ack == 0]):
        if i > max_y/2:
            s.addstr(2+i, 0, "   Skipping....")
            break
        s.addstr(2+i, 0,
                 "{ts} {age:>4} {p:>8} {h:>{mh}}  {d}".format(
                    ts=mk_ts(el.ptime),
                    age=time_since(el.ptime, time.time()),
                    p=priority_map[el.priority]["name"],
                    h=el.host[:32],
                    d=el.description,
                    mh=max_ahost), curses.color_pair(el.priority) | curses.A_BOLD)
    if i is None:
        i = 0
        s.addstr(2+i, 0, "    nothing happening :)")

    s.addstr(2+i+2, 0, "Last events:")

    for ii, el in enumerate(hdata):
        if 2+i+3+ii >= max_y:
            break

        s.addstr(2+i+3+ii, 0,
                 "{pts} {rts} {age:>4} {p:>8} {h:>{mh}}    {d}".format(
                    pts=mk_ts(el.ptime) if el.ptime else "        N/A        ",
                    rts=mk_ts(el.rtime) if el.rtime else "        N/A        " ,
                    age=time_since(el.ptime, el.rtime) if el.ptime and el.rtime else "-",
                    p=priority_map[el.priority]["name"],
                    h=el.host,
                    d=el.description,
                    mh=max_hhost), curses.color_pair(el.priority))


    led_action(hdata, adata)

    s.refresh()

    s.timeout(10 * 1000)
    try:
        key = s.getkey()
        return key
    except curses.error as err:
        return ""
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

def main(s):
    global global_last_active_clock
    global global_ack_active_clock
    global global_active_led
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
            sys.exit(0)
        if lastkey == "a":
            ack = not ack
        if lastkey == "h":
            draw_screen_help(s, lastkey, data)
        if lastkey == " ":
            global_ack_active_clock = global_last_active_clock
            global_active_led = 0
            led_action_off()

config = configparser.ConfigParser()
config.read([
    os.path.dirname(os.path.realpath(__file__)) + "/pyzabbix.conf",
    os.getenv("HOME") + "/pyzabbix.conf",
    "/etc/pyzabbix.conf"])
wrapper(main)
