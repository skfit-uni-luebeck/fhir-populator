import tempfile
from typing import List, TextIO
import json
import os
import argparse
import tarfile
import logging

import requests
from rich.logging import RichHandler


class Populator:
    def __init__(self):
        self.args = self.parse_args()
        self.log = self.configure_logger()
        self.temp_dir = tempfile.mkdtemp(prefix="fhir-populator")
        self.download_dir = os.path.join(self.temp_dir, "download")
        os.mkdir(self.download_dir)
        self.extract_dir = os.path.join(self.temp_dir, "extract")
        os.mkdir(self.extract_dir)
        self.print_args()
        self.request_session = requests.Session()

    def parse_args(self):
        parser = argparse.ArgumentParser(
            prog="fhir_populator",
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
        parser.add_argument(
            "--endpoint", help="The FHIR server REST endpoint", required=True, type=str
        )
        parser.add_argument("--authorization-header", type=str,
                            help="an authorization header to use for uploading. If none, nothing will be sent.")
        parser.add_argument("--log-file", type=str,
                            help="A log file path")
        parser.add_argument(
            "package", nargs="+", type=str,
            help="Specification for the package to download and push to the FHIR server. " +
                 "You can specify more than one package. " +
                 "Use the syntax 'package:version', or leave out the version to use the latest package " +
                 "available on Simplifier."
        )
        return parser.parse_args()

    def download_packages(self):
        for package in self.args.package:
            self.log.info(f"Downloading package with spec {package}")
            self.download_untar_package(package_name=package)

    def download_untar_package(self, package_name: str):
        if ':' in package_name:
            package_id, package_version = package_name.split(':')
        else:
            package_id = package_name
            package_version = self.get_latest_package_version(package_name)

        request_url = f"https://packages.simplifier.net/{package_id}/{package_version}"
        download_request = requests.Request(
            method="GET",
            url=request_url
        ).prepare()
        #TODO

    def get_latest_package_version(self, package_name):
        lookup_url = f"https://packages.simplifier.net/{package_name}"
        lookup_request = requests.Request(
            method="GET",
            url=lookup_url
        ).prepare()
        response = self.request_session.send(lookup_request)
        versions = [v["version"] for v in response.json()["versions"].values()]
        self.log.info(f"Available versions for '{package_name}': {versions}")
        last_version = versions[-1]
        self.log.info(f"Latest version: {last_version}")
        return last_version

    def populate(self):
        packages = self.download_packages()

    def configure_logger(self):
        handlers = [
            RichHandler()
        ]
        if self.args.log_file is not None:
            handlers.append(logging.FileHandler(self.args.log_file, mode="w"))
        format = "%(message)s"
        datefmt = "[%X]"
        level = "NOTSET"
        logging.basicConfig(
            level=level,
            datefmt=datefmt,
            format=format
        )
        logger = logging.getLogger("fhir-populator")
        logger.handlers = handlers
        return logger

    def print_args(self):
        for arg in vars(self.args):
            self.log.info(f" - {arg} : {getattr(self.args, arg)}")


