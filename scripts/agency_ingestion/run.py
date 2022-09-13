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
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.expected_conditions import element_to_be_clickable

SELENIUM_OPTIONS = Options()
# SELENIUM_OPTIONS.add_argument("--headless")
# SELENIUM_OPTIONS.add_argument("--no-sandbox")
# SELENIUM_OPTIONS.add_argument("--disable-gpu")
# SELENIUM_OPTIONS.add_argument("--disable-dev-shm-usage")


PAHO_FILES = {
    "data1": "mpx_data",
    "data2": "mpx_linelist"
}

MAX_ATTEMPTS = 4


CDC_ENDPOINT = os.environ.get("CDC_ENDPOINT")
ECDC_ENDPOINT = os.environ.get("ECDC_ENDPOINT", "https://monkeypoxreport.ecdc.europa.eu")
PAHO_ENDPOINT = os.environ.get("PAHO_ENDPOINT", "https://shiny.pahobra.org/monkeypox/")
WHO_ENDPOINT = os.environ.get("WHO_ENDPOINT")

DATA_BUCKET = os.environ.get("DATA_BUCKET")

CDC_DATA_FOLDER = os.environ.get("CDC_DATA_FOLDER")
ECDC_DATA_FOLDER = os.environ.get("ECDC_DATA_FOLDER")
PAHO_DATA_FOLDER = os.environ.get("PAHO_DATA_FOLDER")
WHO_DATA_FOLDER = os.environ.get("WHO_DATA_FOLDER")

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

TODAY = date.today()


##### PAHO #####


def find_href(driver: object, id_tag: str) -> str:
    curr_attempt = 0
    try:
        element = WebDriverWait(driver, 10).until(
            element_to_be_clickable((By.ID, id_tag))
        )
        while curr_attempt < MAX_ATTEMPTS:
            link = driver.find_element(By.ID, id_tag).get_attribute("href")
            print(link)
            if link:
                return link
            sleep(2 ** curr_attempt)
            curr_attempt += 1
    except Exception:
        logging.exception(f"Something went wrong trying to find {id_tag}")
        raise


def get_paho_data(url: str) -> str:
	data = ""
    try:
        response = requests.get(url)
        for chunk in response.iter_content(chunk_size=128):
            data.append(chunk)
        return data
    except Exception:
        logging.exception(f"Something went wrong trying to download {url}")
        raise


##### ECDC #####


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


def to_csv(json_data: list[dict[str, str | int]], field_names: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=field_names)
    writer.writeheader()
    for row in json_data:
        writer.writerow(row)
    return buf.getvalue()


def get_ecdc_json(soup: BeautifulSoup, div: str) -> str:
	logging.info("Getting HTML from ECDC website")
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


##### CDC #####


def get_cdc_data() -> list[dict[str, str|int|None]]:
	logging.info("Getting CDC data")
	try:
		response = requests.get(CDC_ENDPOINT)
		reader = csv.DictReader(codecs.iterdecode(response.iter_lines(), "utf-8"))
		return [row for row in reader]
	except Exception:
		logging.exception("Something went wrong when trying to retrieve CDC data")
		raise


##################


def setup_logger():
	h = logging.StreamHandler(sys.stdout)
	rootLogger = logging.getLogger()
	rootLogger.addHandler(h)
	rootLogger.setLevel(logging.INFO)


def ingest_cdc_data():
	logging.info("Getting CDC data")
	data = get_cdc_data()
	csv_data = to_csv(data, data[0].keys())
	store_data(csv_data, f"{CDC_DATA_FOLDER}/{TODAY}_{div}.csv")
	store_data(csv_data, f"cdc_{div}_latest.csv")


def ingest_ecdc_data():
	logging.info("Getting ECDC data")
	site_content = get_ecdc_website()
	soup = make_soup(site_content)
	today = datetime.today()
	for div in TARGET_DIVS:
		json_soup = get_ecdc_json(soup, div)
		data = process_json(json_soup, div)
		fields = FIELDS.get(div)
		csv_data = to_csv(data, fields)
		store_data(csv_data, f"{ECDC_DATA_FOLDER}/{TODAY}_{div}.csv")
		store_data(csv_data, f"ecdc_{div}_latest.csv")


def ingest_paho_data():
	logging.info("Getting PAHO data")
	with webdriver.Firefox(options=SELENIUM_OPTIONS) as driver:
        driver.get(PAHO_ENDPOINT)
        WebDriverWait(driver, 3)
        driver.find_element(By.CLASS_NAME, "sidebar-toggle").click()
        today = datetime.today()
        for id_tag, file_name in PAHO_FILES.items():
            url = find_href(driver, id_tag)
            data = get_paho_data(url)
            store_data(paho_data, f"{PAHO_DATA_FOLDER}/{TODAY}_{file_name}.csv")
			store_data(paho_data, f"paho_{file_name}_latest.csv")


def ingest_who_data:
	data = get_who_data()
	csv_data = to_csv(data, data[0].keys())
	store_data(csv_data, f"{WHO_DATA_FOLDER}/{TODAY}.csv")
	store_data(csv_data, f"who_latest.csv")


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


def store_data(data: str, file_name: str) -> None:
	logging.info("Storing WHO data")
	try:
		s3 = boto3.resource("s3")
		s3.Object(DATA_BUCKET, file_name).put(Body=data)
	except Exception as exc:
		logging.exception(f"An exception occurred while trying to upload {file_name}")
		raise


@click.command()
@click.option("--cdc", is_flag=True, show_default=True, default=False, help="Ingest CDC data")
@click.option("--ecdc", is_flag=True, show_default=True, default=False, help="Ingest ECDC data")
@click.option("--paho", is_flag=True, show_default=True, default=False, help="Ingest PAHO data")
@click.option("--who", is_flag=True, show_default=True, default=False, help="Ingest WHO data")
def run(cdc, ecdc, paho, who):
	setup_logger()
	if not any([cdc, ecdc, paho, who]):
		raise Exception("This script requires at least one target agency for data ingestion")
	logging.info("Starting run")
	if cdc:
		ingest_cdc_data()
	if ecdc:
		ingest_ecdc_data()
	if paho:
		ingest_paho_data()
	if who:
		ingest_who_data()


if __name__ == "__main__":
	run()
	
