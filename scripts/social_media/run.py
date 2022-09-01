import csv
from datetime import date, timedelta
import io
import logging
import os

import click
import flag
from iso3166 import countries
import requests
import pygsheets
import tweepy


DOCUMENT_ID = os.environ.get("DOCUMENT_ID")

CONSUMER_KEY = os.environ.get("TWITTER_KEY", "")
CONSUMER_SECRET = os.environ.get("TWITTER_SECRET", "")
ACCESS_TOKEN = os.environ.get("TWITTER_TOKEN", "")
ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_TOKEN_SECRET", "")

TS_DATA_URL = os.environ.get("TS_DATA_URL", "")

GH_TO_ISO = {
   "Bolivia": "Bolivia, Plurinational State of",
   "Czech Republic": "Czechia",
   "Democratic Republic Of The Congo": "Congo, Democratic Republic of the",
   "Iran": "Iran, Islamic Republic of",
   "Moldova": "Moldova, Republic of",
   "Republic of Congo": "Congo",
   "Russia": "Russian Federation",
   "South Korea": "Korea, Republic of",
   "Turkey": "Türkiye",
   "United Kingdom": "United Kingdom of Great Britain and Northern Ireland",
   "United States": "United States of America",
   "Venezuela": "Venezuela, Bolivarian Republic of"
}

ENDEMIC_COUNTRIES = []


def get_gh_timeseries_data():
   logging.info("Getting G.h data")
   try:
      response = requests.get(TS_DATA_URL)
      return response.json()
   except Exception:
      logging.exception("Failed to get G.h data")
      raise


def get_endemic_countries():
   logging.info("Getting data from Google Sheets")
   try:
      client = pygsheets.authorize(service_account_env_var="GOOGLE_CREDENTIALS")
      spreadsheet = client.open_by_key(DOCUMENT_ID)
      records = spreadsheet.worksheet("title", "Endemic Countries").get_all_records()
      return list(set([r["Country"] for r in records]))
   except Exception:
      logging.exception("An error occurred while trying to get values from the sheet")
      raise


def get_gh_spreadsheet_data() -> list[dict[str, str|int|None]]:
   logging.info("Getting data from Google Sheets")
   try:
      client = pygsheets.authorize(service_account_env_var="GOOGLE_CREDENTIALS")
      spreadsheet = client.open_by_key(DOCUMENT_ID)
      vals = spreadsheet.worksheet("title", "Cases by Country").get_all_values()
      data = convert_lists(vals)
      return [c for c in data if c.get("Country")]
   except Exception:
      logging.exception("An error occurred while trying to get values from the sheet")
      raise


def convert_lists(data: list[list]) -> list[dict]:
   logging.info("Converting tabular data to associative array")
   if "COUNTA" in data[0][0]:
      data.remove(data[0])
   if "Country" not in data[0][0]:
      raise Exception("Could not locate column labels")

   rows = "\n".join([f"{','.join(d)}" for d in data])
   f = io.StringIO(rows)
   reader = csv.DictReader(f)
   today = date.today().strftime("%Y-%m-%d")
   records = []
   for row in reader:
      record = {
         "Date": today,
         "Country": row["Country"],
         "confirmed+death": 0
      }
      if row.get("confirmed") != "":
         record["confirmed+death"] += int(row.get("confirmed"))
      if row.get("death") != "":
         record["confirmed+death"] += int(row.get("death"))
      records.append(record)
   return records


def ignore_endemic_countries(data_set):
   for record in data_set:
      if record.get("Country") in ENDEMIC_COUNTRIES:
         data_set.remove(record)


def parse_gh_data(timeseries_data, sheet_data = {}, ts_only = False):
   today = date.today()
   t_str = today.strftime("%Y-%m-%d")
   yesterday = today - timedelta(days = 1)
   y_str = yesterday.strftime("%Y-%m-%d")
   parsed_data = {}
   sheet = (sheet_data, "Today", t_str, "confirmed+death")
   ts_yday = (timeseries_data, "Yesterday", y_str, "Cumulative_cases")
   ts_today = (timeseries_data, "Today", t_str, "Cumulative_cases")
   data_and_terms = [sheet, ts_yday]
   if ts_only:
      data_and_terms = [ts_today, ts_yday]

   for data, day, date_str, key in data_and_terms:
      for obj in data:
         country = obj.get("Country", "")
         if country in GH_TO_ISO:
            country = GH_TO_ISO.get(country)
         try:
            iso_code = countries.get(country).alpha2
         except KeyError:
            logging.warning(f"No ISO3166 country for {country}")
            continue
         if not parsed_data.get(iso_code):
            parsed_data[iso_code] = {}
         if obj.get("Date", "").startswith(date_str):
            parsed_data[iso_code][day] = obj.get(key, 0)
   return parsed_data


def clean_data(data):
   to_delete = []
   print(data)
   for c, v in data.items():
      today = v.get("Today")
      yesterday = v.get("Yesterday")
      if not v or not today or not yesterday or today <= yesterday:
         to_delete.append(c)
   print(to_delete)
   for k in to_delete:
      del data[k]
   print(data)


def make_tweet(data):
   logging.info("Making tweet")
   tweet = "New confirmed Monkeypox cases:\n\n"
   ######
   # for c, v in data.items():
   #    if not v.get("Today"):
   #       print(f"Missing today for {countries.get(c).name}: {v}")
   #    if not v.get("Yesterday"):
   #       print(f"Missing yesterday for {countries.get(c).name}: {v}")
   ######
   if len(data) > 5:
      tweet = "New confirmed Monkeypox cases (top 5):\n\n"
      data = dict(sorted(data.items(), key = lambda x: x[1]["Today"] - x[1]["Yesterday"], reverse=True)[:5])
   for country, counts in data.items():
      yesterday = counts.get("Yesterday", 0)
      today = counts.get("Today", 0)
      tweet += f"{countries.get(country).name.split(',')[0]} "
      if f := flag.flag(country):
         tweet += f"{f}: "
      tweet += f"+{today - yesterday} ({today})\n"
   tweet += "\nData and repo: https://github.com/globaldothealth/monkeypox\nInteractive map: https://map.monkeypox.global.health"
   return tweet


def send_tweet(tweet):
   logging.info(f"Sending tweet:\n{tweet}")
   # print(f"Sending tweet:\n{tweet}")
   try:
      auth = tweepy.OAuthHandler(CONSUMER_KEY, CONSUMER_SECRET)
      auth.set_access_token(ACCESS_TOKEN, ACCESS_TOKEN_SECRET)
      api = tweepy.API(auth)
      api.update_status(tweet)
   except Exception:
      logging.exception("Could not send tweet")
      raise


@click.command()
@click.option("--timeseries", is_flag=True, show_default=True, default=False, help="Use timeseries data, not the Gsheet, for today's cases")
def run(timeseries):
   logging.info("Starting")
   timeseries_data = get_gh_timeseries_data()
   sheet_data = get_gh_spreadsheet_data()
   # get today's data from TS when running in AM BST (curation happens in the US)
   ENDEMIC_COUNTRIES = get_endemic_countries()
   ignore_endemic_countries(timeseries_data)
   ignore_endemic_countries(sheet_data)
   parsed_data = {}
   if timeseries:
      parsed_data = parse_gh_data(timeseries_data, {}, True)
   else:
      parsed_data = parse_gh_data(timeseries_data, sheet_data, False)
   clean_data(parsed_data)
   tweet = make_tweet(parsed_data)
   send_tweet(tweet)
   # find and delete earlier duplicate tweet, if it exists
   # tweet screenshot of map
   # tweet screenshot of 7-day average


if __name__ == "__main__":
   run()
