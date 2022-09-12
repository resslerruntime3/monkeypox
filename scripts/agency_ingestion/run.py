import csv
from datetime import date
from enum import Enum
import io
from itertools import repeat
import json
import logging
import os
import re
import sys
from typing import Optional

import boto3
from bs4 import BeautifulSoup
import requests


CDC_ENDPOINT = os.environ.get("CDC_ENDPOINT")
ECDC_ENDPOINT = os.environ.get("ECDC_ENDPOINT", "https://monkeypoxreport.ecdc.europa.eu")
PAHO_ENDPOINT = os.environ.get("PAHO_ENDPOINT")
WHO_ENDPOINT = os.environ.get("WHO_ENDPOINT")

DATA_BUCKET = os.environ.get("DATA_BUCKET")

CDC_DATA_FOLDER = os.environ.get("CDC_DATA_FOLDER")
ECDC_DATA_FOLDER = os.environ.get("ECDC_DATA_FOLDER")
PAHO_DATA_FOLDER = os.environ.get("PAHO_DATA_FOLDER")
WHO_DATA_FOLDER = os.environ.get("WHO_DATA_FOLDER")


# URL = "https://monkeypoxreport.ecdc.europa.eu"
ONSET_OCA_DIV_ID = "by-date-of-onset-and-by-country-or-area"
ONSET_OCA_FIELDS = ["date", "country", "count"]
NOTIF_DIV_ID = "overall-by-date-of-notification"
NOTIF_FIELDS = ["date", "count"]
ONSET_DATE_DIV_ID = "overall-by-date-of-symptom-onset"
ONSET_DATE_FIELDS = ["date", "count", "type"]

TARGET_DIVS = [ONSET_OCA_DIV_ID, NOTIF_DIV_ID, ONSET_DATE_DIV_ID]


Output = Enum("Output", "CSV JSON Native")

REGEXES = {
    ONSET_OCA_DIV_ID: r"Date: (20\d\d-[0-1]\d-\d\d)<br />count:\s+(\d+)<br />ReportingCountry:\s+(.*)",
    NOTIF_DIV_ID: r"DateNotif: (20\d\d-[0-1]\d-\d\d)<br />count:\s+(\d+)",
    ONSET_DATE_DIV_ID: r"Date: (20\d\d-[0-1]\d-\d\d)<br />count:\s+(\d+)<br />TypeDate: (\w+)"
}

FIELDS = {
    ONSET_OCA_DIV_ID: ONSET_OCA_FIELDS,
    NOTIF_DIV_ID: NOTIF_FIELDS,
    ONSET_DATE_DIV_ID: ONSET_DATE_FIELDS
}


site_content = get_ecdc_website()
soup = make_soup(site_content)
today = datetime.today()
for div in TARGET_DIVS:
	json_soup = get_ecdc_json(soup, div)
	data = process_json(json_soup, div)
	fields = FIELDS.get(div)
	csv_data = to_csv(data, fields)


def store_ecdc_data(today: str):
	file_name = f"ecdc_{div}_latest.csv"
	s3.Object(DATA_BUCKET, f"{ECDC_DATA_FOLDER}/{today}.csv").put(Body=csv_data)
	s3.Object(DATA_BUCKET, file_name).put(Body=csv_data)


def make_soup(content: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(content, "html5lib")
    except Exception:
        try:
        	return BeautifulSoup(content, "html.parser")
        except Exception:
        	logging.exception("Something went wrong trying to make a BeautifulSoup")
        	raise


# def get_json_data(soup: str, div: str) -> dict[str]:
#     html = soup.find("div", id=div)
#     if html is None:
#         raise ValueError(f"div[id='{div}'] not found")
#     script = html.find("script")
#     if script is None:
#         raise ValueError("No JSON data found in div")
#     return json.loads(script.contents[0])



def to_csv(json_data: list[dict[str, str | int]], field_names: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=field_names)
    writer.writeheader()
    for row in json_data:
        writer.writerow(row)
    return buf.getvalue()


# def get_ecdc_data(
#     div: str, url: str = URL, output: Output = Output.CSV
# ) -> str | list[dict[str, str | int]]:
#     json_soup = get_json_data(fetch_soup(URL), div=div)
# def process_data(json_soup: str, div: str)
#     data = process_json(json_soup, div)
#     return to_csv(data, FIELDS[div])


def get_ecdc_json(soup: BeautifulSoup, div: str) -> str:
	logging.info("Getting HTML from ECDC website")
	# soup = None
	# content = None
	# try:
	# 	content = requests.get(ECDC_ENDPOINT).content.decode("utf-8")
	# except Exception:
	# 	logging.exception("Something went wrong getting HTML from ECDC website")
	# soup = make_soup(content, "html5lib")
	html = soup.find("div", id=div)
    if html is None:
        raise ValueError(f"div[id='{div}'] not found")
    script = html.find("script")
    if script is None:
        raise ValueError("No JSON data found in div")
    try:
    	return json.loads(script.contents[0])
    except Exception:
    	logging.exception("Something went wrong getting JSON from <script>")
    	raise


def get_ecdc_website():
	try:
		return requests.get(ECDC_ENDPOINT).content.decode("utf-8")
	except Exception:
		logging.exception("Something went wrong getting HTML from ECDC website")
		raise


def process_json(json_data: dict[str], div: str) -> list[dict[str, str | int]]:
    records = []
    for group in json_data["x"]["data"]:
        text = group["text"]
        # countries with only one entry are not in a list
        text = text if isinstance(text, list) else [text]
        # parse each line and remove invalid lines
        records.extend(list(filter(None, map(parse_line, text, repeat(div)))))

    return records


# Yep, that JSON contains HTML.
def parse_line(line: str, div: str) -> Optional[dict[str, str | int]]:
    """Returns comma separated values from line
    where line is of a form given in REGEXES.values()
    """
    if match := re.match(REGEXES.get(div), line):
        if div == ONSET_OCA_DIV_ID:
            date, count, country = match.groups()
            return {"date": date, "count": int(count), "country": country}
        if div == NOTIF_DIV_ID:
            date, count = match.groups()
            return {"date": date, "count": int(count)}
        if div == ONSET_DATE_DIV_ID:
            date, count, onset_type = match.groups()
            return {"date": date, "count": int(count), "type": onset_type}
    return None


def store_ecdc():
    logging.info("Fetching and storing ECDC data")
    for div in TARGET_DIVS:
        now = datetime.today()
        logging.info(f"Getting data from div {div}")
        file_name = f"ecdc/ecdc-{div}.csv"
        S3.Object(DATA_BUCKET, file_name).put(Body=get_ecdc_data(div=div))
        file_name = f"ecdc-archives/{now}-ecdc-{div}.csv"
        S3.Object(DATA_BUCKET, file_name).put(Body=get_ecdc_data(div=div))


##################


def setup_logger():
	h = logging.StreamHandler(sys.stdout)
	rootLogger = logging.getLogger()
	rootLogger.addHandler(h)
	rootLogger.setLevel(logging.INFO)


def get_cdc_data():
	logging.info("Getting CDC data")
	# C/P from comparison script


def get_ecdc_data():
	logging.info("Getting ECDC data")
	# C/P from ecdc.py


def get_paho_data():
	logging.info("Getting PAHO data")
	# Create a session, GET two CSVs
	# The page loads dynamically, injecting the session ID into the data download links
	# 


def get_who_data() -> list[dict[str, str|int|None]]:
	logging.info("Getting WHO data")
	try:
		response = requests.post(WHO_ENDPOINT, json={})
		return response.json().get("Data")
	except Exception:
		logging.exception("Something went wrong when trying to retrieve WHO data")


def format_data(data: list[dict[str, str|int|None]]) -> tuple[str, str]:
	logging.info("Formatting data")
	json_data = json.dumps(data)
	csv_io = io.StringIO()
	try:
		csv_writer = csv.DictWriter(csv_io, fieldnames=data[0].keys())
		csv_writer.writeheader()
		for row in data:
			csv_writer.writerow(row)
	except Exception:
		logging.exception("Something went wrong formatting data")
	return json_data, csv_io.getvalue()


def store_data(json_data: str, csv_data: str) -> None:
	logging.info("Storing WHO data")
	today = date.today()
	try:
		s3 = boto3.resource("s3")
		s3.Object(DATA_BUCKET, f"{WHO_DATA_FOLDER}/{today}.csv").put(Body=csv_data)
		s3.Object(DATA_BUCKET, f"{WHO_DATA_FOLDER}/{today}.json").put(Body=json_data)
		s3.Object(DATA_BUCKET, "who_latest.csv").put(Body=csv_data)
		s3.Object(DATA_BUCKET, "who_latest.json").put(Body=json_data)
	except Exception as exc:
		logging.exception(f"An exception occurred while trying to upload data files")
		raise


if __name__ == "__main__":
	setup_logger()
	logging.info("Starting run")
	data = get_who_data()
	json_data, csv_data = format_data(data)
	store_data(json_data, csv_data)
