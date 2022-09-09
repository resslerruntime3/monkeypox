import csv
from datetime import date
import io
import json
import logging
import os
import sys

import boto3
import requests


WHO_ENDPOINT = os.environ.get("WHO_ENDPOINT")

DATA_BUCKET = os.environ.get("DATA_BUCKET")
WHO_DATA_FOLDER = os.environ.get("WHO_DATA_FOLDER")


def setup_logger():
	h = logging.StreamHandler(sys.stdout)
	rootLogger = logging.getLogger()
	rootLogger.addHandler(h)
	rootLogger.setLevel(logging.INFO)


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
		csv_data = csv_io.getvalue()
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
