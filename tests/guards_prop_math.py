"""Guards for prop_dist / prop_value / kalshi_nba_props (hermetic: no network, tiny fixtures).

    python3 tests/guards_prop_math.py        # exits 1 on any failed check
    from tests.guards_prop_math import run   # run(ck) inside the main suite

Every check pins a DEFINITION as well as a number: an expected value is computed
here by a route that shares no code with the function under test (direct
summation by recurrence, Simpson integration of a density, integer ceil on
cents, Fractions), and the sentence says what the number is, not just that it is
still there. The market fixtures below are REAL payloads trimmed to the fields
the reader uses: Kalshi's list endpoint and one order book, ESPN scoreboard and
roster entries, all fetched 2026-10-23 00:54-01:10 UTC (the first-listing books
sit at 2c / 68c with no volume -- exactly the "listing, not an opinion" shape
believable() exists to drop).
"""
import ast
import copy
import math
import os
import random
import subprocess
import sys
import tempfile
from fractions import Fraction

os.environ.setdefault("VIGIL_NO_BG", "1")
os.environ.setdefault("ERRLOG_DB", os.path.join(tempfile.gettempdir(), "prop_math_guards_errlog.db"))
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)

# ---- real fixtures --------------------------------------------------------
RAW_MARKETS = [{'ticker': 'KXNBAPTS-26OCT23NOPUTA-UTAJJACKSON20-25',
  'event_ticker': 'KXNBAPTS-26OCT23NOPUTA',
  'title': 'Jaren Jackson Jr.: 25+ points',
  'yes_sub_title': 'Jaren Jackson Jr.: 25+',
  'strike_type': 'greater',
  'floor_strike': 24.5,
  'status': 'active',
  'yes_bid_dollars': '0.0200',
  'yes_ask_dollars': '0.6800',
  'no_bid_dollars': '0.3200',
  'no_ask_dollars': '0.9800',
  'last_price_dollars': '0.0000',
  'volume_fp': '0.00',
  'open_interest_fp': '0.00',
  'volume_24h_fp': '0.00',
  'yes_ask_size_fp': '1.00',
  'yes_bid_size_fp': '4.00',
  'close_time': '2026-10-26T01:30:00Z',
  'expected_expiration_time': '2026-10-24T07:30:00Z',
  'occurrence_datetime': '2026-10-24T04:30:00Z',
  'updated_time': '2026-10-23T00:41:33.845254Z'},
 {'ticker': 'KXNBAPTSPROT-26OCT23NOPUTA-UTAJJACKSON20-25',
  'event_ticker': 'KXNBAPTSPROT-26OCT23NOPUTA',
  'title': 'Jaren Jackson Jr.: 25+ points',
  'yes_sub_title': 'Jaren Jackson Jr.: 25+',
  'strike_type': 'greater',
  'floor_strike': 24.5,
  'status': 'active',
  'yes_bid_dollars': '0.0000',
  'yes_ask_dollars': '0.9800',
  'no_bid_dollars': '0.0200',
  'no_ask_dollars': '1.0000',
  'last_price_dollars': '0.0000',
  'volume_fp': '0.00',
  'open_interest_fp': '0.00',
  'volume_24h_fp': '0.00',
  'yes_ask_size_fp': '3.00',
  'yes_bid_size_fp': '0.00',
  'close_time': '2026-10-26T01:30:00Z',
  'expected_expiration_time': '2026-10-24T07:30:00Z',
  'occurrence_datetime': '2026-10-24T04:30:00Z',
  'updated_time': '2026-10-23T00:33:19.567596Z'},
 {'ticker': 'KXNBA3PT-26OCT23DETMIA-DETIJOESF-3',
  'event_ticker': 'KXNBA3PT-26OCT23DETMIA',
  'title': 'Isaiah Joe: 3+ threes',
  'yes_sub_title': 'Isaiah Joe: 3+',
  'strike_type': 'greater',
  'floor_strike': 2.5,
  'status': 'active',
  'yes_bid_dollars': '0.0200',
  'yes_ask_dollars': '0.6800',
  'no_bid_dollars': '0.3200',
  'no_ask_dollars': '0.9800',
  'last_price_dollars': '0.0000',
  'volume_fp': '0.00',
  'open_interest_fp': '0.00',
  'volume_24h_fp': '0.00',
  'yes_ask_size_fp': '3.00',
  'yes_bid_size_fp': '3.00',
  'close_time': '2026-10-26T00:00:00Z',
  'expected_expiration_time': '2026-10-24T06:00:00Z',
  'occurrence_datetime': '2026-10-24T03:00:00Z',
  'updated_time': '2026-10-22T23:44:13.209584Z'},
 {'ticker': 'KXNBAPRA-26OCT23LACLAL-LALLDONCIC77-40',
  'event_ticker': 'KXNBAPRA-26OCT23LACLAL',
  'title': 'Luka Dončić: 40+ points, rebounds, and assists',
  'yes_sub_title': 'Luka Dončić: 40+',
  'strike_type': 'greater',
  'floor_strike': 39.5,
  'status': 'active',
  'yes_bid_dollars': '0.0200',
  'yes_ask_dollars': '0.9800',
  'no_bid_dollars': '0.0200',
  'no_ask_dollars': '0.9800',
  'last_price_dollars': '0.0000',
  'volume_fp': '0.00',
  'open_interest_fp': '0.00',
  'volume_24h_fp': '0.00',
  'yes_ask_size_fp': '25.00',
  'yes_bid_size_fp': '3.00',
  'close_time': '2026-10-26T02:00:00Z',
  'expected_expiration_time': '2026-10-24T08:00:00Z',
  'occurrence_datetime': '2026-10-24T05:00:00Z',
  'updated_time': '2026-10-23T00:41:33.703698Z'},
 {'ticker': 'KXNBA2D-26OCT23PORSAC-SACPACHIUWA9',
  'event_ticker': 'KXNBA2D-26OCT23PORSAC',
  'title': 'Precious Achiuwa: Double Double',
  'yes_sub_title': 'Precious Achiuwa',
  'strike_type': 'greater',
  'floor_strike': 0.5,
  'status': 'active',
  'yes_bid_dollars': '0.0100',
  'yes_ask_dollars': '0.6000',
  'no_bid_dollars': '0.4000',
  'no_ask_dollars': '0.9900',
  'last_price_dollars': '0.0000',
  'volume_fp': '0.00',
  'open_interest_fp': '0.00',
  'volume_24h_fp': '0.00',
  'yes_ask_size_fp': '5.00',
  'yes_bid_size_fp': '20.00',
  'close_time': '2026-10-26T02:00:00Z',
  'expected_expiration_time': '2026-10-24T05:00:00Z',
  'occurrence_datetime': '2026-10-24T05:00:00Z',
  'updated_time': '2026-10-22T20:10:54.640008Z'},
 {'ticker': 'KXNBA3D-26OCT23PORSAC-SACDSABONIS11',
  'event_ticker': 'KXNBA3D-26OCT23PORSAC',
  'title': 'Domantas Sabonis: Triple Double',
  'yes_sub_title': 'Domantas Sabonis',
  'strike_type': 'greater',
  'floor_strike': 0.5,
  'status': 'active',
  'yes_bid_dollars': '0.0100',
  'yes_ask_dollars': '0.9600',
  'no_bid_dollars': '0.0400',
  'no_ask_dollars': '0.9900',
  'last_price_dollars': '0.0000',
  'volume_fp': '0.00',
  'open_interest_fp': '0.00',
  'volume_24h_fp': '0.00',
  'yes_ask_size_fp': '2750.00',
  'yes_bid_size_fp': '50.00',
  'close_time': '2026-10-26T02:00:00Z',
  'expected_expiration_time': '2026-10-24T05:00:00Z',
  'occurrence_datetime': '2026-10-24T05:00:00Z',
  'updated_time': '2026-10-22T20:10:55.511249Z'},
 {'ticker': 'KXNBASTOCK-26OCT22CLEPHI-CLEPWATSON8-3',
  'event_ticker': 'KXNBASTOCK-26OCT22CLEPHI',
  'title': 'Peyton Watson: 3+ steals and blocks',
  'yes_sub_title': 'Peyton Watson: 3+',
  'strike_type': 'greater',
  'floor_strike': 2.5,
  'status': 'active',
  'yes_bid_dollars': '0.2300',
  'yes_ask_dollars': '1.0000',
  'no_bid_dollars': '0.0000',
  'no_ask_dollars': '0.7700',
  'last_price_dollars': '0.9900',
  'volume_fp': '79.00',
  'open_interest_fp': '79.00',
  'volume_24h_fp': '79.00',
  'yes_ask_size_fp': '0.00',
  'yes_bid_size_fp': '1.00',
  'close_time': '2026-10-24T23:00:00Z',
  'expected_expiration_time': '2026-10-23T05:00:00Z',
  'occurrence_datetime': '2026-10-23T02:00:00Z',
  'updated_time': '2026-10-22T22:05:53.056213Z'},
 {'ticker': 'KXNBAPTS-26OCT22CLEPHI-CLEDMITCHELL45-45',
  'event_ticker': 'KXNBAPTS-26OCT22CLEPHI',
  'title': 'Donovan Mitchell: 45+ points',
  'yes_sub_title': 'Donovan Mitchell: 45+',
  'strike_type': 'greater',
  'floor_strike': 44.5,
  'status': 'active',
  'yes_bid_dollars': '0.0000',
  'yes_ask_dollars': '0.2400',
  'no_bid_dollars': '0.7600',
  'no_ask_dollars': '1.0000',
  'last_price_dollars': '0.0000',
  'volume_fp': '0.00',
  'open_interest_fp': '0.00',
  'volume_24h_fp': '0.00',
  'yes_ask_size_fp': '136.00',
  'yes_bid_size_fp': '0.00',
  'close_time': '2026-10-24T23:00:00Z',
  'expected_expiration_time': '2026-10-23T05:00:00Z',
  'occurrence_datetime': '2026-10-23T02:00:00Z',
  'updated_time': '2026-10-23T00:49:00.560659Z'},
 {'ticker': 'KXNBAPTS-26OCT22CLEPHI-PHILJAMES23-10',
  'event_ticker': 'KXNBAPTS-26OCT22CLEPHI',
  'title': 'LeBron James: 10+ points',
  'yes_sub_title': 'LeBron James: 10+',
  'strike_type': 'greater',
  'floor_strike': 9.5,
  'status': 'active',
  'yes_bid_dollars': '0.9900',
  'yes_ask_dollars': '1.0000',
  'no_bid_dollars': '0.0000',
  'no_ask_dollars': '0.0100',
  'last_price_dollars': '0.9900',
  'volume_fp': '131402.70',
  'open_interest_fp': '125297.91',
  'volume_24h_fp': '130423.33',
  'yes_ask_size_fp': '0.00',
  'yes_bid_size_fp': '19421.81',
  'close_time': '2026-10-24T23:00:00Z',
  'expected_expiration_time': '2026-10-23T02:00:00Z',
  'occurrence_datetime': '2026-10-23T02:00:00Z',
  'updated_time': '2026-10-23T00:46:38.848367Z'},
 {'ticker': 'KXNBAPTS-26OCT22CLEPHI-PHIJEMBIID21-25',
  'event_ticker': 'KXNBAPTS-26OCT22CLEPHI',
  'title': 'Joel Embiid: 25+ points',
  'yes_sub_title': 'Joel Embiid: 25+',
  'strike_type': 'greater',
  'floor_strike': 24.5,
  'status': 'active',
  'yes_bid_dollars': '0.3200',
  'yes_ask_dollars': '0.4400',
  'no_bid_dollars': '0.5600',
  'no_ask_dollars': '0.6800',
  'last_price_dollars': '0.4200',
  'volume_fp': '181837.85',
  'open_interest_fp': '181313.60',
  'volume_24h_fp': '181837.85',
  'yes_ask_size_fp': '25.00',
  'yes_bid_size_fp': '31.50',
  'close_time': '2026-10-24T23:00:00Z',
  'expected_expiration_time': '2026-10-23T02:00:00Z',
  'occurrence_datetime': '2026-10-23T02:00:00Z',
  'updated_time': '2026-10-23T00:46:38.848367Z'},
 {'ticker': 'KXNBABLK-26OCT23DETMIA-MIAGANTETOKOUNMPO7-2',
  'event_ticker': 'KXNBABLK-26OCT23DETMIA',
  'title': 'Giannis Antetokounmpo: 2+ blocks',
  'yes_sub_title': 'Giannis Antetokounmpo: 2+',
  'strike_type': 'greater',
  'floor_strike': 1.5,
  'status': 'active',
  'yes_bid_dollars': '0.2500',
  'yes_ask_dollars': '0.8200',
  'no_bid_dollars': '0.1800',
  'no_ask_dollars': '0.7500',
  'last_price_dollars': '0.0000',
  'volume_fp': '0.00',
  'open_interest_fp': '0.00',
  'volume_24h_fp': '0.00',
  'yes_ask_size_fp': '3.00',
  'yes_bid_size_fp': '7.00',
  'close_time': '2026-10-26T00:00:00Z',
  'expected_expiration_time': '2026-10-24T03:00:00Z',
  'occurrence_datetime': '2026-10-24T03:00:00Z',
  'updated_time': '2026-10-22T23:46:06.72812Z'},
 {'ticker': 'KXNBAPTS-26OCT23NYKBOS-NYKJBRUNSON11-15',
  'event_ticker': 'KXNBAPTS-26OCT23NYKBOS',
  'title': 'Jalen Brunson: 15+ points',
  'yes_sub_title': 'Jalen Brunson: 15+',
  'strike_type': 'greater',
  'floor_strike': 14.5,
  'status': 'active',
  'yes_bid_dollars': '0.7800',
  'yes_ask_dollars': '0.9200',
  'no_bid_dollars': '0.0800',
  'no_ask_dollars': '0.2200',
  'last_price_dollars': '0.7000',
  'volume_fp': '5.00',
  'open_interest_fp': '5.00',
  'volume_24h_fp': '5.00',
  'yes_ask_size_fp': '150.00',
  'yes_bid_size_fp': '23.00',
  'close_time': '2026-10-25T23:00:00Z',
  'expected_expiration_time': '2026-10-24T05:00:00Z',
  'occurrence_datetime': '2026-10-24T02:00:00Z',
  'updated_time': '2026-10-22T23:03:46.557347Z'},
 {'ticker': 'KXNBAPTS-26OCT23NYKBOS-NYKJBRUNSON11-20',
  'event_ticker': 'KXNBAPTS-26OCT23NYKBOS',
  'title': 'Jalen Brunson: 20+ points',
  'yes_sub_title': 'Jalen Brunson: 20+',
  'strike_type': 'greater',
  'floor_strike': 19.5,
  'status': 'active',
  'yes_bid_dollars': '0.6500',
  'yes_ask_dollars': '0.7700',
  'no_bid_dollars': '0.2300',
  'no_ask_dollars': '0.3500',
  'last_price_dollars': '0.7800',
  'volume_fp': '136.89',
  'open_interest_fp': '114.95',
  'volume_24h_fp': '136.89',
  'yes_ask_size_fp': '301.00',
  'yes_bid_size_fp': '6.00',
  'close_time': '2026-10-25T23:00:00Z',
  'expected_expiration_time': '2026-10-24T02:00:00Z',
  'occurrence_datetime': '2026-10-24T02:00:00Z',
  'updated_time': '2026-10-22T23:03:46.557347Z'},
 {'ticker': 'KXNBAPTS-26OCT23NYKBOS-NYKJBRUNSON11-25',
  'event_ticker': 'KXNBAPTS-26OCT23NYKBOS',
  'title': 'Jalen Brunson: 25+ points',
  'yes_sub_title': 'Jalen Brunson: 25+',
  'strike_type': 'greater',
  'floor_strike': 24.5,
  'status': 'active',
  'yes_bid_dollars': '0.4800',
  'yes_ask_dollars': '0.5600',
  'no_bid_dollars': '0.4400',
  'no_ask_dollars': '0.5200',
  'last_price_dollars': '0.5500',
  'volume_fp': '218.69',
  'open_interest_fp': '218.69',
  'volume_24h_fp': '218.69',
  'yes_ask_size_fp': '216.00',
  'yes_bid_size_fp': '1.00',
  'close_time': '2026-10-25T23:00:00Z',
  'expected_expiration_time': '2026-10-24T02:00:00Z',
  'occurrence_datetime': '2026-10-24T02:00:00Z',
  'updated_time': '2026-10-22T23:03:46.557347Z'},
 {'ticker': 'KXNBAPTS-26OCT23NYKBOS-NYKJBRUNSON11-30',
  'event_ticker': 'KXNBAPTS-26OCT23NYKBOS',
  'title': 'Jalen Brunson: 30+ points',
  'yes_sub_title': 'Jalen Brunson: 30+',
  'strike_type': 'greater',
  'floor_strike': 29.5,
  'status': 'active',
  'yes_bid_dollars': '0.3000',
  'yes_ask_dollars': '0.3400',
  'no_bid_dollars': '0.6600',
  'no_ask_dollars': '0.7000',
  'last_price_dollars': '0.3300',
  'volume_fp': '526.97',
  'open_interest_fp': '526.97',
  'volume_24h_fp': '526.97',
  'yes_ask_size_fp': '148.00',
  'yes_bid_size_fp': '2881.47',
  'close_time': '2026-10-25T23:00:00Z',
  'expected_expiration_time': '2026-10-24T02:00:00Z',
  'occurrence_datetime': '2026-10-24T02:00:00Z',
  'updated_time': '2026-10-22T23:03:46.557347Z'},
 {'ticker': 'KXNBAPTS-26OCT23NYKBOS-NYKJBRUNSON11-35',
  'event_ticker': 'KXNBAPTS-26OCT23NYKBOS',
  'title': 'Jalen Brunson: 35+ points',
  'yes_sub_title': 'Jalen Brunson: 35+',
  'strike_type': 'greater',
  'floor_strike': 34.5,
  'status': 'active',
  'yes_bid_dollars': '0.1200',
  'yes_ask_dollars': '0.1600',
  'no_bid_dollars': '0.8400',
  'no_ask_dollars': '0.8800',
  'last_price_dollars': '0.0000',
  'volume_fp': '0.00',
  'open_interest_fp': '0.00',
  'volume_24h_fp': '0.00',
  'yes_ask_size_fp': '110.00',
  'yes_bid_size_fp': '25.00',
  'close_time': '2026-10-25T23:00:00Z',
  'expected_expiration_time': '2026-10-24T02:00:00Z',
  'occurrence_datetime': '2026-10-24T02:00:00Z',
  'updated_time': '2026-10-22T23:03:46.557347Z'}]

ESPN_EVENTS = [{'d': '20261022',
  'id': '401909093',
  'date': '2026-10-22T23:00Z',
  'competitors': [{'homeAway': 'home', 'team': {'abbreviation': 'PHI'}},
                  {'homeAway': 'away', 'team': {'abbreviation': 'CLE'}}],
  'state': 'in'},
 {'d': '20261022',
  'id': '401909094',
  'date': '2026-10-23T01:30Z',
  'competitors': [{'homeAway': 'home', 'team': {'abbreviation': 'OKC'}},
                  {'homeAway': 'away', 'team': {'abbreviation': 'DEN'}}],
  'state': 'pre'},
 {'d': '20261023',
  'id': '401909095',
  'date': '2026-10-23T23:00Z',
  'competitors': [{'homeAway': 'home', 'team': {'abbreviation': 'BOS'}},
                  {'homeAway': 'away', 'team': {'abbreviation': 'NY'}}],
  'state': 'pre'},
 {'d': '20261023',
  'id': '401909096',
  'date': '2026-10-24T01:30Z',
  'competitors': [{'homeAway': 'home', 'team': {'abbreviation': 'SA'}},
                  {'homeAway': 'away', 'team': {'abbreviation': 'HOU'}}],
  'state': 'pre'},
 {'d': '20261023',
  'id': '401909849',
  'date': '2026-10-24T01:30Z',
  'competitors': [{'homeAway': 'home', 'team': {'abbreviation': 'UTAH'}},
                  {'homeAway': 'away', 'team': {'abbreviation': 'NO'}}],
  'state': 'pre'}]

ROSTER_UTAH = [{'id': '4873138',
  'fullName': 'Ace Bailey',
  'displayName': 'Ace Bailey',
  'jersey': '19',
  'position': {'abbreviation': 'G'}},
 {'id': '4433627',
  'fullName': 'Keyonte George',
  'displayName': 'Keyonte George',
  'jersey': '3',
  'position': {'abbreviation': 'G'}},
 {'id': '4433618',
  'fullName': 'Harrison Ingram',
  'displayName': 'Harrison Ingram',
  'jersey': '00',
  'position': {'abbreviation': 'F'}},
 {'id': '4277961',
  'fullName': 'Jaren Jackson Jr.',
  'displayName': 'Jaren Jackson Jr.',
  'jersey': '20',
  'position': {'abbreviation': 'F'}},
 {'id': '4066336',
  'fullName': 'Lauri Markkanen',
  'displayName': 'Lauri Markkanen',
  'jersey': '23',
  'position': {'abbreviation': 'F'}}]

ORDERBOOK_LIVE = {'ticker': 'KXNBAAST-26OCT22DENOKC-OKCJWILLIAMS8-6',
 'fetched_utc': '2026-10-23T01:10:11Z',
 'orderbook': {'orderbook_fp': {'no_dollars': [['0.0100', '3328.00'],
                                               ['0.0200', '437.00'],
                                               ['0.0300', '371.00'],
                                               ['0.0400', '4.00'],
                                               ['0.0500', '13.00'],
                                               ['0.0600', '1.00'],
                                               ['0.0800', '1.00'],
                                               ['0.1000', '1943.00'],
                                               ['0.1400', '1.00'],
                                               ['0.1500', '3.00'],
                                               ['0.1600', '500.00'],
                                               ['0.1700', '489.00'],
                                               ['0.1800', '1.00'],
                                               ['0.1900', '255.00'],
                                               ['0.2400', '20.00'],
                                               ['0.2500', '2.00'],
                                               ['0.2900', '20.00'],
                                               ['0.3200', '2.00'],
                                               ['0.4100', '500.00'],
                                               ['0.4200', '427.00'],
                                               ['0.4300', '216.00'],
                                               ['0.4800', '26.00'],
                                               ['0.5100', '90.00'],
                                               ['0.5200', '153.00'],
                                               ['0.5300', '1949.00'],
                                               ['0.5500', '2001.00'],
                                               ['0.5800', '1262.00'],
                                               ['0.5900', '948.00'],
                                               ['0.6000', '910.00'],
                                               ['0.6100', '813.00'],
                                               ['0.6200', '1854.00'],
                                               ['0.6300', '2111.00'],
                                               ['0.6400', '1413.00']],
                                'yes_dollars': [['0.0100', '2128.00'],
                                                ['0.0200', '550.00'],
                                                ['0.0300', '1233.00'],
                                                ['0.0400', '1675.00'],
                                                ['0.0500', '520.00'],
                                                ['0.0700', '40.00'],
                                                ['0.0800', '20.00'],
                                                ['0.0900', '444.00'],
                                                ['0.1700', '2201.00'],
                                                ['0.1800', '2.00'],
                                                ['0.2000', '90.00'],
                                                ['0.2100', '2.00'],
                                                ['0.2700', '5059.00'],
                                                ['0.3200', '6347.00'],
                                                ['0.3300', '251.00'],
                                                ['0.3400', '10000.00'],
                                                ['0.3500', '75.00']]}}}

# Kalshi's published example book (docs.kalshi.com/getting_started/orderbook_responses)
ORDERBOOK_DOCS = {"orderbook_fp": {
    "yes_dollars": [["0.0100", "200.00"], ["0.1500", "100.00"], ["0.2000", "50.00"], ["0.2500", "20.00"],
                    ["0.3000", "11.00"], ["0.3100", "10.00"], ["0.3200", "10.00"], ["0.3300", "11.00"],
                    ["0.3400", "9.00"], ["0.3500", "11.00"], ["0.4100", "10.00"], ["0.4200", "13.00"]],
    "no_dollars": [["0.0100", "100.00"], ["0.1600", "3.00"], ["0.2500", "50.00"], ["0.2800", "19.00"],
                   ["0.3600", "5.00"], ["0.3700", "50.00"], ["0.3800", "300.00"], ["0.4400", "29.00"],
                   ["0.4500", "20.00"], ["0.5600", "17.00"]]}}


def _mk(ticker):
    return copy.deepcopy(next(m for m in RAW_MARKETS if m["ticker"] == ticker))


def _scoreboard(day):
    return {"events": [{"id": e["id"], "date": e["date"],
                        "competitions": [{"competitors": e["competitors"],
                                          "status": {"type": {"state": e["state"]}}}]}
                       for e in ESPN_EVENTS if e["d"] == day]}


def _close(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


def _ceil_int(num, den):
    return -((-num) // den)


def run(ck):
    """Run every check through `ck(name, cond, detail="")`. errlog.note is replaced
    by a recorder for the duration so a failure-path test never writes to the
    app's ledger (and so the guards can assert WHICH ledger id a failure used)."""
    import errlog
    real_note, noted = errlog.note, []
    errlog.note = lambda code, exc=None, msg="", path=None: noted.append(code)
    try:
        _run(ck, noted)
        import prop_value as V
        # a zero-width band is no band: robust must not be awarded on it
        _zw = V.labels_from(p_side=0.79, ev_c=25.2, ev_lo_c=25.2, width=0.0, n_eff=40)
        _wb = V.labels_from(p_side=0.79, ev_c=25.2, ev_lo_c=3.0, width=0.06, n_eff=40)
        _lv = V.line_labels(V.line_value(0.79, {"yes_bid": 46, "yes_ask": 52, "no_bid": 48, "no_ask": 54, "ts": 0,
                                                "volume": 55, "oi": 55}, 0.79, 0.79, now=10), n_eff=40)
        ck("labels: positive_ev_robust is refused on a zero-width band (p_lo = p = p_hi, the NFL board's lines when "
           "nothing beyond Sleeper ships: 'positive at the band's low end' is then just 'positive') -- directly and "
           "through line_value / line_labels; a real band still earns it",
           "positive_ev" in _zw and "positive_ev_robust" not in _zw and "positive_ev_robust" in _wb
           and "positive_ev" in _lv and "positive_ev_robust" not in _lv, str((_zw, _wb, _lv)))
    finally:
        errlog.note = real_note



def _run(ck, noted):
    import functools
    import kalshi
    import kalshi_nfl
    import combo_engine
    import prop_dist as D
    import prop_value as V
    import kalshi_nba_props as K

    NOW = 1100.0                                   # "now" for quote ages; blocks below are stamped ts 1000
    LV = functools.partial(V.line_value, now=NOW)
    LIVE = {"ts": 1000.0, "volume": 50.0, "oi": 50.0}   # a quote 100 s old on a book with trading in it

    # =====================================================================
    # A. Odds conversions (hand-computed)
    # =====================================================================
    ck("American +150 -> decimal 2.5, -200 -> 1.5, +100 and -100 -> 2.0, -110 -> 1 + 100/110",
       V.american_to_decimal(150) == 2.5 and V.american_to_decimal(-200) == 1.5
       and V.american_to_decimal(100) == 2.0 and V.american_to_decimal(-100) == 2.0
       and _close(V.american_to_decimal(-110), 1 + 100 / 110, 1e-15)
       and V.american_to_decimal(250) == 3.5 and _close(V.american_to_decimal(-500), 1.2, 1e-15))
    ck("implied probability: -110 = 110/210, +150 = 0.4, -200 = 2/3, +100 = 0.5",
       _close(V.american_to_prob(-110), 110 / 210, 1e-15) and _close(V.american_to_prob(150), 0.4, 1e-15)
       and _close(V.american_to_prob(-200), 2 / 3, 1e-15) and V.american_to_prob(100) == 0.5)
    ck("decimal -> American: 2.5 = +150, 1.5 = -200, 2.0 = +100 (not -100), 3.0 = +200, 1.25 = -400",
       _close(V.decimal_to_american(2.5), 150, 1e-12) and _close(V.decimal_to_american(1.5), -200, 1e-12)
       and V.decimal_to_american(2.0) == 100.0 and _close(V.decimal_to_american(3.0), 200, 1e-12)
       and _close(V.decimal_to_american(1.25), -400, 1e-12))
    ck("probability -> American: 0.25 = +300, 0.8 = -400, 0.4 = +150; round trip is exact",
       _close(V.prob_to_american(0.25), 300, 1e-9) and _close(V.prob_to_american(0.8), -400, 1e-9)
       and all(_close(V.american_to_prob(V.prob_to_american(p)), p, 1e-12) for p in (0.03, 0.2, 0.5, 0.77, 0.97)))

    def rejects(fn, *args):
        try:
            fn(*args)
        except ValueError:
            return True
        return False
    ck("invalid odds are rejected, not converted",
       all(rejects(V.american_to_decimal, x) for x in (0, 50, -50, 99.9, -99.9, float("nan"), float("inf"), True, "150", None))
       and all(rejects(V.decimal_to_american, x) for x in (1.0, 0.5, -2, float("nan")))
       and all(rejects(V.prob_to_decimal, x) for x in (0, 1, -0.1, 1.1))
       and rejects(V.odds_to_prob, 150, "fractional"))

    # =====================================================================
    # B. No-vig
    # =====================================================================
    nv = V.novig_odds(-110, -110)
    ck("no-vig on a -110/-110 book is 50/50 with overround 10/210 and says the vig was known",
       _close(nv["p_a"], 0.5, 1e-15) and _close(nv["p_b"], 0.5, 1e-15)
       and _close(nv["overround"], 2 * 110 / 210 - 1, 1e-15) and nv["vig_known"] and nv["two_sided"])
    nv = V.novig_odds(-150, 130)
    ck("multiplicative no-vig on -150/+130 is p / (p_a + p_b) = 69/119 exactly",
       _close(nv["p_a"], float(Fraction(69, 119)), 1e-15) and _close(nv["p_a"] + nv["p_b"], 1.0, 1e-15)
       and nv["method"] == "multiplicative")
    pw = V.novig_odds(-150, 130, method="power")
    ck("power no-vig solves p_a^k + p_b^k = 1 and moves MORE margin onto the longshot than the multiplicative one",
       abs(V.american_to_prob(-150) ** pw["power_k"] + V.american_to_prob(130) ** pw["power_k"] - 1) < 1e-12
       and pw["power_k"] > 1 and _close(pw["p_a"] + pw["p_b"], 1.0, 1e-12)
       and pw["p_a"] > nv["p_a"] and pw["p_b"] < nv["p_b"])
    one = V.novig(0.4)
    ck("a one-sided market returns the implied probability UNCHANGED and says its vig is unknown",
       one["p_a"] == 0.4 and one["p_b"] is None and one["vig_known"] is False and one["two_sided"] is False
       and one["overround"] is None)
    ck("an underround (arbitrage) pair still normalises, with a negative overround",
       _close(V.devig_two_way(0.45, 0.5)["p_a"], 0.45 / 0.95, 1e-15) and V.devig_two_way(0.45, 0.5)["overround"] < 0)
    ck("unknown no-vig method is an error", rejects(V.devig_two_way, 0.5, 0.5, "shin"))

    ref = V.kalshi_reference({"yes_bid": 40, "yes_ask": 44, "no_bid": 56, "no_ask": 60})
    ck("Kalshi reference on a tight two-sided book is the YES mid (42c); the de-vigged asks sit beside it",
       ref["kind"] == "two_sided" and _close(ref["p"], 0.42, 1e-15) and ref["vig_known"]
       and _close(ref["p_mid"], 0.42, 1e-15) and _close(ref["p_ask_devig"], 44 / 104, 1e-12) and ref["spread_c"] == 4)
    ck("ask_devig / ask_power reference methods read the two asks",
       _close(V.kalshi_reference({"yes_bid": 40, "yes_ask": 44, "no_bid": 56, "no_ask": 60}, "ask_devig")["p"], 44 / 104, 1e-12)
       and 0.4 < V.kalshi_reference({"yes_bid": 40, "yes_ask": 44, "no_bid": 56, "no_ask": 60}, "ask_power")["p"] < 0.44)
    ref = V.kalshi_reference(_clean_block(_mk("KXNBAPTS-26OCT22CLEPHI-CLEDMITCHELL45-45")))
    ck("an ask with nobody bidding is one-sided: p = the ask, vig unknown, never a mid",
       ref["kind"] == "one_sided" and _close(ref["p"], 0.24, 1e-15) and ref["vig_known"] is False and ref["method"] == "ask")
    ref = V.kalshi_reference(_clean_block(_mk("KXNBAPTS-26OCT22CLEPHI-PHILJAMES23-10")))
    ck("a bid with nobody offering is one-sided on the bid",
       ref["kind"] == "one_sided" and _close(ref["p"], 0.99, 1e-15) and ref["method"] == "bid")
    ref = V.kalshi_reference({"yes_bid": 4, "yes_ask": 54})
    ck("a 50c-wide book's mid (29c) is NOT a fair value: the ask is the one real number",
       ref["kind"] == "one_sided" and _close(ref["p"], 0.54, 1e-15))
    ck("no usable price at all -> kind none", V.kalshi_reference({})["kind"] == "none"
       and V.kalshi_reference({"yes_bid": 0, "yes_ask": 100})["kind"] == "none")

    # =====================================================================
    # C. Kalshi fee: smooth expectation and the exact, rounded order fee
    # =====================================================================
    ck("smooth fee: 1.75c at 50c, 0.63c at 10c and 90c, 0 at the sentinels, times the series multiplier",
       _close(V.fee_per_contract_c(50), 1.75, 1e-12) and _close(V.fee_per_contract_c(10), 0.63, 1e-12)
       and _close(V.fee_per_contract_c(90), 0.63, 1e-12) and V.fee_per_contract_c(0) == 0.0
       and V.fee_per_contract_c(100) == 0.0 and _close(V.fee_per_contract_c(50, 0.5), 0.875, 1e-12)
       and all(V.fee_per_contract_c(c, 1.0) == kalshi.taker_fee_cents(c) for c in (1, 7, 33, 50, 81, 99)))
    ck("exact order fee, hand-computed: 100 @ 50c = 175c, 1 @ 50c = 2c (the cent round-up), 100 @ 1c and @ 99c = 7c, 10 @ 43c = 18c, 3 @ 30c = 5c",
       V.fee_order_c(50, 100) == 175 and V.fee_order_c(50, 1) == 2 and V.fee_order_c(1, 100) == 7
       and V.fee_order_c(99, 100) == 7 and V.fee_order_c(43, 10) == 18 and V.fee_order_c(30, 3) == 5)
    ck("float-exact rounding: 4 @ 50c is EXACTLY 7c and 40 @ 50c exactly 70c (float math gives 7.000000000000001 for the first and rounds it up to 8); 8 @ 50c at multiplier 0.5 is 7c",
       V.fee_order_c(50, 4) == 7 and V.fee_order_c(50, 40) == 70 and V.fee_order_c(50, 8, 0.5) == 7)
    bad = []
    for c in range(1, 100):
        for n in (1, 2, 3, 7, 10, 25, 100, 333):
            want = _ceil_int(7 * n * c * (100 - c), 10000)               # integer cents: 0.07 C P (1-P)
            want_half = _ceil_int(7 * n * c * (100 - c), 20000)           # multiplier 0.5
            if V.fee_order_c(c, n) != want or V.fee_order_c(c, n, 0.5) != want_half:
                bad.append((c, n))
    ck("a single fill's fee is ceil(7 C c (100-c) / 10000) cents for EVERY integer price x 8 sizes, at multipliers 1 and 0.5", not bad, bad[:5])
    ck("the order fee never undercuts the smooth expectation and exceeds it by under one cent per fill",
       all(0.0 <= V.fee_order_c(c, n) - n * V.fee_per_contract_c(c) < 1.0 + 1e-9 for c in (3, 20, 50, 77) for n in (1, 5, 40)))
    ck("a direct member's grid is the centicent: 1 @ 50c costs 1.75c, not 2c",
       _close(V.fee_order_c(50, 1, precision="centicent"), 1.75, 1e-12))
    docs = V.fee_fills_c([(5.5, 1)])
    ck("Kalshi's own worked example (docs fee_rounding): -$0.055 revenue, model fee $0.00363825 -> fee + rounding fee = $0.005, rounding fee $0.001361 carried",
       _close(docs["fee_c"], 0.5, 1e-12) and _close(docs["carry_c"], 0.1361, 1e-9))
    acc = V.fee_fills_c([(50, 1)] * 3, mult=Fraction(12, 35))        # model fee exactly $0.006 -> rounding fee $0.004 a fill
    ck("the docs' accumulator table: three fills each adding $0.004 -> nets 1c, 1c, 0c and $0.002 carried forward",
       [round(x, 9) for x in acc["per_fill_c"]] == [1.0, 1.0, 0.0] and _close(acc["carry_c"], 0.2, 1e-9)
       and _close(acc["fee_c"], 2.0, 1e-12))
    sweep = [(44, 25), (45, 10)]
    ck("a two-level sweep: order rounding ceil_cent(sum of 6dp fees) = 61c, per-fill round-up = 62c",
       V.fee_fills_c(sweep)["fee_c"] == 61 and V.fee_fills_c(sweep, rounding="fill")["fee_c"] == 62)
    ck("a fee on a price that is not a price is an error", rejects(V.fee_fills_c, [(100, 1)]) and rejects(V.fee_fills_c, [(50, 0)]))
    params = V.fee_params(fee_type="quadratic", fee_multiplier=1.0)
    ck("fee_params: explicit values win and need no lookup; a non-quadratic type is flagged unknown",
       params["known"] and params["fee_multiplier"] == 1.0 and params["multiplier_source"] == "explicit"
       and not V.fee_params(fee_type="flat")["known"] and V.fee_params(lookup=False)["multiplier_source"] == "default")
    saved = dict(kalshi._series_cache)
    kalshi._series_cache["KXTEST"] = (__import__("time").time(), {"fee_type": "quadratic", "fee_multiplier": 0.5}, 3600)
    try:
        fp = V.fee_params("KXTEST")
    finally:
        kalshi._series_cache.clear()
        kalshi._series_cache.update(saved)
    ck("fee_params reads the series record through kalshi.series_info (cached): multiplier 0.5 comes back as 0.5 from 'series'",
       fp["fee_multiplier"] == 0.5 and fp["multiplier_source"] == "series" and fp["known"])

    # =====================================================================
    # D. EV, ROI, intervals, stake, labels
    # =====================================================================
    e = V.ev_buy(0.60, 50)
    ck("YES EV, hand-computed: p .60 at a 50c ask -> fee 1.75c, cost 51.75c, EV +8.25c, ROI 8.25/51.75, break-even p 0.5175",
       _close(e["fee_c"], 1.75, 1e-12) and _close(e["cost_c"], 51.75, 1e-12) and _close(e["ev_c"], 8.25, 1e-12)
       and _close(e["roi"], 8.25 / 51.75, 1e-12) and _close(e["breakeven_p"], 0.5175, 1e-12))
    e = V.ev_buy(1 - 0.60, 55)
    ck("NO EV, hand-computed: P(NO) .40 at a 55c NO ask -> fee 1.7325c, EV 40 - 56.7325 = -16.7325c",
       _close(e["fee_c"], 1.7325, 1e-12) and _close(e["ev_c"], -16.7325, 1e-12))
    ck("EV is zero exactly at the break-even probability (cost / 100) and positive just above it",
       abs(V.ev_buy(0.5175, 50)["ev_c"]) < 1e-9 and V.ev_buy(0.52, 50)["ev_c"] > 0 > V.ev_buy(0.51, 50)["ev_c"])
    ck("buying one contract pays the whole-cent fee: p .6 at 50c is +8c at contracts=1, +8.25c smooth, and +8.25c at 100",
       _close(V.ev_buy(0.6, 50, contracts=1)["ev_c"], 8.0, 1e-12) and _close(V.ev_buy(0.6, 50, contracts=100)["ev_c"], 8.25, 1e-12))
    ck("no offer (None / 0 / 100 / nan) -> None, never a 100c 'price'",
       all(V.ev_buy(0.5, x) is None for x in (None, 0, 100, 100.0, float("nan"))))
    ck("p outside [0, 1] is an error", rejects(V.ev_buy, 1.2, 50) and rejects(V.ev_buy, -0.1, 50))

    iv = V.ev_interval(0.55, 0.65, 50, "yes")
    ck("YES EV interval is [EV(p_lo), EV(p_hi)] = [3.25c, 13.25c]",
       _close(iv["lo_c"], 3.25, 1e-12) and _close(iv["hi_c"], 13.25, 1e-12))
    ivn = V.ev_interval(0.55, 0.65, 52, "no")
    ck("NO EV interval runs the other way: its bad end is the HIGH P(YES)",
       _close(ivn["lo_c"], 100 * 0.35 - (52 + V.fee_per_contract_c(52)), 1e-12)
       and _close(ivn["hi_c"], 100 * 0.45 - (52 + V.fee_per_contract_c(52)), 1e-12))
    ck("positive_at_lower_bound: true when even p_lo pays, false when only the point estimate does",
       V.positive_at_lower_bound(0.55, 0.65, 50) and not V.positive_at_lower_bound(0.50, 0.70, 50)
       and not V.positive_at_lower_bound(0.55, 0.65, None))
    ck("a point interval collapses to the point EV", _close(V.ev_interval(0.6, 0.6, 50)["lo_c"], V.ev_buy(0.6, 50)["ev_c"], 1e-12)
       and rejects(V.ev_interval, 0.7, 0.6, 50))

    st = V.stake_profile(50, 0.6, 50)
    ck("a $50 stake at 50c buys 96 contracts: cost 4800 + 168c fee = $49.68; 97 would cost $50.20; expected profit (5760 - 4968)/100 = $7.92",
       st["contracts"] == 96 and _close(st["cost_usd"], 49.68, 1e-9) and _close(st["fee_usd"], 1.68, 1e-9)
       and _close(st["expected_profit_usd"], 7.92, 1e-9) and _close(st["profit_if_win_usd"], 46.32, 1e-9)
       and _close(st["loss_if_lose_usd"], -49.68, 1e-9)
       and 97 * 50 + V.fee_order_c(50, 97) > 5000)
    ck("a stake too small for one contract, or a missing offer, returns None",
       V.stake_profile(0.3, 0.6, 50) is None and V.stake_profile(50, 0.6, None) is None)

    blk = dict(LIVE, yes_bid=48, yes_ask=50, no_bid=50, no_ask=52,
               ticker="KXNBAPTS-26OCT23NYKBOS-NYKJBRUNSON11-25", fee_type="quadratic", fee_multiplier=1.0)
    lv = LV(0.60, blk, 0.55, 0.65)
    ck("line_value: YES EV +8.25c at 50c + 1.75c, NO EV -13.7472c at 52c, edge .60 - mid .49 = .11, executable edge .0825",
       _close(lv["ev_yes"], 8.25, 1e-9) and _close(lv["ev_no"], 40 - (52 + 7 * 0.52 * 0.48), 1e-9)
       and _close(lv["p_market"], 0.49, 1e-12) and _close(lv["edge"], 0.11, 1e-12)
       and _close(lv["edge_exec"]["yes"], 0.0825, 1e-12) and lv["best_side"] == "yes"
       and lv["market_kind"] == "two_sided" and lv["price_used"] == {"yes": 50.0, "no": 52.0})
    ck("line_value carries the interval and the robust flag: YES [3.25, 13.25]c, positive at the lower bound",
       _close(lv["ev_interval"]["yes"][0], 3.25, 1e-9) and _close(lv["ev_interval"]["yes"][1], 13.25, 1e-9)
       and lv["positive_at_lower_bound"] and _close(lv["ev_lo_best"], 3.25, 1e-9) and _close(lv["roi"], 8.25 / 51.75, 1e-9)
       and lv["fee_assumed"] is False and lv["fee_type"] == "quadratic")
    ck("line_value names its units so a cent is never read as a dollar",
       lv["units"]["ev"] == "cents per contract" and lv["units"]["price_used"] == "cents" and lv["units"]["prob"] == "fraction")
    lv_no = LV(0.30, blk)
    ck("a low P(YES) makes NO the best side, priced on the NO ask (EV 70 - (52 + fee)), and the YES interval is None without a band",
       lv_no["best_side"] == "no" and _close(lv_no["ev_no"], 70 - (52 + 7 * 0.52 * 0.48), 1e-9)
       and lv_no["ev_interval"] == {"yes": None, "no": None} and lv_no["positive_at_lower_bound"] is False)
    ck("one-sided book: no NO ask is invented from nothing (NO ask = 100 - YES bid only when there IS a bid)",
       LV(0.5, {"yes_bid": None, "yes_ask": 24, "no_bid": 76, "no_ask": None})["price_used"]["no"] is None
       and LV(0.5, {"yes_bid": 40, "yes_ask": None, "no_bid": None, "no_ask": 60})["price_used"]["yes"] is None
       and LV(0.5, {"yes_bid": 40, "yes_ask": None, "no_bid": 55, "no_ask": None})["price_used"]["yes"] == 45.0)
    ck("no market at all -> every EV None, best_side None, no crash",
       LV(0.5, None)["best_side"] is None and LV(0.5, {})["ev_yes"] is None)
    ck("line_value rejects an interval that does not contain p, or half an interval",
       rejects(V.line_value, 0.5, blk, 0.55, 0.65) and rejects(V.line_value, 0.5, blk, 0.4, None))
    lv_c = LV(0.60, blk, contracts=1)
    ck("contracts=1 switches to the exact one-contract fee (2c, not 1.75c)",
       _close(lv_c["fee"]["yes"], 2.0, 1e-12) and _close(lv_c["ev_yes"], 8.0, 1e-9))
    lv_s = LV(0.60, blk, 0.55, 0.65, stake=50)
    ck("line_value with a stake adds the stake block for the best side (96 contracts, $7.92)",
       lv_s["stake"]["contracts"] == 96 and _close(lv_s["stake"]["expected_profit_usd"], 7.92, 1e-9)
       and len(lv_s["stake"]["expected_profit_interval_usd"]) == 2)
    ck("market blocks without fee info are flagged: multiplier assumed 1.0",
       LV(0.6, {"yes_bid": 48, "yes_ask": 50, "no_bid": 50, "no_ask": 52})["fee_assumed"] is True)

    lab = V.line_labels(lv, n_eff=40)
    ck("labels: p .60 is not 'high_probability' (< .70); positive EV at the ask AND at the lower bound; 40 effective games with a .10-wide band is not low evidence",
       lab == ["positive_ev", "positive_ev_robust"], lab)
    ck("labels follow LABELS' order and only ever use the five allowed names",
       V.line_labels(LV(0.80, blk, 0.75, 0.85), n_eff=40, strong_fit=True)
       == ["high_probability", "positive_ev", "positive_ev_robust", "strong_fit"])
    ck("positive_ev but not robust: the point pays, the lower bound does not",
       V.line_labels(LV(0.60, blk, 0.50, 0.70), n_eff=40) == ["positive_ev"])
    hv = LV(0.90, dict(LIVE, yes_bid=94, yes_ask=95, no_bid=5, no_ask=6), 0.86, 0.94)
    ck("high_probability is descriptive only: p .90 against a 95c ask is high_probability, NOT positive_ev (YES costs 95.33c)",
       V.line_labels(hv, side="yes", n_eff=40) == ["high_probability"] and hv["ev_yes"] < 0)
    ck("with no side given the labels follow the BEST side: here the 6c NO ask (+3.6c) -> positive_ev on NO, no high_probability",
       hv["best_side"] == "no" and V.line_labels(hv, n_eff=40) == ["positive_ev"], V.line_labels(hv, n_eff=40))
    ck("low_evidence by sample size (n_eff 12 < 20), by interval width (.40 > .25), and when neither is supplied",
       "low_evidence" in V.line_labels(lv, n_eff=12)
       and "low_evidence" in V.line_labels(LV(0.6, blk, 0.4, 0.8), n_eff=40)
       and "low_evidence" in V.line_labels(LV(0.6, blk))
       and "low_evidence" not in V.line_labels(lv, n_eff=20))
    ck("thresholds are parameters: high_p=.55 awards high_probability at p .60; min_n_eff=50 flags 40 games",
       "high_probability" in V.line_labels(lv, n_eff=40, high_p=0.55)
       and "low_evidence" in V.line_labels(lv, n_eff=40, min_n_eff=50))
    ck("strong_fit is formatted, never computed: absent / False / None -> no label; True -> label",
       "strong_fit" not in V.line_labels(lv, n_eff=40) and "strong_fit" not in V.line_labels(lv, n_eff=40, strong_fit=False)
       and "strong_fit" in V.line_labels(lv, n_eff=40, strong_fit=True))
    nolab = V.line_labels(LV(0.6, {}), n_eff=40)
    ck("no executable price -> neither EV label can be awarded", "positive_ev" not in nolab and "positive_ev_robust" not in nolab)
    seen = set()
    for p_ in (0.02, 0.3, 0.6, 0.85, 0.99):
        for width in (None, 0.1, 0.5):
            for n_eff in (None, 5, 60):
                for fit in (None, True):
                    v_ = LV(p_, blk, None if width is None else max(0.0, p_ - width / 2),
                                      None if width is None else min(1.0, p_ + width / 2))
                    seen.update(V.line_labels(v_, n_eff=n_eff, strong_fit=fit))
                    seen.update(V.line_labels(v_, side="no", n_eff=n_eff, strong_fit=fit))
                    seen.update(V.label_reasons(v_, n_eff=n_eff, strong_fit=fit).values())
    ck("the word 'lock' appears in no label and no label reason over a 90-case grid",
       all("lock" not in str(s).lower() for s in seen) and set(V.LABELS) >= {s for s in seen if s in V.LABELS}
       and all("lock" not in x for x in V.LABELS + V.QUALIFIERS))
    why = V.label_reasons(lv, n_eff=40)
    ck("label_reasons explains exactly the awarded labels, with the numbers",
       set(why) == {"positive_ev", "positive_ev_robust"} and "+8.25c" in why["positive_ev"] and "+3.25c" in why["positive_ev_robust"])
    nl = V.line_labels(LV(0.30, blk, 0.25, 0.35), side="no", n_eff=40)
    ck("a NO label rides on the NO side's own numbers: p(YES) .30 -> NO wins .70 (high_probability), EV from the NO ask",
       "high_probability" in nl and "positive_ev" in nl)
    cm = V.combo_ev(0.30, 0.20)
    ck("combo_ev goes through combo_engine.combo_cost: cost 0.2 x (1 + 0.07 x 0.8) = 0.2112, EV = 0.30/0.2112 - 1",
       _close(cm["cost"], 0.2112, 1e-12) and _close(cm["cost"], combo_engine.combo_cost(0.20), 1e-15)
       and _close(cm["ev"], 0.30 / 0.2112 - 1, 1e-12) and V.combo_ev(0.3, 1.0) is None)

    pe_y, pe_n = V.refund_adjusted_sides(0.30, 0.05, 0.20)
    ck("refund-adjusted payoffs: P(YES) .30, P(refund) .05 at fair price .20 -> YES .30 + .05 x .20 = .31, NO .65 + .05 x .80 = .69, and the two sum to 1",
       _close(pe_y, 0.31, 1e-12) and _close(pe_n, 0.69, 1e-12) and _close(pe_y + pe_n, 1.0, 1e-12)
       and V.refund_adjusted_sides(0.4, 0.0, 0.5) == (0.4, 0.6) and rejects(V.refund_adjusted_sides, 0.7, 0.4, 0.5))
    ck("a refund moves EV: YES at 50c with p_win .31 vs .30 differs by exactly 1c (100 x .01)",
       _close(V.ev_buy(pe_y, 50)["ev_c"] - V.ev_buy(0.30, 50)["ev_c"], 1.0, 1e-9))
    import json as _json
    ck("line_value and line_labels are plain JSON (no Fraction, no nan) so they drop into the payload as they are",
       len(_json.dumps({"value": lv_s, "labels": V.line_labels(lv_s, n_eff=40), "why": V.label_reasons(lv_s, n_eff=40),
                        "walk": V.walk_book(V.asks_from_orderbook(ORDERBOOK_DOCS, "yes"), 30, 0.6)}, allow_nan=False)) > 100)

    # ---- the book ---------------------------------------------------------
    asks = V.asks_from_orderbook(ORDERBOOK_DOCS, "yes")
    ck("YES asks are the NO bids flipped (100 - bid), cheapest first: docs example best ask 44c x 17, then 55c x 20",
       asks[0] == (44.0, 17.0) and asks[1] == (55.0, 20.0) and asks[2] == (56.0, 29.0) and len(asks) == 10)
    na = V.asks_from_orderbook(ORDERBOOK_DOCS, "no")
    ck("NO asks are the YES bids flipped: docs example best NO ask 100 - 42 = 58c x 13 (docs: 'NO spread = best NO ask - best NO bid')",
       na[0] == (58.0, 13.0) and na[1] == (59.0, 10.0))
    legacy = {"orderbook": {"yes": [[40, 7], [42, 3]], "no": [[55, 9], [56, 4]]}}
    ck("the legacy cents payload reads the same", V.asks_from_orderbook(legacy, "yes") == [(44.0, 4.0), (45.0, 9.0)])
    wb = V.walk_book(asks, 30, 0.60)
    ck("walking the book for 30: 17 @ 44c then 13 @ 55c, each level at ITS price (principal 1463c); fee ceil_cent(.293216 + .225225) = 52c, not the 53c of per-fill round-ups",
       [(f["price_c"], f["qty"]) for f in wb["fills"]] == [(44.0, 17.0), (55.0, 13.0)]
       and wb["principal_c"] == 1463 and wb["fee_c"] == 52 and wb["filled"] == 30 and wb["shortfall"] == 0
       and V.walk_book(asks, 30, 0.60, rounding="fill")["fee_c"] == 53)
    ck("walk_book EV = 100 p filled - (principal + fee); avg price 48.77c; marginal level 55c",
       _close(wb["ev_c"], 100 * 0.6 * 30 - (1463 + 52), 1e-9) and _close(wb["avg_price_c"], 1463 / 30, 1e-12)
       and wb["marginal"]["price_c"] == 55.0 and _close(wb["roi"], wb["ev_c"] / wb["cost_c"], 1e-12))
    ck("a size larger than the book fills what exists and reports the shortfall; an empty book fills nothing",
       V.walk_book([(44, 5)], 20, 0.6)["filled"] == 5 and V.walk_book([(44, 5)], 20, 0.6)["shortfall"] == 15
       and V.walk_book([], 5, 0.6)["filled"] == 0 and V.walk_book([], 5, 0.6)["roi"] is None)
    ck("walking one contract equals ev_buy at contracts=1 (the 44c ask, exact fee)",
       _close(V.walk_book(asks, 1, 0.60)["ev_c"], V.ev_buy(0.60, 44, contracts=1)["ev_c"], 1e-9))
    mx = V.max_size_positive_ev(asks, 0.60)
    ck("max +EV size at p .60: the 44c (cost 45.7c), 55c (56.7c) and 56c (57.7c) levels pay; the 62c level (63.7c) does not -> 66 contracts",
       mx["contracts"] == 17 + 20 + 29 and mx["levels"] == 3 and mx["limit_price_c"] == 56.0
       and V.max_size_positive_ev(asks, 0.30)["contracts"] == 0)
    live_asks = V.asks_from_orderbook(ORDERBOOK_LIVE["orderbook"], "yes")
    ck("the REAL Jalen Williams 6+ assists book: best YES ask 36c x 1413 (= 100 - the 64c NO bid), prices strictly rising",
       live_asks[0] == (36.0, 1413.0) and all(a[0] < b[0] for a, b in zip(live_asks, live_asks[1:])))

    # =====================================================================
    # E. prop_dist: special functions, families, direct-summation checks
    # =====================================================================
    ck("incomplete gamma: P(1, x) = 1 - e^-x; P(1/2, x) = erf(sqrt x); P + Q = 1 on both branches",
       all(_close(D.gammainc_pq(1.0, x)[0], 1 - math.exp(-x), 1e-14) for x in (0.1, 1.0, 3.0, 9.0))
       and all(_close(D.gammainc_pq(0.5, x)[0], math.erf(math.sqrt(x)), 1e-13) for x in (0.2, 1.5, 6.0))
       and all(_close(sum(D.gammainc_pq(a, x)), 1.0, 1e-14) for a in (2.5, 30.0, 300.0) for x in (1.0, 29.0, 31.0, 400.0)))
    ck("incomplete beta: I_x(a, 1) = x^a; I_x(1, b) = 1 - (1-x)^b; I_.5(a, a) = .5; symmetric I_x(a,b) = 1 - I_{1-x}(b,a)",
       all(_close(D.betainc_reg(a, 1.0, x), x ** a, 1e-13) for a in (0.5, 2.0, 7.0) for x in (0.1, 0.5, 0.9))
       and all(_close(D.betainc_reg(1.0, b, x), 1 - (1 - x) ** b, 1e-13) for b in (0.5, 3.0, 12.0) for x in (0.1, 0.5, 0.9))
       and all(_close(D.betainc_reg(a, a, 0.5), 0.5, 1e-13) for a in (0.7, 4.0, 40.0))
       and _close(D.betainc_reg(3.5, 8.0, 0.3), 1 - D.betainc_reg(8.0, 3.5, 0.7), 1e-13))

    def poisson_pmfs(mu, n):
        p = math.exp(-mu)
        out = [p]
        for k in range(1, n):
            p *= mu / k
            out.append(p)
        return out

    def negbin_pmfs(mu, alpha, n):
        r = 1.0 / alpha
        p = (r / (r + mu)) ** r
        out = [p]
        for k in range(n - 1):
            p *= (k + r) / (k + 1) * (mu / (r + mu))
            out.append(p)
        return out

    def sf_direct(pm, k):
        return sum(pm[k:]) if k > 0 else 1.0
    worst = 0.0
    for mu in (0.3, 2.5, 12.0, 40.0):
        pm = poisson_pmfs(mu, 600)
        d = D.Poisson(mu)
        for k in (0, 1, 2, 5, 10, 30, 60):
            worst = max(worst, abs(d.sf_ge(k) - sf_direct(pm, k)))
    ck("Poisson P(X >= k) equals the sum of the recurrence pmf for 4 means x 7 thresholds (max error < 1e-13)", worst < 1e-13, worst)
    worst = 0.0
    for mu, al in ((2.5, 0.3), (12.0, 0.05), (25.0, 0.2), (0.8, 1.5), (6.0, 0.01)):
        pm = negbin_pmfs(mu, al, 1500)
        d = D.NegBin(mu, al)
        for k in (0, 1, 2, 5, 10, 30, 60):
            worst = max(worst, abs(d.sf_ge(k) - sf_direct(pm, k)))
    ck("NegBin P(X >= k) = I_{mu/(r+mu)}(k, r) equals the sum of the independent-recurrence pmf for 5 (mean, alpha) x 7 thresholds (< 1e-11)", worst < 1e-11, worst)
    nb = D.NegBin(9.0, 0.4)
    pm = negbin_pmfs(9.0, 0.4, 2000)
    m1 = sum(k * p for k, p in enumerate(pm))
    v1 = sum(k * k * p for k, p in enumerate(pm)) - m1 * m1
    ck("NegBin's variance is mu + alpha mu^2 by the pmf: 9 + 0.4 x 81 = 41.4 (and the mean is 9)",
       _close(m1, 9.0, 1e-8) and _close(v1, 41.4, 1e-5) and _close(nb.var(), 41.4, 1e-12))
    ck("alpha -> 0 is a Poisson: NegBin(6, 0) and NegBin(6, 1e-9) give the Poisson tail",
       all(_close(D.NegBin(6.0, a).sf_ge(k), D.Poisson(6.0).sf_ge(k), 1e-12) for a in (0.0, 1e-9) for k in (1, 6, 12)))
    zp = D.ZeroInflated(D.Poisson(5.0), 0.2)
    pm = poisson_pmfs(5.0, 200)
    ck("zero-inflated Poisson: P(X >= k) = (1 - pi) P_base(X >= k) for k >= 1, 1 at k <= 0; pmf(0) = pi + (1 - pi) e^-5; mean (1 - pi) mu",
       all(_close(zp.sf_ge(k), 0.8 * sf_direct(pm, k), 1e-13) for k in (1, 3, 7, 12)) and zp.sf_ge(0) == 1.0
       and _close(zp.pmf(0), 0.2 + 0.8 * math.exp(-5), 1e-14) and _close(zp.mean(), 4.0, 1e-12)
       and _close(sum(zp.pmf(k) for k in range(200)), 1.0, 1e-12))
    zn = D.ZeroInflated.from_overall_mean("negbin", 3.0, 0.25, alpha=0.3)
    pm = negbin_pmfs(4.0, 0.3, 1500)
    ck("zero-inflated NegBin from an OVERALL mean 3.0 at pi .25 has base mean 4.0; its tail is .75 x the NegBin(4, .3) tail; mean 3.0",
       _close(zn.base.mu, 4.0, 1e-12) and _close(zn.mean(), 3.0, 1e-12)
       and all(_close(zn.sf_ge(k), 0.75 * sf_direct(pm, k), 1e-11) for k in (1, 4, 9)))
    vz = sum(k * k * zn.pmf(k) for k in range(1500)) - zn.mean() ** 2
    ck("zero-inflated variance formula matches the pmf second moment", _close(zn.var(), vz, 1e-6), (zn.var(), vz))
    nc = D.NormalCC(25.0, 6.0, lower=None)
    z = lambda k: (k - 0.5 - 25.0) / 6.0   # noqa: E731
    ck("discretised normal (lower=None, the pure latent discretisation): P(X >= 25) = P(N >= 24.5) = 1 - Phi(-1/12); continuity correction at k = 30, 10",
       all(_close(nc.sf_ge(k), 1 - 0.5 * (1 + math.erf(z(k) / math.sqrt(2))), 1e-13) for k in (10, 25, 30, 40)))
    nt = D.NormalCC(2.0, 3.0, lower=0)
    tot = sum(nt.pmf(k) for k in range(0, 80))
    ck("a normal truncated at 0: P(X >= 0) = 1 and the pmf sums to 1 on k >= 0",
       nt.sf_ge(0) == 1.0 and _close(tot, 1.0, 1e-12) and nt.sf_ge(1) < 1.0)

    def simpson(f, a, b, n=20000):
        h = (b - a) / n
        s = f(a) + f(b)
        for i in range(1, n):
            s += (4 if i % 2 else 2) * f(a + i * h)
        return s * h / 3

    g = D.Yards(0.15, 45.0, 30.0)
    a_, sc_ = (45.0 / 30.0) ** 2, 30.0 ** 2 / 45.0
    gpdf = lambda x: x ** (a_ - 1) * math.exp(-x / sc_) / (math.gamma(a_) * sc_ ** a_)   # noqa: E731
    worst = max(abs(g.sf_ge(k) - 0.85 * simpson(gpdf, k - 0.5, 45 + 40 * 30.0)) for k in (1, 10, 50, 100, 150))
    ck("yards (gamma): P(Y >= k) = (1 - p0) x integral of the gamma density from k - 0.5, by Simpson's rule (< 1e-7)", worst < 1e-7, worst)
    ln = D.Yards(0.15, 45.0, 30.0, "lognormal")
    s2 = math.log1p((30.0 / 45.0) ** 2)
    mu_l = math.log(45.0) - s2 / 2
    lpdf = lambda x: math.exp(-(math.log(x) - mu_l) ** 2 / (2 * s2)) / (x * math.sqrt(2 * math.pi * s2))   # noqa: E731
    worst = max(abs(ln.sf_ge(k) - 0.85 * simpson(lpdf, k - 0.5, 45 + 60 * 30.0)) for k in (1, 10, 50, 100, 150))
    ck("yards (lognormal): the same check against the lognormal density (< 1e-7)", worst < 1e-7, worst)
    ck("yards has a point mass at 0: P(Y >= 0) = 1, P(Y >= 1) <= 1 - p0, pmf sums to 1, and no-catch probability is >= p0",
       g.sf_ge(0) == 1.0 and g.sf_ge(1) <= 0.85 and _close(sum(g.pmf(k) for k in range(0, 2500)), 1.0, 1e-9) and g.pmf(0) >= 0.15)
    yf = D.yards_from_moments(60.0, 55.0, 0.2)
    ck("yards_from_moments recovers the overall mean and sd of the mixture it was fitted to",
       _close(yf.mean(), 60.0, 1e-9) and _close(math.sqrt(yf.var()), 55.0, 1e-9) and yf.p0 == 0.2 and _close(yf.mean_pos, 75.0, 1e-9))
    ck("inconsistent yards moments (sd too small for the zero rate and mean) are an error, not a clamp",
       rejects(D.yards_from_moments, 60.0, 5.0, 0.5) and rejects(D.yards_from_moments, -1.0, 5.0, 0.1))
    ck("every family reports itself for the payload's dist field", D.Poisson(3).to_dict() == {"family": "poisson", "params": {"mu": 3.0}}
       and D.NegBin(3, 0.2).to_dict()["family"] == "negbin" and zp.to_dict()["family"] == "zi_poisson"
       and zp.to_dict()["params"]["pi"] == 0.2 and g.to_dict()["family"] == "yards_gamma")
    ck("bad family parameters are errors", rejects(D.Poisson, -1) and rejects(D.NegBin, 3, -0.1)
       and rejects(D.ZeroInflated, D.Poisson(3), 1.0) and rejects(D.NormalCC, 3, 0) and rejects(D.Yards, 0.1, 0, 1))

    # ---- monotonicity ------------------------------------------------------
    ck("PAVA pools a violation: [.50, .60, .30, .35] at k = 10,20,30,40 -> [.55, .55, .325, .325]",
       all(_close(a, b, 1e-12) for a, b in zip(D.enforce_monotone([10, 20, 30, 40], [0.5, 0.6, 0.3, 0.35]), [0.55, 0.55, 0.325, 0.325])))
    ck("cummin never raises a rung: same input -> [.50, .50, .30, .30]",
       D.enforce_monotone([10, 20, 30, 40], [0.5, 0.6, 0.3, 0.35], "cummin") == [0.5, 0.5, 0.3, 0.3])
    ck("out-of-range inputs are clamped to [0, 1] first, then pooled: k = 30, 10, 20 with p = .3, 1.2, -0.1 -> 1.0 at k10, then (0, .3) pooled to .15 -> [.15, 1.0, .15] in INPUT order",
       all(_close(a, b, 1e-12) for a, b in zip(D.enforce_monotone([30, 10, 20], [0.3, 1.2, -0.1]), [0.15, 1.0, 0.15])))
    mono = D.enforce_monotone([30, 10, 20], [0.2, 0.9, 0.5])
    ck("an already-monotone ladder is returned unchanged, in input order", mono == [0.2, 0.9, 0.5] and D.is_monotone([30, 10, 20], mono))
    ck("is_monotone catches a rise", not D.is_monotone([1, 2, 3], [0.5, 0.6, 0.4]))
    lad = D.NegBin(21.0, 0.08).ladder(range(0, 60, 5))
    ck("a family's ladder over 12 thresholds is non-increasing and inside [0, 1]",
       D.is_monotone(list(lad), list(lad.values()), 0.0) and all(0.0 <= p <= 1.0 for p in lad.values()))
    band = D.interval_over_mean(lambda m: D.NegBin(m, 0.1), 18.0, 24.0, [15, 20, 25])
    ck("interval_over_mean: P(X >= k) at the mean's two ends, min/max ordered, widest where the tail is steepest",
       all(lo < hi for lo, hi in band.values()) and _close(band[20][0], D.NegBin(18.0, 0.1).sf_ge(20), 1e-12)
       and _close(band[20][1], D.NegBin(24.0, 0.1).sf_ge(20), 1e-12))

    # ---- sampling table ----------------------------------------------------
    tab = D.NegBin(8.0, 0.2).cdf_table()
    ck("cdf_table is non-decreasing, ends at exactly 1.0, and the mass beyond its last index (P(X >= len)) is below 1e-12",
       all(b >= a for a, b in zip(tab, tab[1:])) and tab[-1] == 1.0 and D.NegBin(8.0, 0.2).sf_ge(len(tab)) < 1e-12)

    # ---- fitting -----------------------------------------------------------
    f = D.negbin_fit_mom([0, 2, 4, 6], min_n=3)
    ck("method of moments on [0, 2, 4, 6]: mean 3, unbiased variance 20/3, alpha = (20/3 - 3)/9",
       f["ok"] and _close(f["mu"], 3.0, 1e-12) and _close(f["var"], 20 / 3, 1e-12) and _close(f["alpha"], (20 / 3 - 3) / 9, 1e-12)
       and f["method"] == "mom", f)
    ck("an underdispersed sample (variance <= mean) gives alpha 0.0 = Poisson and says so, never a negative alpha",
       D.negbin_fit_mom([3, 3, 4, 4, 3, 4])["alpha"] == 0.0 and D.negbin_fit_mom([3, 3, 4, 4, 3, 4])["underdispersed"])
    ck("weights: a repeated value is a weight (reliability-weighted mean and variance match the expanded sample)",
       _close(D.negbin_fit_mom([1, 5, 9], [1, 2, 3], min_n=3)["mu"], (1 + 10 + 27) / 6, 1e-12)
       and _close(D.negbin_fit_mom([1, 5, 5, 9, 9, 9], min_n=3)["mu"], (1 + 10 + 27) / 6, 1e-12))
    samp = [3, 5, 2, 8, 0, 4, 6, 12, 1, 3, 2, 5]
    fm = D.negbin_fit_mle(samp)
    grid = max((D.negbin_loglik(samp, fm["mu"], a / 10000.0), a / 10000.0) for a in range(10, 30000, 5))
    ck("MLE dispersion equals the grid argmax of the NegBin log-likelihood (0.3268 vs grid step 0.0005) and its loglik is the maximum",
       abs(fm["alpha"] - grid[1]) <= 0.0006 and fm["loglik"] >= grid[0] - 1e-6 and fm["loglik"] >= D.negbin_loglik(samp, fm["mu"], 0.0) - 1e-9, (fm, grid))
    rng = random.Random(7)
    tbl = D.NegBin(6.0, 0.25).cdf_table()
    import bisect
    big = [bisect.bisect_left(tbl, rng.random()) for _ in range(4000)]
    fm2, fo2 = D.negbin_fit_mle(big), D.negbin_fit_mom(big)
    ck("on 4,000 seeded NegBin(6, 0.25) draws both fitters recover alpha within 0.04 and the mean within 0.15",
       abs(fm2["alpha"] - 0.25) < 0.04 and abs(fo2["alpha"] - 0.25) < 0.04 and abs(fm2["mu"] - 6.0) < 0.15, (fm2["alpha"], fo2["alpha"]))
    pois = [bisect.bisect_left(D.Poisson(6.0).cdf_table(), rng.random()) for _ in range(600)]
    ck("a genuinely Poisson sample gives a dispersion indistinguishable from 0 (< 0.05)",
       D.negbin_fit_mle(pois)["alpha"] < 0.05 and D.negbin_fit_mom(pois)["alpha"] < 0.05)
    degenerate = {"too few": [1, 2, 3], "all zeros": [0] * 8, "constant": [4] * 8, "negative": [1, 2, -1, 3, 4, 5],
                  "non-finite": [1, 2, float("nan"), 3, 4, 5]}
    ck("degenerate samples (too few, all zeros, constant, negative, nan) return ok False with a reason and no alpha, from BOTH fitters",
       all((not D.negbin_fit_mle(v)["ok"]) and D.negbin_fit_mle(v)["alpha"] is None and D.negbin_fit_mle(v)["reason"]
           and (not D.negbin_fit_mom(v)["ok"]) and D.negbin_fit_mom(v)["alpha"] is None for v in degenerate.values()),
       {k: D.negbin_fit_mle(v)["reason"] for k, v in degenerate.items()})
    ck("MLE refuses non-integer counts (it would be fitting something else)", not D.negbin_fit_mle([1, 2.5, 3, 4, 2, 6])["ok"])
    wide = D.negbin_fit_mle([0, 0, 0, 0, 0, 0, 0, 0, 0, 60])
    ck("an extremely overdispersed sample lands on the alpha_max bound and says so", wide["ok"] and wide["at_bound"] == "alpha_max" and wide["alpha"] == 25.0)
    ck("shrink_alpha: (n_eff a + k prior) / (n_eff + k); no player estimate -> the prior",
       _close(D.shrink_alpha(0.5, 10, 0.2, 30), (10 * 0.5 + 30 * 0.2) / 40, 1e-12) and D.shrink_alpha(None, 0, 0.2) == 0.2)

    # ---- copula ------------------------------------------------------------
    marg = [D.Poisson(4.0), D.Poisson(6.0), D.Poisson(3.0)]
    n_mc = 20000
    dr = D.copula_draws(marg, None, n=n_mc, seed=11)
    ck("copula draws are reproducible with a seed and differ across seeds",
       dr == D.copula_draws(marg, None, n=n_mc, seed=11) and dr != D.copula_draws(marg, None, n=n_mc, seed=12))
    res = D.sum_threshold_probs(dr, [8, 13, 18, 22])
    pois13 = D.Poisson(13.0)
    ck("independent Poisson(4)+Poisson(6)+Poisson(3) sum: MC P(S >= k) matches the exact Poisson(13) tail within 4.5 standard errors at 4 thresholds",
       all(abs(res[k]["p"] - pois13.sf_ge(k)) <= 4.5 * res[k]["se"] + 1e-12 for k in res), {k: (res[k]["p"], pois13.sf_ge(k), res[k]["se"]) for k in res})
    ck("the reported SE is the binomial sqrt(p (1-p) / n) of the simulation",
       all(_close(res[k]["se"], math.sqrt(res[k]["p"] * (1 - res[k]["p"]) / n_mc), 1e-12) for k in res) and res[13]["n"] == n_mc)
    zero = D.sum_threshold_probs(dr, [200])[200]
    ck("a never-hit threshold has p 0 but a non-zero SE (it does not claim certainty from a finite sample)", zero["p"] == 0.0 and zero["se"] > 0)
    com = D.copula_draws([D.NegBin(5.0, 0.3), D.NegBin(5.0, 0.3)], [[1.0, 1.0], [1.0, 1.0]], n=n_mc, seed=5)
    cres = D.sum_threshold_probs(com, [6, 10, 15])
    nb5 = D.NegBin(5.0, 0.3)
    ck("perfectly correlated identical marginals (rho = 1, a singular matrix): X1 == X2 in every draw, so S = 2X and P(S >= k) = P(X >= ceil(k/2)) within 4.5 SE",
       all(a == b for a, b in com) and all(abs(cres[k]["p"] - nb5.sf_ge(math.ceil(k / 2))) <= 4.5 * cres[k]["se"] for k in cres))
    hi_rho = D.copula_sum_probs(marg, [[1, .6, .6], [.6, 1, .6], [.6, .6, 1]], [22], n=n_mc, seed=11)[22]
    ck("positive correlation fattens the sum's upper tail: rho .6 -> P(S >= 22) well above the independent value",
       hi_rho["p"] - res[22]["p"] > 3 * (hi_rho["se"] + res[22]["se"]), (hi_rho, res[22]))
    jp = D.joint_threshold_prob(dr, [5, 7, None])
    ck("joint P(X1 >= 5 and X2 >= 7) of independent marginals is the product of the tails within 4.5 SE",
       abs(jp["p"] - D.Poisson(4.0).sf_ge(5) * D.Poisson(6.0).sf_ge(7)) <= 4.5 * jp["se"])
    ck("an invalid correlation matrix is rejected: not PSD, asymmetric, wrong size, non-unit diagonal",
       rejects(D.copula_draws, marg, [[1, .9, -.9], [.9, 1, .9], [-.9, .9, 1]], 10)
       and rejects(D.copula_draws, marg, [[1, .5, 0], [.4, 1, 0], [0, 0, 1]], 10)
       and rejects(D.copula_draws, marg, [[1, 0], [0, 1]], 10)
       and rejects(D.copula_draws, marg, [[2, 0, 0], [0, 1, 0], [0, 0, 1]], 10))
    wsum = D.sum_threshold_probs(dr, [10], weights=[1, 0, 0])[10]
    ck("weights (1, 0, 0) make the 'sum' the first stat alone: matches Poisson(4)'s tail at 10 within 4.5 SE",
       abs(wsum["p"] - D.Poisson(4.0).sf_ge(10)) <= 4.5 * wsum["se"] + 1e-12)

    # ---- calibration ------------------------------------------------------
    ps_, ys_ = [0.9, 0.2, 0.6], [1, 0, 1]
    ck("Brier = ((.1)^2 + (.2)^2 + (.4)^2) / 3 = 0.07; log loss = -(ln .9 + ln .8 + ln .6) / 3",
       _close(D.brier(ps_, ys_), 0.07, 1e-15) and _close(D.log_loss(ps_, ys_), -(math.log(0.9) + math.log(0.8) + math.log(0.6)) / 3, 1e-15))
    ck("log loss clips: a confident miss costs -ln(1e-12) = 27.63, not infinity; always-0.5 scores ln 2 and Brier .25",
       _close(D.log_loss([0.0], [1]), -math.log(1e-12), 1e-9) and _close(D.log_loss([0.5, 0.5], [1, 0]), math.log(2), 1e-15)
       and _close(D.brier([0.5, 0.5], [1, 0]), 0.25, 1e-15))
    rt = D.reliability_table([0.1, 0.15, 0.8, 0.85, 0.9, 1.0], [0, 1, 1, 1, 0, 1], bins=2)
    ck("reliability table (2 uniform bins): low bin n=2 mean_p .125 freq .5, high bin n=4 mean_p .8875 freq .75; p = 1.0 falls in the last bin",
       len(rt) == 2 and rt[0]["n"] == 2 and _close(rt[0]["mean_p"], 0.125, 1e-12) and rt[0]["freq"] == 0.5
       and rt[1]["n"] == 4 and _close(rt[1]["mean_p"], 0.8875, 1e-12) and rt[1]["freq"] == 0.75 and _close(rt[1]["gap"], -0.1375, 1e-12))
    ck("ECE = sum (n_b / N) |freq - mean_p| = (2 x .375 + 4 x .1375) / 6",
       _close(D.ece([0.1, 0.15, 0.8, 0.85, 0.9, 1.0], [0, 1, 1, 1, 0, 1], bins=2), (2 * 0.375 + 4 * 0.1375) / 6, 1e-12))
    r2 = random.Random(3)
    ps_big = [r2.random() for _ in range(20000)]
    ys_big = [1 if r2.random() < p else 0 for p in ps_big]
    ck("probabilities that ARE the truth are calibrated: ECE < 0.02 on 20,000 draws, Brier skill > 0",
       D.ece(ps_big, ys_big) < 0.02 and D.brier_skill(ps_big, ys_big) > 0)
    ck("quantile bins hold equal counts", [r["n"] for r in D.reliability_table(ps_big[:1000], ys_big[:1000], bins=10, strategy="quantile")] == [100] * 10)
    ck("calibration inputs are validated", rejects(D.brier, [1.2], [1]) and rejects(D.brier, [0.5], [2]) and rejects(D.brier, [0.5], [1, 0])
       and rejects(D.reliability_table, [0.5], [1], 0))
    lo_, hi_ = D.wilson_interval(8, 10)
    ck("Wilson 95% interval for 8/10 is (0.4902, 0.9433); no data is the full interval; 0/n and n/n stay inside [0, 1]",
       _close(lo_, 0.4902, 5e-5) and _close(hi_, 0.9433, 5e-5) and D.wilson_interval(0, 0) == (0.0, 1.0)
       and D.wilson_interval(0, 20)[0] == 0.0 and D.wilson_interval(20, 20)[1] == 1.0)

    # =====================================================================
    # F. kalshi_nba_props
    # =====================================================================
    tk = K.parse_market_ticker("KXNBAPTS-26OCT23NOPUTA-UTAJJACKSON20-25")
    ck("the brief's ticker: points, 2026-10-23, NOP @ UTA (away first), UTA's player code JJACKSON20, jersey 20, threshold 25",
       tk == {"series": "KXNBAPTS", "date": "2026-10-23", "away": "NOP", "home": "UTA", "key": "26OCT23NOPUTA", "team": "UTA",
              "player_code": "JJACKSON20", "name_token": "JJACKSON", "jersey": 20, "k": 25}, tk)
    t2 = K.parse_market_ticker("KXNBA3PT-26OCT23DETMIA-DETIJOESF-3")
    ck("a player code with NO jersey (DETIJOESF) parses: team DET, code IJOESF, jersey None, k 3",
       t2["team"] == "DET" and t2["player_code"] == "IJOESF" and t2["jersey"] is None and t2["k"] == 3)
    t3 = K.parse_market_ticker("KXNBA2D-26OCT23PORSAC-SACPACHIUWA9")
    ck("a yes/no event (double double) has no threshold", t3["k"] is None and t3["jersey"] == 9 and t3["team"] == "SAC")
    ck("season-long and malformed tickers are not game props",
       K.parse_market_ticker("KXNBAONE3PT-27-WKESSLER14") is None and K.parse_market_ticker("KXNFLREC-26OCT25ATLCAR-ATLKPITTS8-5") is None
       and K.parse_market_ticker("KXNBAPTS-26XXX23NOPUTA-UTAJJACKSON20-25") is None
       and K.parse_market_ticker("KXNBAPTS-26OCT23NOPUTA-LALJJACKSON20-25") is None and K.parse_market_ticker(None) is None)
    ck("event tickers parse: the suffix is shared by every series of a game",
       K.parse_event_ticker("KXNBAREB-26OCT23NOPUTA")["key"] == "26OCT23NOPUTA" and K.parse_event_ticker("KXNBAREB-26OCT23NOPUTA")["away"] == "NOP")
    ck("titles: '25+ points', '30+ points, rebounds, and assists', '3+ steals and blocks', 'Double Double'; accents survive",
       K.parse_title("Jaren Jackson Jr.: 25+ points") == {"name": "Jaren Jackson Jr.", "k": 25, "stat_text": "points", "event": None}
       and K.parse_title("Luka Dončić: 40+ points, rebounds, and assists")["k"] == 40
       and K.parse_title("Peyton Watson: 3+ steals and blocks")["stat_text"] == "steals and blocks"
       and K.parse_title("Precious Achiuwa: Double Double")["event"] == "Double Double" and K.parse_title("no colon here") is None)
    ck("name keys: accents, Jr. and dots fold; P.J. and PJ meet only on the squashed key",
       K.norm_name("Luka Dončić") == "luka doncic" and K.norm_name("Jaren Jackson Jr.") == "jaren jackson"
       and K.norm_name("P.J. Washington") != K.norm_name("PJ Washington")
       and K.squash_name("P.J. Washington") == K.squash_name("PJ Washington") == "pjwashington")
    recs = [K.parse_market(m, 1761181000.0, "quadratic", 1.0) for m in RAW_MARKETS]
    ck("all 16 real fixture markets parse", all(recs) and len(recs) == 16)
    ck("PINNED DEFINITION: the threshold k is floor_strike + 0.5 on every threshold fixture, and equals the ticker's own suffix",
       all(r["k"] == int(round(m["floor_strike"] + 0.5)) == K.parse_market_ticker(m["ticker"])["k"]
           for r, m in zip(recs, RAW_MARKETS) if r["kind"] == "over"))
    by = {r["ticker"]: r for r in recs}
    pr, st_ = by["KXNBAPTSPROT-26OCT23NOPUTA-UTAJJACKSON20-25"], by["KXNBAPTS-26OCT23NOPUTA-UTAJJACKSON20-25"]
    ck("the PROTECTED series is flagged and kept distinct from the standard one with the same title; both read stat pts",
       pr["protected"] and not st_["protected"] and pr["stat"] == st_["stat"] == "pts" and pr["k"] == st_["k"] == 25
       and K.SERIES["KXNBAPTSPROT"]["base"] == "KXNBAPTS" and pr["ticker"] != st_["ticker"])
    ck("record fields: player, norm key, team/opp, ET date, stat parts; PRA is points + rebounds + assists; steals+blocks is 'stl_blk'",
       st_["player"] == "Jaren Jackson Jr." and st_["player_norm"] == "jaren jackson" and st_["team"] == "UTA" and st_["opp"] == "NOP"
       and st_["date"] == "2026-10-23" and by["KXNBAPRA-26OCT23LACLAL-LALLDONCIC77-40"]["parts"] == ("pts", "reb", "ast")
       and by["KXNBASTOCK-26OCT22CLEPHI-CLEPWATSON8-3"]["stat"] == "stl_blk" and by["KXNBAPRA-26OCT23LACLAL-LALLDONCIC77-40"]["player_norm"] == "luka doncic")
    ck("yes/no events: dd and td3, no threshold",
       by["KXNBA2D-26OCT23PORSAC-SACPACHIUWA9"]["stat"] == "dd" and by["KXNBA3D-26OCT23PORSAC-SACDSABONIS11"]["stat"] == "td3"
       and by["KXNBA2D-26OCT23PORSAC-SACPACHIUWA9"]["k"] is None)
    ck("the tip estimate is occurrence - 3 h (labelled an estimate): NOP@UTA occurrence 04:30Z -> 01:30Z, which is ESPN's tip",
       st_["start_est_ts"] == st_["occurrence_ts"] - 10800
       and st_["start_est_ts"] == K.kalshi._parse_time("2026-10-24T01:30:00Z"))
    b0 = st_["market"]
    ck("market block, as the payload contract names it: ticker, 4 prices in cents, ts, volume, oi, fee_type",
       {"ticker", "yes_bid", "yes_ask", "no_bid", "no_ask", "ts", "volume", "oi", "fee_type"} <= set(b0)
       and b0["yes_bid"] == 2.0 and b0["yes_ask"] == 68.0 and b0["no_bid"] == 32.0 and b0["no_ask"] == 98.0
       and b0["ts"] == 1761181000.0 and b0["fee_type"] == "quadratic" and b0["fee_multiplier"] == 1.0 and b0["book"] == "two_sided")
    one = by["KXNBAPTS-26OCT22CLEPHI-CLEDMITCHELL45-45"]["market"]
    ck("sentinels become None: an ask-only book (bid 0.0000, NO ask 1.0000) has no yes_bid and no no_ask; 'ask_only'",
       one["yes_bid"] is None and one["no_ask"] is None and one["yes_ask"] == 24.0 and one["no_bid"] == 76.0 and one["book"] == "ask_only")
    bo = by["KXNBAPTS-26OCT22CLEPHI-PHILJAMES23-10"]["market"]
    ck("a bid-only book (ask 1.0000) is 'bid_only'; volume and open interest are floats from the *_fp strings",
       bo["yes_ask"] is None and bo["yes_bid"] == 99.0 and bo["book"] == "bid_only" and bo["volume"] == 131402.7 and bo["oi"] == 125297.91)
    ck("NO-side depth is the YES book's mirrored (Kalshi publishes no no_*_size_fp)",
       b0["no_ask_size"] == b0["yes_bid_size"] == 4.0 and b0["no_bid_size"] == b0["yes_ask_size"] == 1.0)
    legacy_m = {"ticker": "KXNBAPTS-26OCT23NOPUTA-UTAJJACKSON20-25", "yes_bid": 41, "yes_ask": 44, "no_bid": 56, "no_ask": 59,
                "last_price": 43}
    lb = K.market_block(legacy_m, 1.0)
    ck("the legacy integer-cents payload parses too (no *_dollars fields); a dollar field wins when both exist",
       lb["yes_bid"] == 41.0 and lb["yes_ask"] == 44.0 and lb["last"] == 43.0
       and K.market_block(dict(legacy_m, yes_ask_dollars="0.4500"), 1.0)["yes_ask"] == 45.0)
    ck("a market that is not a game prop, or has a title/threshold disagreement, is skipped",
       K.parse_market({"ticker": "KXNBAONE3PT-27-WKESSLER14", "title": "x: y"}) is None
       and K.parse_market(dict(_mk("KXNBAPTS-26OCT23NOPUTA-UTAJJACKSON20-25"), floor_strike=29.5)) is None
       and K.parse_market(dict(_mk("KXNBAPTS-26OCT23NOPUTA-UTAJJACKSON20-25"), strike_type="between")) is None)

    # ---- home / away from ESPN ---------------------------------------------
    idx = K._build(fetch=lambda s: [copy.deepcopy(m) for m in RAW_MARKETS if m["ticker"].startswith(s + "-")],
                   now=1761181000.0, fee_lookup=lambda s: {"fee_type": "quadratic", "fee_multiplier": 1})
    ck("the index builds from fixtures: 16 records over 6 games (events keyed by the shared suffix), complete, 0 skipped",
       idx["complete"] and len(idx["markets"]) == 16 and idx["skipped"] == 0
       and set(idx["events"]) == {"26OCT23NOPUTA", "26OCT23DETMIA", "26OCT23LACLAL", "26OCT23PORSAC", "26OCT22CLEPHI", "26OCT23NYKBOS"}
       and idx["events"]["26OCT23NOPUTA"]["series"] == ["KXNBAPTS", "KXNBAPTSPROT"])
    boards = {"2026-10-22": _scoreboard("20261022"), "2026-10-23": _scoreboard("20261023")}
    rep = K.attach_games(idx, boards)
    ck("ESPN agrees with the ticker's AWAY-then-HOME order for the games it lists (CLE@PHI, NY@BOS, NO@UTAH): order_ok, none reversed",
       rep["order_bad"] == [] and rep["order_ok"] == 3 and idx["events"]["26OCT23NOPUTA"]["game"]["home"] == "UTAH"
       and idx["events"]["26OCT23NOPUTA"]["game"]["away"] == "NO" and idx["events"]["26OCT23NOPUTA"]["game"]["home_kalshi"] == "UTA"
       and idx["events"]["26OCT22CLEPHI"]["game"]["home"] == "PHI", rep)
    ck("games ESPN did not return (DET@MIA, LAC@LAL, POR@SAC in the fixture) are reported unmatched with game None, not guessed",
       set(rep["unmatched"]) == {"26OCT23DETMIA", "26OCT23LACLAL", "26OCT23PORSAC"} and idx["events"]["26OCT23DETMIA"]["game"] is None)
    ck("the date is the ET game date: DEN@OKC tips 01:30Z on the 23rd and Kalshi files it under 26OCT22 (ESPN's 20261022 board)",
       K.attach_games({"events": {"26OCT22DENOKC": {"date": "2026-10-22", "away_espn": "DEN", "home_espn": "OKC"}}}, boards)["matched"] == 1)
    rev = {"events": {"X": {"date": "2026-10-23", "away_espn": "UTAH", "home_espn": "NO"}}}
    ck("a REVERSED order is detected (a ticker that listed home first would be flagged order_bad, not trusted)",
       K.attach_games(rev, boards)["order_bad"] == ["X"] and rev["events"]["X"]["game"]["order_ok"] is False)
    ck("Kalshi's six differing club codes map to ESPN's and back",
       [K.KALSHI_TO_ESPN[c] for c in ("NYK", "GSW", "NOP", "SAS", "UTA", "WAS")] == ["NY", "GS", "NO", "SA", "UTAH", "WSH"]
       and all(K.ESPN_TO_KALSHI[v] == k for k, v in K.KALSHI_TO_ESPN.items()))
    ck("pregame(): True only for ESPN 'pre' with the tip ahead; the live CLE@PHI game is False; an unconsulted game is None (never True)",
       K.pregame(idx, "26OCT23NOPUTA", now=1761181000.0) is True and K.pregame(idx, "26OCT22CLEPHI", now=1761181000.0) is False
       and K.pregame(idx, "26OCT23DETMIA", now=1761181000.0) is None
       and K.pregame(idx, "26OCT23NOPUTA", now=K.kalshi._parse_time("2026-10-24T02:00:00Z")) is False)

    # ---- believable / reference --------------------------------------------
    agree = []
    for m, r in zip(RAW_MARKETS, recs):
        q = kalshi_nfl._q(m, "yes")
        agree.append(K.believable(r) == kalshi_nfl.believable(q) and K.believable(r["market"]) == kalshi_nfl.believable(q))
    ck("believable() agrees with kalshi_nfl.believable on the YES quote of all 16 real fixtures, from the record or from the bare market block", all(agree), agree)
    ck("first-listing books (2c / 68c, no volume) are not believable; the liquid Embiid 25+ book (32/44, 181k traded) is",
       not K.believable(st_) and K.believable(by["KXNBAPTS-26OCT22CLEPHI-PHIJEMBIID21-25"]))
    emb = by["KXNBAPTS-26OCT22CLEPHI-PHIJEMBIID21-25"]
    ref_p, ref_q = K.market_ref(emb)
    ck("market_ref is combo_engine.market_reference on the YES quote: Embiid 32/44 -> mid 38c, quality 0 < q <= 1 (spread 12c, deep)",
       _close(ref_p, 0.38, 1e-12) and (ref_p, ref_q) == combo_engine.market_reference(emb["q"]["yes"]) and 0 < ref_q <= 1
       and K.market_ref(emb["market"]) == (ref_p, ref_q))
    ck("an ask-only book reads as one-sided at the ask with the fixed low quality (0.2); no ask -> (None, 0)",
       K.market_ref(by["KXNBAPTS-26OCT22CLEPHI-CLEDMITCHELL45-45"]) == (0.24, 0.2)
       and K.market_ref({"yes_bid": None, "yes_ask": None}) == (None, 0.0))
    ck("quote_from_block rebuilds kalshi_nfl._q's shape (ask, bid, mid, spread, size, vol, oi)",
       set(K.quote_from_block(emb["market"], "yes")) == {"ask", "bid", "mid", "spread", "size", "vol", "oi"}
       and _close(K.quote_from_block(emb["market"], "no")["ask"], 68.0, 1e-12))

    # ---- ladders -------------------------------------------------------------
    lads = K.ladders(idx, key="26OCT23NYKBOS")
    ck("Brunson's real points ladder: the 35+ rung (no volume, no open interest) is DROPPED, 15/20/25/30 stay, p = YES mid 0.85 / 0.71 / 0.52 / 0.32",
       list(lads) == [("26OCT23NYKBOS", "pts", "jalen brunson")]
       and [k for k, _ in lads[("26OCT23NYKBOS", "pts", "jalen brunson")]["rungs"]] == [15, 20, 25, 30]
       and all(_close(p, q, 1e-12) for (_, p), q in zip(lads[("26OCT23NYKBOS", "pts", "jalen brunson")]["rungs"], (0.85, 0.71, 0.52, 0.32)))
       and lads[("26OCT23NYKBOS", "pts", "jalen brunson")]["n_dropped"] == 1 and lads[("26OCT23NYKBOS", "pts", "jalen brunson")]["n_listed"] == 5)
    lr = lads[("26OCT23NYKBOS", "pts", "jalen brunson")]["rungs"]
    ck("ladder rungs are non-increasing in k", D.is_monotone([k for k, _ in lr], [p for _, p in lr], 0.0))
    ck("standard and protected ladders are never merged: Jackson 25+ appears only under protected=True for the PROT record",
       ("26OCT23NOPUTA", "pts", "jaren jackson") not in K.ladders(idx, key="26OCT23NOPUTA") and K.ladders(idx, key="26OCT23NOPUTA", protected=True) == {})
    ck("markets() filters by key/stat/player/protected", len(K.markets(idx, key="26OCT23NYKBOS")) == 5
       and len(K.markets(idx, player="Jalen Brunson")) == 5 and len(K.markets(idx, protected=True)) == 1
       and len(K.markets(idx, stat="dd")) == 1 and len(K.markets(idx, believable_only=True, key="26OCT23NYKBOS")) == 4)

    ck("event_key finds a game from either club order and either code set (ESPN's NO/UTAH or Kalshi's NOP/UTA); date disambiguates; unknown -> None",
       K.event_key(idx, "UTAH", "NO") == K.event_key(idx, "NOP", "UTA") == "26OCT23NOPUTA"
       and K.event_key(idx, "NOP", "UTA", date="2026-10-24") is None and K.event_key(idx, "LAL", "UTA") is None)
    mfl = K.market_for_line(idx, key="26OCT23NYKBOS", stat="pts", player="Jalen Brunson", k=25)
    ck("market_for_line returns the contract's market block for one rung (Brunson 25+: bid 48 / ask 56), a copy, and None for an unlisted rung",
       mfl["yes_bid"] == 48.0 and mfl["yes_ask"] == 56.0 and mfl["ticker"] == "KXNBAPTS-26OCT23NYKBOS-NYKJBRUNSON11-25"
       and K.market_for_line(idx, key="26OCT23NYKBOS", stat="pts", player="Jalen Brunson", k=26) is None
       and K.market_for_line(idx, key="26OCT23NYKBOS", stat="pts", player="Jalen Brunson", k=25, protected=True) is None
       and mfl is not idx["markets"][idx["by_line"][("26OCT23NYKBOS", "pts", False, "jalen brunson", 25)]]["market"])
    lvb = V.line_value(0.60, mfl, 0.55, 0.65, now=1761181100.0)
    ck("end to end: the Brunson 25+ block feeds line_value directly (YES at the 56c ask + 1.73c fee: EV +2.27c)",
       lvb["price_used"]["yes"] == 56.0 and _close(lvb["ev_yes"], 60 - 56 - 7 * 0.56 * 0.44, 1e-9) and _close(lvb["p_market"], 0.52, 1e-12))

    # ---- player matching -----------------------------------------------------
    rosters = {"UTAH": K.parse_roster({"athletes": ROSTER_UTAH}),
               "NO": K.parse_roster({"athletes": [{"id": "9001", "fullName": "P.J. Washington Jr.", "jersey": "25"}]})}
    ck("ESPN's '00' jersey reads as 0 and '20' as 20; ids stay strings", {r["name"]: r["jersey"] for r in rosters["UTAH"]}["Harrison Ingram"] == 0
       and {r["name"]: r["jersey"] for r in rosters["UTAH"]}["Jaren Jackson Jr."] == 20 and rosters["UTAH"][0]["id"] == "4873138")
    mt = K.match_player("Jaren Jackson Jr.", "UTA", 20, rosters)
    ck("Kalshi 'Jaren Jackson Jr.' on UTA (Kalshi's code) -> ESPN athlete 4277961 by exact normalised name on UTAH's roster",
       mt["athlete_id"] == "4277961" and mt["method"] == "team_name" and mt["team_ok"])
    ck("'PJ Washington' on NOP matches ESPN's 'P.J. Washington Jr.' through the squashed key, not by luck",
       K.match_player("PJ Washington", "NOP", 25, rosters)["method"] == "team_squash"
       and K.match_player("PJ Washington", "NOP", 25, rosters)["athlete_id"] == "9001")
    ck("a spelling variant is matched by JERSEY + similar name only above the 0.75 similarity bar ('Keyonte Georges' #3 -> Keyonte George)",
       K.match_player("Keyonte Georges", "UTA", 3, rosters)["method"] == "team_jersey_name"
       and K.match_player("Keyonte Georges", "UTA", 3, rosters)["athlete_id"] == "4433627")
    ck("surname + first initial, unique on the roster, matches ('L. Markkanen')",
       K.match_player("Lauri Markkanen", "UTA", None, rosters)["method"] == "team_name"
       and K.match_player("Larri Markkanen", "UTA", None, rosters)["method"] == "team_last_init")
    ck("a player listed under the wrong club is found league-wide ONLY when the name is unique, and flagged team_ok False; strict_team refuses",
       K.match_player("Lauri Markkanen", "LAL", None, rosters)["method"] == "league_name"
       and K.match_player("Lauri Markkanen", "LAL", None, rosters)["team_ok"] is False
       and K.match_player("Lauri Markkanen", "LAL", None, rosters, strict_team=True) is None)
    ck("an unknown name with no jersey is a MISS (None), never a guess; a wrong jersey alone does not match",
       K.match_player("Nobody Atall", "UTA", None, rosters) is None and K.match_player("Nobody Atall", "UTA", 20, rosters) is None)
    mrep = K.match_players({"markets": [dict(r) for r in recs if r["team"] == "UTA"]}, rosters)
    ck("match_players on the fixture's UTA records: Jackson (standard + protected) is ONE player, matched; rate 1.0; misses empty",
       mrep["n_players"] == 1 and mrep["n_matched"] == 1 and mrep["rate"] == 1.0 and mrep["misses"] == [] and mrep["markets_matched"] == 2)
    miss = K.match_players({"markets": [dict(recs[0], team="UTA", player="Zzz Nobody", player_norm="zzz nobody", jersey=None)]}, rosters)
    ck("a miss is listed with its team, name, jersey and code", miss["n_matched"] == 0 and miss["misses"][0]["player"] == "Zzz Nobody"
       and miss["rate"] == 0.0)

    # ---- paging that never returns part of a series -------------------------
    calls = []

    def fake_pages(fail_on=None, first=False):
        def get(url, timeout=10):
            calls.append(url)
            n = len(calls)
            page = 1 + (int(url.split("cursor=c")[1]) if "cursor=c" in url else 0)
            if fail_on is not None and page == fail_on:
                raise RuntimeError("429")
            if first:
                raise RuntimeError("429")
            return {"markets": [{"ticker": f"KXNBAPTS-X-{page}-{n}"}], "cursor": f"c{page}" if page < 3 else ""}
        return get
    real_get, real_waits = kalshi._get_json, K._RETRY_WAITS
    K._RETRY_WAITS = (0.0, 0.0)
    try:
        kalshi._get_json = fake_pages()
        full = K._fetch("KXNBAPTS")
        ck("_fetch follows the cursor to the end: 3 pages -> 3 markets", len(full) == 3)
        calls.clear()
        kalshi._get_json = fake_pages(fail_on=2)
        try:
            K._fetch("KXNBAPTS")
            partial = True
        except RuntimeError:
            partial = False
        ck("a page that fails after two retries FAILS the fetch -- the first page's markets are never returned as if they were the series", partial is False)
        calls.clear()
        kalshi._get_json = fake_pages(first=True)
        try:
            K._fetch("KXNBAPTS")
            empty_ok = True
        except RuntimeError:
            empty_ok = False
        ck("a series whose FIRST page fails raises too (it must not read as 'no open markets')", empty_ok is False)
        ck("each failed page is retried: 1 try + 2 retries = 3 requests for the failing page",
           len([c for c in calls]) == 3)
        ck("and the failure is ledgered under its stable id KNBA-fetch (never silent)", "KNBA-fetch" in noted, noted)
    finally:
        kalshi._get_json, K._RETRY_WAITS = real_get, real_waits

    def failing_fetch(s):
        if s == "KXNBAREB":
            raise RuntimeError("boom")
        return [copy.deepcopy(m) for m in RAW_MARKETS if m["ticker"].startswith(s + "-")]
    part = K._build(fetch=failing_fetch, series=["KXNBAPTS", "KXNBAREB"], now=1.0, fee_lookup=lambda s: {})
    ck("a failed series marks the index incomplete and names it; the other series is still there; ledgered KNBA-series",
       part["complete"] is False and part["failed"] == ["KXNBAREB"] and part["counts"]["KXNBAPTS"] == 9 and "KNBA-series" in noted)
    ck("with no live fee lookup the table values (quadratic x 1, verified 2026-10-23) fill the block, and a live change wins",
       part["markets"][0]["market"]["fee_type"] == "quadratic"
       and K._fee_for("KXNBAPTS", lambda s: {"fee_type": "quadratic", "fee_multiplier": 0.5}) == ("quadratic", 0.5)
       and K._fee_for("KXNBAPTS", lambda s: {}) == ("quadratic", 1.0))

    # ---- depth ---------------------------------------------------------------
    got = K.fetch_orderbook("KXNBAAST-26OCT22DENOKC-OKCJWILLIAMS8-6", get=lambda url: ORDERBOOK_LIVE["orderbook"])
    ck("fetch_orderbook returns both ask ladders (cheapest first) and both bid ladders (best first) from the real payload",
       got["yes_asks"][0] == (36.0, 1413.0) and got["no_asks"][0] == (65.0, 75.0) and got["yes_bids"][0] == (35.0, 75.0)
       and got["no_bids"][0] == (64.0, 1413.0) and got["yes_asks"] == live_asks)
    ck("...and the best YES ask is the book's own: 100 - best NO bid == the ask on the list endpoint's market (36c)",
       got["yes_asks"][0][0] == 100.0 - got["no_bids"][0][0])

    # =====================================================================
    # H. fixes from the independent review (each check FAILS on the code it fixed)
    # =====================================================================
    import shutil
    import boardshare

    # ---- F1: a normal sampled through the copula must not pile mass on kmax --
    n0 = len(noted)
    ncd = D.NormalCC(1.0, 3.0)
    ck("NormalCC now defaults to lower=0: a count cannot be negative (NormalCC(25, 6).sf_ge(0) == 1, params carry lower 0)",
       ncd.lower == 0 and ncd.sf_ge(0) == 1.0 and ncd.to_dict()["params"]["lower"] == 0 and D.NormalCC(25.0, 6.0).lower == 0)
    z0 = 0.5 * math.erfc((-0.5 - 1.0) / (3.0 * math.sqrt(2)))

    def tsf(k):
        return 0.5 * math.erfc((k - 0.5 - 1.0) / (3.0 * math.sqrt(2))) / z0
    mean_t = sum(k * (tsf(k) - tsf(k + 1)) for k in range(0, 300))
    var_t = sum(k * k * (tsf(k) - tsf(k + 1)) for k in range(0, 300)) - mean_t ** 2
    ck("NormalCC.mean() / var() are the TRUNCATED variable's moments (summed from the pmf by an independent route), not the latent mu / sd^2: NormalCC(1, 3) has mean ~1.9, not 1.0",
       _close(ncd.mean(), mean_t, 1e-9) and _close(ncd.var(), var_t, 1e-9) and ncd.mean() > 1.5
       and _close(D.NormalCC(25.0, 6.0, lower=None).mean(), 25.0, 1e-12))
    dn = [r[0] for r in D.copula_draws([ncd], None, n=20000, seed=3)]
    p0n = ncd.pmf(0)
    ck("NormalCC(1, 3) through copula_draws: no draw near kmax (max < 60, where the untruncated version put 30.6% of 20,000 draws on k = 20,000); the share at 0 matches pmf(0) and the mean matches the truncated mean within 4.5 SE",
       max(dn) < 60 and abs(dn.count(0) / 20000 - p0n) <= 4.5 * math.sqrt(p0n * (1 - p0n) / 20000)
       and abs(sum(dn) / 20000 - ncd.mean()) <= 4.5 * math.sqrt(ncd.var() / 20000), (max(dn), dn.count(0) / 20000, p0n))
    ck("an untruncated normal with real negative mass cannot be sampled: NormalCC(1, 3, lower=None).cdf_table() and copula_draws raise ValueError (never a silent pile-up), ledgered PDIST-cdf-table",
       rejects(D.NormalCC(1.0, 3.0, lower=None).cdf_table) and rejects(D.copula_draws, [D.NormalCC(1.0, 3.0, lower=None)], None, 10, 1)
       and "PDIST-cdf-table" in noted[n0:])
    ck("...but an untruncated normal far above 0 (negative mass < 1e-12) tabulates fine: NormalCC(100, 5, lower=None)",
       D.NormalCC(100.0, 5.0, lower=None).cdf_table()[-1] == 1.0)
    n1 = len(noted)
    ck("cdf_table refuses when kmax is exhausted with mass left (NegBin mean 50,000 out past 20,000), instead of forcing cum[-1] = 1.0 onto k = kmax",
       rejects(D.NegBin(50000.0, 0.1).cdf_table) and "PDIST-cdf-table" in noted[n1:]
       and rejects(D.Poisson(50.0).cdf_table, 1e-12, 20) and D.Poisson(50.0).cdf_table()[-1] == 1.0)

    # ---- F2: a player in two games keeps two ladders -----------------------
    def _brunson_game(key, mids):
        out = []
        for m in RAW_MARKETS:
            if "NYKJBRUNSON11" not in m["ticker"]:
                continue
            k = int(m["ticker"].rsplit("-", 1)[1])
            mid = mids[k]
            m2 = copy.deepcopy(m)
            m2["ticker"] = m["ticker"].replace("26OCT23NYKBOS", key)
            m2["event_ticker"] = m["event_ticker"].replace("26OCT23NYKBOS", key)
            m2.update(yes_bid_dollars="%.4f" % (mid - 0.01), yes_ask_dollars="%.4f" % (mid + 0.01),
                      no_bid_dollars="%.4f" % (1 - mid - 0.01), no_ask_dollars="%.4f" % (1 - mid + 0.01),
                      volume_fp="60.00", open_interest_fp="60.00")
            out.append(m2)
        return out
    g2 = _brunson_game("26OCT24NYKPHI", {15: 0.40, 20: 0.30, 25: 0.20, 30: 0.12, 35: 0.06})   # SYNTHETIC second game built from the real fixture
    g1 = [copy.deepcopy(m) for m in RAW_MARKETS if "NYKJBRUNSON11" in m["ticker"]]
    idx2 = K._build(fetch=lambda s: g1 + [copy.deepcopy(m) for m in g2] if s == "KXNBAPTS" else [], series=["KXNBAPTS"],
                    now=1761181000.0, fee_lookup=lambda s: {})
    l2 = K.ladders(idx2)
    a_, b_ = l2.get(("26OCT23NYKBOS", "pts", "jalen brunson")), l2.get(("26OCT24NYKPHI", "pts", "jalen brunson"))
    ck("a back-to-back (SYNTHETIC second game from the real Brunson rungs): two ladders, one per game, each with its own rungs and no duplicated thresholds -- not one pooled PAVA fit",
       set(l2) == {("26OCT23NYKBOS", "pts", "jalen brunson"), ("26OCT24NYKPHI", "pts", "jalen brunson")}
       and [k for k, _ in a_["rungs"]] == [15, 20, 25, 30] and [k for k, _ in b_["rungs"]] == [15, 20, 25, 30, 35]
       and all(_close(p, q, 1e-9) for (_, p), q in zip(a_["rungs"], (0.85, 0.71, 0.52, 0.32)))
       and all(_close(p, q, 1e-9) for (_, p), q in zip(b_["rungs"], (0.40, 0.30, 0.20, 0.12, 0.06)))
       and a_["monotone_fixed"] is False and b_["monotone_fixed"] is False and a_["n_listed"] == b_["n_listed"] == 5
       and a_["key"] == "26OCT23NYKBOS" and b_["key"] == "26OCT24NYKPHI", {k: v["rungs"] for k, v in l2.items()})
    ck("key= still restricts to one game", list(K.ladders(idx2, key="26OCT24NYKPHI")) == [("26OCT24NYKPHI", "pts", "jalen brunson")])
    ck("market_for_line addresses the two games' same rung separately (Brunson 25+: NYK@BOS ask 56, the synthetic NYK@PHI game ask 21)",
       K.market_for_line(idx2, key="26OCT23NYKBOS", stat="pts", player="Jalen Brunson", k=25)["yes_ask"] == 56.0
       and K.market_for_line(idx2, key="26OCT24NYKPHI", stat="pts", player="Jalen Brunson", k=25)["yes_ask"] == 21.0)

    # ---- F12: two same-named players in one game ----------------------------
    twin = copy.deepcopy(next(m for m in g1 if m["ticker"].endswith("-25")))
    twin["ticker"] = twin["ticker"].replace("NYKJBRUNSON11", "BOSJBRUNSON11")
    n2 = len(noted)
    idx3 = K._build(fetch=lambda s: g1 + [twin] if s == "KXNBAPTS" else [], series=["KXNBAPTS"], now=1761181000.0, fee_lookup=lambda s: {})
    _tk = lambda m: (m or {}).get("ticker") or ""          # a None lookup is a FAIL, not a traceback (re-check R-2)
    ck("two same-named players in one game: the ambiguous line is None without a club (never the wrong man's book), each answers to his own club, other rungs are unaffected; ledgered KNBA-dup-line",
       K.market_for_line(idx3, key="26OCT23NYKBOS", stat="pts", player="Jalen Brunson", k=25) is None
       and _tk(K.market_for_line(idx3, key="26OCT23NYKBOS", stat="pts", player="Jalen Brunson", k=25, team="NYK")).endswith("NYKJBRUNSON11-25")
       and _tk(K.market_for_line(idx3, key="26OCT23NYKBOS", stat="pts", player="Jalen Brunson", k=25, team="BOS")).endswith("BOSJBRUNSON11-25")
       and K.market_for_line(idx3, key="26OCT23NYKBOS", stat="pts", player="Jalen Brunson", k=20) is not None
       and "KNBA-dup-line" in noted[n2:])
    l3 = K.ladders(idx3, key="26OCT23NYKBOS")
    ck("...and the ladder keeps the first man only (n_listed 5, rungs not doubled), ledgered KNBA-dup-player",
       l3[("26OCT23NYKBOS", "pts", "jalen brunson")]["n_listed"] == 5 and l3[("26OCT23NYKBOS", "pts", "jalen brunson")]["team"] == "NYK"
       and "KNBA-dup-player" in noted[n2:])

    # ---- F3: labels on a listing are not edges -------------------------------
    first = st_["market"]                          # the REAL first-listing book: 2c / 68c, nothing traded
    FN = 1761181100.0                              # 100 s after the fixture's fetch time
    vfirst = V.line_value(0.80, first, 0.75, 0.85, now=FN)
    ck("line_value carries believable and the quote's age: the real 2c / 68c zero-volume listing is believable False, quote_age_s 100, book kind one_sided; a 48/50 book with trading is believable True",
       vfirst["believable"] is False and vfirst["quote_age_s"] == 100.0 and vfirst["quote_ts"] == 1761181000.0
       and vfirst["book"]["kind"] == "one_sided" and lv["believable"] is True and lv["quote_age_s"] == 100.0, vfirst["book"])
    ck("the model says .80 against that listing: EV is +10.5c on paper, but positive_ev and positive_ev_robust are WITHHELD and thin_book is returned in their place (the old code awarded both); the descriptive high_probability (p .80 >= .70) is untouched",
       vfirst["ev_yes"] > 10 and V.line_labels(vfirst, n_eff=40) == ["high_probability", "thin_book"], V.line_labels(vfirst, n_eff=40))
    askonly = LV(0.20, dict(LIVE, yes_bid=None, yes_ask=3.0, no_bid=97.0, no_ask=None), 0.15, 0.25)
    # An ask-only book that HAS traded is real, just not two-sided: the reason
    # says "no offer on the other side", not "a listing" (Fable re-check R-1).
    ck("an ask-only book WITH volume is still thin: ask 3c, p .20, EV +16.8c -> ['thin_book']; label_reasons says it is one-sided with no offer on the other side (it has traded, so it is not 'a listing')",
       askonly["believable"] is False and askonly["ev_yes"] > 16 and V.line_labels(askonly, n_eff=40) == ["thin_book"]
       and "one-sided" in V.label_reasons(askonly, n_eff=40)["thin_book"]
       and "no offer on the other side" in V.label_reasons(askonly, n_eff=40)["thin_book"]
       and "listing" not in V.label_reasons(askonly, n_eff=40)["thin_book"])
    ck("a wide book (spread 24c) is thin even with trading; exactly 20c is not (the rule is <= 20, kalshi_nfl.believable's)",
       LV(0.9, dict(LIVE, yes_bid=40, yes_ask=64, no_bid=36, no_ask=60))["believable"] is False
       and LV(0.9, dict(LIVE, yes_bid=40, yes_ask=60, no_bid=40, no_ask=60))["believable"] is True)
    ck("a stale quote withholds positive_ev: age 601 s -> ['stale_quote']; age exactly 600 s is still fresh; the limit is a parameter (max_quote_age_s)",
       V.line_labels(V.line_value(0.60, blk, 0.55, 0.65, now=1601.0), n_eff=40) == ["stale_quote"]
       and V.line_labels(V.line_value(0.60, blk, 0.55, 0.65, now=1600.0), n_eff=40) == ["positive_ev", "positive_ev_robust"]
       and V.line_labels(V.line_value(0.60, blk, 0.55, 0.65, now=1601.0), n_eff=40, max_quote_age_s=5000) == ["positive_ev", "positive_ev_robust"]
       and V.line_labels(V.line_value(0.60, blk, 0.55, 0.65, now=1601.0), n_eff=40, max_quote_age_s=60) == ["stale_quote"])
    nots = {k: v for k, v in blk.items() if k != "ts"}
    vnots = LV(0.60, nots, 0.55, 0.65)
    ck("a block with no timestamp has an unknown age, and an unknown age is not a fresh one: quote_age_s None -> ['stale_quote'], and the reason says so",
       vnots["quote_age_s"] is None and V.line_labels(vnots, n_eff=40) == ["stale_quote"]
       and "no timestamp" in V.label_reasons(vnots, n_eff=40)["stale_quote"])
    vboth = V.line_value(0.80, first, 0.75, 0.85, now=FN + 5000)
    ck("a thin AND stale quote carries both qualifiers, in QUALIFIERS' order, and the stale reason prints the age",
       V.line_labels(vboth, n_eff=40) == ["high_probability", "thin_book", "stale_quote"] and "5100s old" in V.label_reasons(vboth, n_eff=40)["stale_quote"])
    vneg = V.line_value(0.50, first, 0.45, 0.55, now=FN)
    ck("a qualifier REPLACES an earned label and nothing else: a negative-EV listing (p .50 against 68c) gets no qualifier at all, and high_probability / strong_fit / low_evidence are untouched by a thin book",
       V.line_labels(vneg, n_eff=40) == []
       and V.line_labels(vfirst, n_eff=5, strong_fit=True) == ["high_probability", "strong_fit", "low_evidence", "thin_book"])
    nb = V.line_labels(V.line_value(0.97, dict(LIVE, volume=0.0, oi=0.0, yes_bid=2, yes_ask=68, no_bid=32, no_ask=98), 0.95, 0.99, now=NOW), side="yes", n_eff=40)
    ck("a quote's own age and a thin book are separate causes: a 2c / 68c listing with a FRESH timestamp (100 s) is thin_book alone, with high_probability kept (p .97)", nb == ["high_probability", "thin_book"], nb)
    ck("labels_from takes the two flags directly (book_ok / quote_fresh default True so a caller with explicit numbers is unchanged)",
       V.labels_from(p_side=0.6, ev_c=3.0, ev_lo_c=1.0, n_eff=40) == ["positive_ev", "positive_ev_robust"]
       and V.labels_from(p_side=0.6, ev_c=3.0, ev_lo_c=1.0, n_eff=40, book_ok=False) == ["thin_book"]
       and V.labels_from(p_side=0.6, ev_c=3.0, ev_lo_c=1.0, n_eff=40, quote_fresh=False) == ["stale_quote"])
    ck("book_is_believable is kalshi_nfl.believable's rule: same answer on all 16 real fixture books and on spread / volume variants of the Embiid book",
       all(V.book_is_believable(r["market"]) == K.believable(r["market"]) == kalshi_nfl.believable(kalshi_nfl._q(m, "yes"))
           for r, m in zip(recs, RAW_MARKETS) if r["market"]["yes_bid"] is not None)
       and all(V.book_is_believable(dict(emb["market"], yes_bid=yb_, volume=vv, oi=oo)) == K.believable(dict(emb["market"], yes_bid=yb_, volume=vv, oi=oo))
               for yb_ in (32.0, 24.0, 23.9, 44.0) for vv, oo in ((0.0, 0.0), (5.0, 0.0), (0.0, 5.0))))
    jsn = _json.dumps({"v": vfirst, "labels": V.line_labels(vboth, n_eff=40), "why": V.label_reasons(vboth, n_eff=40)}, allow_nan=False)
    ck("the new value fields are plain JSON", len(jsn) > 100 and '"believable": false' in jsn and "quote_age_s" in jsn)

    # ---- F4: a COMPLETE build with zero markets is the truth, not a failure ---
    old_build, old_cache, old_dir = K._build, dict(K._cache), boardshare._DIR
    tmp = tempfile.mkdtemp(prefix="prop_math_idx_")
    builds = []
    try:
        boardshare._DIR = os.path.join(tmp, "boards")
        K._cache.update({"ts": 0.0, "data": None})

        def empty_complete(*a, **k):
            builds.append("empty")
            return {"ts": 1.0, "complete": True, "failed": [], "counts": {}, "skipped": 0, "events": {}, "rules": {},
                    "markets": [], "by_line": {}, "by_line_team": {}}
        K._build = empty_complete
        n3 = len(noted)
        e1 = K.index()
        pub, _age = boardshare.get("kalshi_nba_idx", 60)
        K.index()
        K._cache.update({"ts": 0.0, "data": None})          # a sibling worker
        e3 = K.index()
        ck("a complete-but-empty build is served as complete, PUBLISHED to the shared store, and not ledgered as incomplete; later calls and sibling workers reuse it (1 build, not 3)",
           e1["complete"] is True and e1["markets"] == [] and pub is not None and pub["complete"] is True
           and e3["complete"] is True and builds == ["empty"] and "KNBA-index-incomplete" not in noted[n3:], (builds, noted[n3:]))
        shutil.rmtree(boardshare._DIR, ignore_errors=True)
        K._cache.update({"ts": 0.0, "data": None})

        def incomplete(*a, **k):
            builds.append("incomplete")
            return {"ts": 1.0, "complete": False, "failed": ["KXNBAREB"], "counts": {}, "skipped": 0, "events": {}, "rules": {},
                    "markets": [{"x": 1}]}
        K._build = incomplete
        n4 = len(noted)
        ic = K.index()
        pub2, _age2 = boardshare.get("kalshi_nba_idx", 60)
        ck("an INCOMPLETE build (a series failed) is still never published and is served flagged complete False, ledgered KNBA-index-incomplete",
           ic["complete"] is False and ic["failed"] == ["KXNBAREB"] and pub2 is None and "KNBA-index-incomplete" in noted[n4:])
    finally:
        K._build = old_build
        K._cache.clear()
        K._cache.update(old_cache)
        boardshare._DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- F5: the fractional-k convention is exercised -------------------------
    fams = [D.Poisson(3.0), D.NegBin(20.0, 0.1), D.ZeroInflated(D.Poisson(5.0), 0.2), D.NormalCC(25.0, 6.0), D.Yards(0.15, 45.0, 30.0)]
    ck("P(X >= k) for a fractional k is P(X >= ceil(k)) in every family: 24.5 -> 25, 0.2 -> 1, -0.5 -> 1.0, 24.0 -> 24, and float noise just above an integer (24 + 1e-13) stays 24",
       all(f.sf_ge(24.5) == f.sf_ge(25) and f.sf_ge(0.2) == f.sf_ge(1) and f.sf_ge(-0.5) == 1.0
           and f.sf_ge(24.0) == f.sf_ge(24) and f.sf_ge(24 + 1e-13) == f.sf_ge(24) for f in fams)
       and D.NegBin(20.0, 0.1).sf_ge(24.5) < D.NegBin(20.0, 0.1).sf_ge(24))

    # ---- F6: the exact-fee adjustment in stake sizing ------------------------
    s1, s2 = V.stake_profile(0.518, 0.6, 50), V.stake_profile(51.235, 0.6, 50)
    ck("stake sizing uses the EXACT fee, not the smooth estimate: $0.518 at 50c buys NOTHING (1 contract is 50c + a whole-cent 2c fee = 52c; the smooth fee 1.75c would say 51.75c and buy one)",
       s1 is None and 50 + V.fee_order_c(50, 1) > 51.8 > 50 + V.fee_per_contract_c(50))
    ck("$51.235 at 50c buys 98, not the smooth estimate's 99 (99 costs 4,950 + 174 = 5,124c > 5,123.5c)",
       s2["contracts"] == 98 and 99 * 50 + V.fee_order_c(50, 99) == 5124 > 5123.5 and s2["cost_usd"] * 100 <= 5123.5)

    # ---- F7: unsupported provenance claims are gone ---------------------------
    rdoc = V.refund_adjusted_sides.__doc__
    ck("refund_adjusted_sides no longer claims the fair price 'sits near' the pre-game price; it says the fair price is unmeasured and cites the counterexample (Nesmith 10+ settled 0.71 against a last trade of 0.00)",
       "sit near" not in rdoc and "UNMEASURED" in rdoc and "0.71" in rdoc and "GUESS" in rdoc
       and "roughly EV-neutral" not in K.__doc__ and "NOT assumed" in K.__doc__ and "4 event" in K.__doc__)

    # ---- F10: a crossed book --------------------------------------------------
    cr = V.kalshi_reference({"yes_bid": 60, "yes_ask": 55})
    ck("a crossed book (bid above ask) is never a two-sided mid: kind one_sided on the ask, noted as crossed, and not believable",
       cr["kind"] == "one_sided" and _close(cr["p"], 0.55, 1e-15) and "crossed" in cr["note"]
       and V.book_is_believable({"yes_bid": 60, "yes_ask": 55, "volume": 9.0}) is False)
    ck("the forbidden-word check covers the labels and the qualifiers", V.QUALIFIERS == ("thin_book", "stale_quote")
       and all("lock" not in w for w in V.LABELS + V.QUALIFIERS))

    # =====================================================================
    # G. house rules: no silent swallow, nothing heavy imported, no import-time network
    # =====================================================================
    bad = []
    for mod in ("prop_dist.py", "prop_value.py", "kalshi_nba_props.py"):
        tree = ast.parse(open(os.path.join(_ROOT, mod)).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                broad = node.type is None or (isinstance(node.type, ast.Name) and node.type.id == "Exception")
                if broad and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    bad.append(f"{mod}:{node.lineno}")
    ck("no silent `except Exception: pass` in the three modules", not bad, bad)
    code = ("import socket,sys\n"
            "def _no(*a, **k): raise RuntimeError('network at import')\n"
            "socket.socket.connect = _no; socket.create_connection = _no; socket.getaddrinfo = _no\n"
            "import prop_dist, prop_value, kalshi_nba_props\n"
            "bad = [m for m in ('numpy', 'scipy', 'pandas') if m in sys.modules]\n"
            "sys.exit(1 if bad else 0)\n")
    env = dict(os.environ, VIGIL_NO_BG="1")
    pr_ = subprocess.run([sys.executable, "-c", code], cwd=_ROOT, env=env, capture_output=True, text=True, timeout=60)
    ck("the three modules import with the network disabled and without numpy / scipy / pandas (the web server has none)",
       pr_.returncode == 0, (pr_.stdout + pr_.stderr)[-300:])


def _clean_block(m):
    """A raw fixture market as the payload's market block (cents, sentinels None)."""
    import kalshi_nba_props as K
    return K.market_block(m, 0.0)


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if (detail and not cond) else ""))

    run(ck)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
