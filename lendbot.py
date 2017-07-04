from decimal import Decimal
from datetime import datetime, timedelta
import numpy as np

# API key stuff
BITFINEX_API_KEY = ""
BITFINEX_API_SECRET = b""

# Initial cumulative ask amount threshold where rate is considered
CUMULATIVE_ASK_AMOUNT_THRESHOLD = Decimal("50.0")

# Percent to bump up first ask rate by
ASK_RATE_BUMP = np.float64("3.0")

# Don't reduce our ETH offers below this rate, in percentage per year
ETH_MINIMUM_RATE_PERCENT = Decimal("0.0001")

# How much to reduce the rates on our unfilled ETH offers, in percentage per
# year
ETH_RATE_DECREMENT_PERCENT = np.float64("1.0")

# How many days we're willing to lend our ETH funds for
ETH_LEND_PERIOD_DAYS = 2

# Don't try to make ETH offers smaller than this. Bitfinex currently doesn't
# allow loan offers smaller than $50.
ETH_MINIMUM_LEND_AMOUNT = Decimal("0.16")

# How often to retrieve the current statuses of our offers
POLL_INTERVAL = timedelta(minutes=8)

# How often to retrieve the current statuses of our offers
HOURS_TO_ALLOW_HIGH_RATE_ASKS = np.float64("2.0")

# Scaling threshold for 30 day rate to be issued instead of 2 day rate
LONG_LOAN_TO_SHORT_LOAN_THRESHOLD = np.float64("1.5")

from itertools import count
import time
import base64
import json
import hmac
import hashlib
import urllib
from collections import defaultdict, deque

import requests

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

class Offer(object):
    """
    An unfilled swap offer.

    """
    def __init__(self, offer_dict):
        """
        Args:
            offer_dict: Dictionary of data for a single swap offer as returned
                by the Bitfinex API.

        """
        self.id = offer_dict["id"]
        self.currency = offer_dict["currency"]
        self.rate = Decimal(offer_dict["rate"])
        self.submitted_at = datetime.utcfromtimestamp(int(Decimal(
            offer_dict["timestamp"]
        )))
        self.amount = Decimal(offer_dict["remaining_amount"])

    def __repr__(self):
        return (
            "Offer(id={}, currency='{}', rate={}, amount={}, submitted_at={})"
        ).format(self.id, self.currency, self.rate, self.amount,
                 self.submitted_at)

class BitfinexAPI(object):
    """
    Handles API requests and responses.

    """
    base_url = "https://api.bitfinex.com"
    rate_limit_interval = timedelta(seconds=70)
    max_requests_per_interval = 60

    def __init__(self, api_key, api_secret):
        """
        Args:
            api_key: The API key to use for requests made by this object.
            api_secret: THe API secret to use for requests made by this object.

        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.nonce = count(int(time.time()))
        self.request_timestamps = deque()

    def get_offers(self):
        """
        Retrieve current offers.

        Returns:
            A 1-tuple of lists. Contains ETH offers, as Offer objects.

        """
        offers_data = self._request("/v1/offers")
        eth_offers = []
        for offer_dict in offers_data:
            # Ignore swap demands and FRR offers
            if (
                offer_dict["direction"] == "lend"
                and offer_dict["rate"] != "0.0"
            ):
                offer = Offer(offer_dict)
                if offer.currency == "ETH":
                    eth_offers.append(offer)
        return (eth_offers)

    def get_best_funding_rate(self, amount, last_loan_pend_time):
        """
        Set Optimum Rate from Funding Book.

        """
        if amount <  CUMULATIVE_ASK_AMOUNT_THRESHOLD:
           amount = CUMULATIVE_ASK_AMOUNT_THRESHOLD
        funding_book = requests.get('https://api.bitfinex.com/v1/lendbook/ETH?limit_asks=1000', verify=True).json()['asks']

        #Computing 2 day loan rate
        i = 0
        dur = 2 #2 days by default
        threshold_amount = 0.0
        value_bump = 0.0
        while threshold_amount < amount and i <= len(funding_book)-1:
            threshold_amount += np.float64(funding_book[i]['amount'])
            i +=1

        if threshold_amount < amount:#Increasing rate percentage if lended sum is more than cumulative sum of available loans
            value_bump = (amount - threshold_amount)/amount

        final_rate = np.float64(funding_book[max(0,i-1)]['rate']) * np.float64(1.00 + ASK_RATE_BUMP/100.0 + value_bump)


        #Computing 30 day loan rate if loans pending for < 2 hours        
        if last_loan_pend_time == None or (datetime.now() - last_loan_pend_time).seconds < HOURS_TO_ALLOW_HIGH_RATE_ASKS*3600.0 : 
            i = 0
            threshold_amount = 0.0
            while threshold_amount < amount and i <= len(funding_book)-1:
                if np.float64(funding_book[i]['period']) == 30.0:
                    threshold_amount += np.float64(funding_book[i]['amount'])
                i +=1

            if threshold_amount < amount:
                value_bump = (amount - threshold_amount)/amount

            thirty_day_rate = np.float64(funding_book[max(0,i-1)]['rate']) * np.float64(1.00 + ASK_RATE_BUMP/100.0 + value_bump)

            if thirty_day_rate >= LONG_LOAN_TO_SHORT_LOAN_THRESHOLD * final_rate:# Go for 30 day rate only if 30 day rate atleast 1.5 times 2 day rate
               final_rate = thirty_day_rate
               dur = 30

        return (final_rate, dur)

    def cancel_offer(self, offer):
        """
        Cancel an offer.

        Args:
            offer: The offer to cancel as an Offer object.

        Returns:
            An Offer object representing the now-cancelled offer.

        """
        return Offer(self._request("/v1/offer/cancel", {"offer_id": offer.id}))

    '''def funds_status(self):
        """
        Returns:

        """
        status_data = self._request("/v2/auth/r/funding/fETH")
        print status_data
        return 

    def new_offer_two(self, currency, amount, rate, period):
        """
        Create a new offer.

        Args:
            currency: "ETH".
            amount: Amount of the offer as a Decimal object.
            rate: Interest rate of the offer per year, as a Decimal object.
            period: How many days to lend for.

        Returns:
            An Offer object representing the newly-created offer.

        """
        return Offer(self._request("/v2/auth/r/offers", {
            "symbol": currency,
            "amount": np.float64(amount),
            "rate": np.float64(rate),
            "period": np.int32(period),
            "type": "lend",
            "hidden": 1,
            "insure": 1,
        }))'''

    def new_offer(self, currency, amount, rate, period):
        """
        Create a new offer.

        Args:
            currency: "ETH".
            amount: Amount of the offer as a Decimal object.
            rate: Interest rate of the offer per year, as a Decimal object.
            period: How many days to lend for.

        Returns:
            An Offer object representing the newly-created offer.

        """
        return Offer(self._request("/v1/offer/new", {
            "currency": currency,
            "amount": str(amount),
            "rate": str(rate),
            "period": period,
            "direction": "lend",
        }))

    def get_available_balances(self, avail, total):
        """
        Retrieve available balances in deposit wallet.

        Returns:
            A 2-tuple of the USD balance followed by the ETH balance.

        """
        balances_data = self._request("/v1/balances")
        eth_available = 0
        for balance_data in balances_data:
            if np.float64(balance_data["amount"]) != total:
                print (bcolors.OKGREEN + str(datetime.now()) + bcolors.OKBLUE + bcolors.BOLD + " Total ETH: " + balance_data["amount"])
                print(bcolors.BOLD + "30 Days Interest: " + str(self._request("/v1/summary")["funding_profit_30d"][3]["amount"]) + bcolors.ENDC)
            if balance_data["type"] == "deposit":
                if balance_data["currency"] == "eth":
                    eth_available = Decimal(balance_data["available"])
                    if eth_available != avail:
                        print (bcolors.OKGREEN + str(datetime.now()) + bcolors.ENDC + " Available ETH: " + str(eth_available) + bcolors.ENDC)
        return (eth_available,np.float64(balance_data["amount"]))

    def _request(self, request_type, parameters=None):
        self._rate_limiter()
        url = self.base_url + request_type
        if parameters is None:
            parameters = {}
        parameters.update({"request": request_type,
                           "nonce": str(next(self.nonce))})
        payload = base64.b64encode(json.dumps(parameters).encode())
        signature = hmac.new(self.api_secret, payload, hashlib.sha384)
        headers = {"X-BFX-APIKEY": self.api_key,
                   "X-BFX-PAYLOAD": payload,
                   "X-BFX-SIGNATURE": signature.hexdigest()}
        request = None
        retry_count = 0
        while request is None:
            status_string = None
            try:
                request = requests.post(url, headers=headers)
            except requests.exceptions.ConnectionError:
                status_string = "Connection failed,"
            if request and request.status_code == 500:
                request = None
                status_string = "500 internal server error,"
            if request is None:
                delay = 2 ** retry_count
                print(status_string, "sleeping for", delay,
                      "seconds before retrying")
                time.sleep(delay)
                retry_count += 1
                # I'm assuming that if we don't manage to connect, or we get a
                # 500 internal server error, it doesn't count against our
                # request limit. If this isn't the case, then we should call
                # _rate_limiter() here too.
        if request.status_code != 200:
            print(request.text)
            request.raise_for_status()
        return request.json()

    def _rate_limiter(self):
        timestamps = self.request_timestamps
        while True:
            expire = datetime.utcnow() - self.rate_limit_interval
            while timestamps and timestamps[0] < expire:
                timestamps.popleft()
            if len(timestamps) >= self.max_requests_per_interval:
                delay = (timestamps[0] - expire).total_seconds()
                print("Request rate limit hit, sleeping for", delay, "seconds")
                time.sleep(delay)
            else:
                break
        timestamps.append(datetime.utcnow())

def go():
    """
    Main loop.

    """
    api = BitfinexAPI(BITFINEX_API_KEY, BITFINEX_API_SECRET)
    restart_flag = True
    rate = 0.001
    eth_available = 0.0
    last_eth_available = eth_available
    total_bal = 0.0
    last_reset_time = datetime.now()
    time_since_last_pending_lend = None
    while True:
        start_time = datetime.utcnow()

        eth_offers = api.get_offers()
        if len(eth_offers) > 0:#Cancel any pending orders
            api_success_flag = False
            try_counter = 0
            while api_success_flag == False and try_counter < 10:#To ensure step does not fail
                try:
                    api.cancel_offer(eth_offers[0])
                    api_success_flag = True
                except Exception:
                    import traceback
                    print (str(datetime.now()) + ' ***BITFINEX CALL EXCEPTION***: ' + traceback.format_exc() + ": Waiting for 10 seconds before re-attempting..")
                    try_counter = try_counter + 1
                    time.sleep(10)

        try:	
            (eth_available, total_bal) = api.get_available_balances(eth_available, total_bal)
            #api.funds_status()
        except Exception:
            print Exception
        if (datetime.now() - last_reset_time).seconds/3600.0 > 1.0 or eth_available != last_eth_available:#Reset flag if it's been sitting past 1 hour since last rate set or eth available to lend has changed
            restart_flag = True
            last_eth_available = eth_available

        if eth_offers != [] or eth_available >= ETH_MINIMUM_LEND_AMOUNT:
            if restart_flag == True:#Starting new lend rate set
                (rate, days) = api.get_best_funding_rate(eth_available, time_since_last_pending_lend)
                restart_flag = False
                last_reset_time = datetime.now()  
            else:#No one took the last ask, decrement ask rate by ETH_RATE_DECREMENT_PERCENT
                rate = rate * (1.0 - ETH_RATE_DECREMENT_PERCENT/100.0)
            try:	
                print(bcolors.OKGREEN + str(datetime.now()) + ": " + bcolors.ENDC + str(api.new_offer("ETH", eth_available, rate, days)))
            except Exception:
                print Exception
            if time_since_last_pending_lend == None:#Starting offer pending timer
                time_since_last_pending_lend = datetime.now()
        elif eth_available < ETH_MINIMUM_LEND_AMOUNT:#No loans pending reset clock
            time_since_last_pending_lend = None


        end_time = datetime.utcnow()
        elapsed = end_time - start_time
        remaining = POLL_INTERVAL - elapsed
        delay = max(remaining.total_seconds(), 0)
        time.sleep(delay)


go()