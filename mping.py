import asyncio
import curses
import ipaddress
import socket
import time
from datetime import datetime

import aioping
import async_dns.resolver
import tomli
from async_dns.core import Address, types

COLORPAIR_ODDROW = 1
COLORPAIR_EVENROW = 2
COLORPAIR_TIMEOUT = 3
COLORPAIR_WAITING = 4

IP_COLUMN = 0
ALIAS_COLUMN = 1
NAME_COLUMN = 2
LATENCY_COLUMN = 3
HISTOGRAM_COLUMN = 4

dns_cache = {}


def map_address(address):
    try:
        return ipaddress.ip_address(address)
    except ValueError:
        ip_address = socket.gethostbyname(address)
        dns_cache[ip_address] = address
        addresses_table[ip_address] = addresses_table[address]
        return ipaddress.ip_address(ip_address)


with open("mping.toml", "rb") as infile:
    hosts = tomli.load(infile)

addresses_table = {}
for alias, params in hosts["hosts"].items():
    if isinstance(params, str):
        address = params
    else:
        address = params["address"]
    addresses_table[address] = alias

ping_interval = 1.0
ping_timeout = 3.0
log_timeouts = None
if mping := hosts.get("mping"):
    if interval_value := mping.get("interval"):
        ping_interval = interval_value
    if value := mping.get("timeout"):
        ping_timeout = value
    if log_timeouts := mping.get("log_timeouts"):
        log_timeouts_filename = log_timeouts

addresses = list(addresses_table.keys())
addresses = [map_address(addr) for addr in addresses]
addresses = sorted(addresses)


async def reverse_lookup(address):
    """Reverse DNS lookup"""
    arpa = ".".join(map(str, reversed(address.packed))) + ".in-addr.arpa"
    client = async_dns.resolver.DNSClient()
    try:
        response = await client.query(arpa, types.PTR, Address.parse("10.10.224.133"))
        if response.an:
            return (address, response.an[0].data.data)
        else:
            return (address, None)
    except TimeoutError as e:
        return (address, e)
    except asyncio.TimeoutError as e:
        return (address, e)
    except asyncio.CancelledError as e:
        return (address, e)


def classify(x):
    if x is None:
        return "×"
    x = x * 1000
    levels = "▁▂▃▄▅▆▇█"
    index = min(int(x // 5), len(levels) - 1)
    return levels[index]


async def ping(id, addr):
    """Ping host"""
    try:
        delay = await aioping.ping(str(addr), timeout=ping_timeout)
    except TimeoutError:
        delay = None
    return (id, addr, delay)


def format_millis(millis):
    if millis < 1:
        return f"{millis:6.3f}"
    elif millis < 10:
        return f"{millis:6.2f}"
    elif millis < 100:
        return f"{millis:6.1f}"
    else:
        return f"{millis:6.0f}"


async def main(win):
    win.timeout(0)

    attr = [
        curses.color_pair(COLORPAIR_EVENROW) + curses.A_BOLD,
        curses.color_pair(COLORPAIR_ODDROW),
    ]

    timeout_attr = curses.color_pair(COLORPAIR_TIMEOUT) + curses.A_BOLD
    waiting_attr = curses.color_pair(COLORPAIR_WAITING) + curses.A_BOLD

    dns_tasks = list()

    tasks = set()

    col_pos = [0, 17, 23, 23, 32]

    def adjust_col(index, count):
        count = count + 2
        cur_width = col_pos[index + 1] - col_pos[index]
        if count > cur_width:
            insert_length = count - cur_width
            insert = " " * insert_length
            for i in range(len(addresses)):
                win.move(i, col_pos[index + 1])
                win.insstr(insert)
            win.refresh()
            for i in range(index + 1, len(col_pos)):
                col_pos[i] += insert_length

    for row, addr in enumerate(addresses):
        alias = addresses_table[str(addr)]
        win.addstr(row, IP_COLUMN, str(addr).rjust(15), attr[row % 2])
        adjust_col(ALIAS_COLUMN, len(alias))
        win.addstr(row, col_pos[ALIAS_COLUMN], alias, attr[row % 2])
        task = asyncio.create_task(reverse_lookup(addr))
        tasks.add(task)
        dns_tasks.append(task)
    win.refresh()

    running = True

    task_run_interval = ping_interval / len(addresses)
    next_task = 0
    current_generation = [0] * len(addresses)
    timeouts = 0

    # schedule task running tasks at interval
    next_task_run = time.monotonic()
    wait_for_task_run = asyncio.create_task(asyncio.sleep(next_task_run - time.monotonic()))
    tasks.add(wait_for_task_run)

    # schedule ping timeout check
    next_timeout_check = time.monotonic() + 1.0
    wait_for_timeout_check = asyncio.create_task(asyncio.sleep(next_timeout_check - time.monotonic()))
    tasks.add(wait_for_timeout_check)

    while running:
        # wait for task(s) to complete
        done_tasks, tasks = await asyncio.wait(list(tasks), return_when=asyncio.FIRST_COMPLETED)

        for task in done_tasks:
            if task in dns_tasks:
                address, outcome = task.result()
                index = addresses.index(address)
                if not outcome:
                    if cached_dns := dns_cache.get(str(address)):
                        outcome = f"({cached_dns})"
                    elif alias := addresses_table.get(str(address)):
                        outcome = f"[{alias}]"
                    else:
                        outcome = "[?]"
                attrx = attr[index % 2]
                adjust_col(NAME_COLUMN, len(outcome))
                win.move(index, col_pos[NAME_COLUMN])
                win.addstr(outcome, attrx)

            elif task == wait_for_task_run:
                current_generation[next_task] += 1
                win.move(next_task, col_pos[HISTOGRAM_COLUMN])
                win.insstr("○", waiting_attr)
                addr = addresses[next_task]
                task = asyncio.create_task(ping((next_task, current_generation[next_task]), addr))
                tasks.add(task)

                next_task += 1
                if next_task == len(addresses):
                    next_task = 0

                next_task_run += task_run_interval
                wait_for_task_run = asyncio.create_task(asyncio.sleep(next_task_run - time.monotonic()))
                tasks.add(wait_for_task_run)

            elif task == wait_for_timeout_check:
                # restart task
                next_timeout_check += 1.0
                wait_for_timeout_check = asyncio.create_task(asyncio.sleep(next_timeout_check - time.monotonic()))
                tasks.add(wait_for_timeout_check)

                # show timeout counts on screen
                win.move(len(addresses), 0)
                win.clrtoeol()
                win.addstr(str(timeouts))

                # save number of timeouts to log file
                if log_timeouts_filename:
                    with open(log_timeouts_filename, "a") as outfile:
                        outfile.write(f"{datetime.now()},{timeouts}\n")
                timeouts = 0

            else:
                task_id, addr, delay = task.result()
                if delay is None:
                    timeouts += 1
                histogram_char = classify(delay)
                task_number, task_generation = task_id
                win.move(task_number, col_pos[HISTOGRAM_COLUMN] + current_generation[task_number] - task_generation)
                if histogram_char == "×":
                    win.addstr(histogram_char, timeout_attr)
                else:
                    win.addstr(histogram_char, attr[task_number % 2])
                win.move(task_number, col_pos[LATENCY_COLUMN])
                if delay:
                    win.addstr(format_millis(delay * 1000), attr[task_number % 2])
        win.move(len(addresses), 0)
        win.refresh()
        if win.getch() == ord("q"):
            running = False


def scrmain(win):
    curses.init_pair(COLORPAIR_EVENROW, curses.COLOR_BLUE, curses.COLOR_BLACK)
    curses.init_pair(COLORPAIR_ODDROW, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(COLORPAIR_TIMEOUT, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(COLORPAIR_WAITING, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    asyncio.run(main(win))


if __name__ == "__main__":
    curses.wrapper(scrmain)
