import sys
import tempfile
import time
from typing import List, TextIO, Optional, Dict
import json
import os
import argparse
import tarfile
import shutil
import logging
import requests
from rich.logging import RichHandler
import xml.etree.ElementTree as ElementTree
from enum import Enum
import inquirer


class FhirResource:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.type = self.get_filetype()
        self.resource_type = self.get_argument("resourceType", raise_on_missing=True)
        self.id = self.get_argument("id", raise_on_missing=False)
        self.resource_order = self.get_resource_order()

    resource_order = {
        "CodeSystem": 1,
        "ValueSet": 2,
        "ConceptMap": 3,
        "StructureDefinition": 4,
        "Bundle": 100,
        "Patient": 110,
        "Condition": 120,
        "Consent": 120,
        "DiagnosticReport": 120,
        "Immunization": 120,
        "MedicationStatement": 120,
        "Observation": 120,
        "Procedure": 120,
        "ImplementationGuide": 999,

    }

    def get_resource_order(self, default_resource_priority: int = 50) -> int:
        if self.resource_type in self.resource_order:
            return self.resource_order[self.resource_type]
        else:
            return default_resource_priority

    def get_argument(self, argument: str, raise_on_missing: bool = False) -> str:
        if self.type == FhirResource.FileType.XML:
            return self.get_argument_xml(argument, raise_on_missing)
        else:
            return self.get_argument_json(argument, raise_on_missing)

    def get_payload(self, rewrite_version: Optional[str] = None) -> str:
        if rewrite_version is None:
            with open(self.file_path, "r") as fs:
                return fs.read()
        elif self.type == FhirResource.FileType.XML:
            return self.get_payload_rewrite_xml(rewrite_version)
        else:
            return self.get_payload_rewrite_json(rewrite_version)

    class FileType(Enum):
        JSON = 1
        XML = 2

    def get_filetype(self) -> FileType:
        """
        check if this file is XML or JSON
        https://codereview.stackexchange.com/a/137926
        :return: FhirResource.FileType enum member
        """
        with open(self.file_path) as unknown_file:
            c = unknown_file.read(1)
            if c != '<':
                return FhirResource.FileType.JSON
            return FhirResource.FileType.XML

    def __repr__(self):
        return f"FHIR Resource ({self.resource_type}) @ {self.file_path} - {self.resource_type}"

    def get_payload_rewrite_xml(self, rewrite_version: str) -> str:
        tree = ElementTree.parse(self.file_path)
        root = tree.getroot()
        version_node = root.find("version")
        if version_node is not None:
            version_node.text = rewrite_version
        return ElementTree.tostring(root, encoding="unicode")

    def get_payload_rewrite_json(self, rewrite_version: str, indent: int = 2) -> str:
        with open(self.file_path, "r") as jf:
            json_dict = json.load(jf)
        if "version" in json_dict:
            json_dict["version"] = rewrite_version
        return json.dumps(json_dict, indent=indent)

    def get_argument_xml(self, argument: str, raise_on_missing: bool = False):
        tree = ElementTree.parse(self.file_path)
        root = tree.getroot()
        res_node = root.find(argument)
        if res_node is None and raise_on_missing:
            raise LookupError(f"the resource {self.file_path} does not have an attribute {argument}!")
        elif res_node is None:
            return None
        else:
            return res_node.text

    def get_argument_json(self, argument: str, raise_on_missing: bool = False) -> Optional[str]:
        with open(self.file_path) as jf:
            json_dict = json.load(jf)
            if argument not in json_dict and raise_on_missing:
                raise LookupError(f"the resource {self.file_path} does not have an attribute {argument}!")
            elif argument not in json_dict:
                return None
            else:
                return json_dict[argument]


class Populator:

    log: logging.Logger = None
    temp_dir: str = None

    def __init__(self):
        self.args = self.parse_args()
        self.endpoint = self.args.endpoint.rstrip('/')
        self.log = self.configure_logger()
        self.temp_dir = tempfile.mkdtemp(prefix="fhir-populator")
        self.download_dir = os.path.join(self.temp_dir, "download")
        os.mkdir(self.download_dir)
        self.extract_dir = os.path.join(self.temp_dir, "extract")
        os.mkdir(self.extract_dir)
        self.print_args()
        self.request_session = requests.Session()
        if self.args.authorization_header is not None:
            self.request_session.headers.update({
                "Authorization": self.args.authorization_header
            })
            self.log.info(f"Using Authorization header: {self.args.authorization_header[:10]}...")

    def __del__(self):
        if self.log is not None:
            self.log.info("Cleaning up!")
            self.log.info(f"Deleting temp directory: {self.temp_dir}")
        if self.temp_dir is not None:
            shutil.rmtree(self.temp_dir)

    @staticmethod
    def parse_args() -> argparse.Namespace:
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
        parser.add_argument("--get-dependencies", dest="get_dependencies", action="store_true")
        parser.add_argument(
            "--package", nargs="+", type=str,
            help="Specification for the package to download and push to the FHIR server. " +
                 "You can specify more than one package. " +
                 "Use the syntax 'package:version', or leave out the version to use the latest package " +
                 "available on Simplifier."
        )
        parser.add_argument(
            "--rewrite-versions", action="store_true",
            help="If provided, all versions of FHIR resources will be modified to be consistent with the package " +
                 "version. Otherwise, the version is used as-is!"
        )
        parser.add_argument(
            "--exclude-resource-type", type=str, nargs="*",
            help="Specify resource types to ignore!"
        )
        return parser.parse_args()

    def download_packages(self) -> List[str]:
        untar_folders = []
        for package in self.args.package:
            self.log.info(f"Downloading package with spec {package}")
            untar_folders.append(self.download_untar_package(package_name=package))
        return untar_folders

    def download_untar_package(self, package_name: str) -> str:
        if ':' in package_name:
            package_id, package_version = package_name.split(':')
        else:
            package_id = package_name
            package_version = self.get_latest_package_version(package_name)

        request_url = f"https://packages.simplifier.net/{package_id}/{package_version}"
        download_filename = f"{package_id}_{package_version}"
        download_path = os.path.join(self.download_dir, f"{download_filename}.tar")
        extract_path = os.path.join(self.extract_dir, download_filename)
        download_request = requests.Request(
            method="GET",
            url=request_url
        ).prepare()
        download_response = self.request_session.send(download_request, stream=True)
        with open(download_path, "wb") as download_fs:
            for chunk in download_response.iter_content(chunk_size=8192):
                download_fs.write(chunk)
        self.log.info(f"Downloaded to {download_path}")
        with tarfile.open(download_path) as download_tar_fs:
            download_tar_fs.extractall(extract_path)
        self.log.info(f"Extracted to {extract_path}")
        return extract_path

    def get_latest_package_version(self, package_name: str) -> str:
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

    def populate(self) -> None:
        packages = self.download_packages()
        if self.args.get_dependencies:
            packages = self.gather_dependencies(packages)
        self.upload_resources(packages)

    def configure_logger(self) -> logging.Logger:
        handlers = [
            RichHandler()
        ]
        if self.args.log_file is not None:
            handlers.append(logging.FileHandler(self.args.log_file, mode="w"))
        log_format = "%(message)s"
        log_dateformat = "[%X]"
        log_level = "NOTSET"
        # logging.basicConfig(
        #     level=level,
        #     datefmt=log_dateformat,
        #     format=log_format
        # )
        # logger = logging.getLogger("fhir-populator")
        logging.basicConfig(
            level=log_level, format=log_format, datefmt=log_dateformat, handlers=handlers
        )
        return logging.getLogger("rich")

    def print_args(self):
        for arg in vars(self.args):
            self.log.info(f" - {arg} : {getattr(self.args, arg)}")

    def upload_resources(self, packages: List[str]):
        for package_dir in packages:
            self.log.info("Uploading package files from package directory: %s", package_dir)
            fhir_files = []
            package_json = None
            for (directory_path, _, filenames) in os.walk(package_dir):
                for file_name in filenames:
                    if file_name == "package.json":
                        with open(os.path.join(directory_path, file_name)) as jf:
                            package_json = json.load(jf)
                        continue
                    full_path = os.path.join(directory_path, file_name)
                    try:
                        fhir_resource = FhirResource(full_path)
                        if self.args.exclude_resource_type is not None and fhir_resource.resource_type in self.args.exclude_resource_type:
                            self.log.debug(f"Resource {full_path} is of resource type {fhir_resource.resource_type}" +
                                           f" and is skipped.")
                        else:
                            fhir_files.append(FhirResource(full_path))
                    except LookupError:
                        self.log.exception(f"Error reading FHIR resource from package: {file_name}")
            fhir_files = self.sort_fhir_files(fhir_files)
            if package_json is None:
                raise FileNotFoundError(f"package.json was not found within {package_dir}!")
            rewrite_version = None
            package_version = package_json["version"]
            package_name = package_json["name"]
            package_description = package_json["description"]
            package_dependencies : Dict[str, str] = package_json.get("dependencies")
            self.log.info(f"uploading Package: {package_name} (\"{package_description}\"; version {package_version}; ")
            if package_dependencies is not None and len(package_dependencies.keys()) > 0:
                for dep, dep_version in package_dependencies.items():
                    self.log.warning(f"The package has a dependency on: {dep} version {dep_version}")
            if self.args.rewrite_versions:
                rewrite_version = package_version
                self.log.warning(f"rewriting resources in package")
            num_files = len(fhir_files)
            while len(fhir_files) > 0:
                fhir_file = fhir_files.pop(0)
                self.log.info(f"Uploading {fhir_file.file_path} ({fhir_file.resource_type}) ({num_files-len(fhir_files)}/{num_files})")
                upload_url = f"{self.endpoint}/{fhir_file.resource_type}"
                request_method = "POST"
                if fhir_file.id is not None:
                    request_method = "PUT"
                    upload_url += f"/{fhir_file.id}"
                if fhir_file.resource_type == "Bundle":
                    bundle_type = fhir_file.get_argument("type", raise_on_missing=False)
                    if bundle_type == "transaction":
                        upload_url = self.endpoint
                        request_method = "POST"
                content_type = "application/xml" if fhir_file.type == FhirResource.FileType.XML else "application/json"
                payload = fhir_file.get_payload(rewrite_version=rewrite_version).encode("utf-8")
                upload_request = requests.Request(
                    method=request_method,
                    url=upload_url,
                    headers={
                        "Content-Type": content_type
                    },
                    data=payload
                ).prepare()
                self.log.debug(f"uploading to {upload_url} (content type: {content_type})")
                upload_result = self.request_session.send(upload_request)
                if 200 <= upload_result.status_code < 300:
                    self.log.info(f"uploaded {fhir_file.resource_type} with status {upload_result.status_code}")
                else:
                    self.log.error(f"The status code signifies error: {upload_result.status_code}")
                    operation_outcome = upload_result.json()
                    self.log.error(operation_outcome["issue"])
                    choices = [
                        inquirer.List('action',
                                      "What should we do?",
                                      choices=[("Ignore (continue with the next resource)", "Ignore"),
                                               ("Retry (because you have changed/uploaded something else)", "Retry")
                                               ])
                    ]
                    sys.stdout.flush()
                    action = inquirer.prompt(choices)['action']
                    sys.stdout.flush()
                    if action == "Ignore":
                        self.log.warning(
                            "The file is ignored. Proceeding with the next file.")
                    elif action == "Retry":
                        self.log.warning("Trying to upload file again.")
                        fhir_files.insert(0, fhir_file)

    @staticmethod
    def sort_fhir_files(fhir_files: List[FhirResource]):
        def sort_key(x: FhirResource):
            return x.resource_order, x.resource_type
        fhir_files.sort(key=sort_key)
        return fhir_files

    def gather_dependencies(self, packages: List[str]) -> Optional[List[str]]:
        dependencies = {}
        for package_path in packages:
            all_files = []
            for (directory_path, _, filenames) in os.walk(package_path):
                for file_name in filenames:
                    all_files.append(os.path.join(directory_path, file_name))
            package_json_file = [f for f in all_files if "package.json" in f]
            if len(package_json_file) != 1:
                self.log.error(f"Within the package {package_path}, one and only one package.json must be present")
                return None
            with open(package_json_file[0]) as jf:
                package_json = json.load(jf)
                package_dependencies = package_json["dependencies"]

        # sort dependencies

