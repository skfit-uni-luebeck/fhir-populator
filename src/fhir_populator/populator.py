import argparse
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ElementTree
from enum import Enum
from io import BufferedReader
from typing import List, Optional, Dict

import inquirer
import networkx as nx
import requests
from rich.logging import RichHandler
from slugify import slugify


class FhirResource:
    def __init__(self,
                 file_path: str,
                 package_version: str,
                 generate_missing_ids: bool,
                 versioned_ids: bool):
        self.file_path = file_path
        self.type = self.get_filetype()
        self.resource_type = self.get_argument("resourceType", raise_on_missing=True)
        new_id = self.get_id(package_version, generate_missing_ids, versioned_ids)
        self.id = new_id
        self.resource_order = self.get_resource_order()

    resource_order_dict = {
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
        if self.resource_type in self.resource_order_dict:
            return self.resource_order_dict[self.resource_type]
        else:
            return default_resource_priority

    def get_argument(self, argument: str, raise_on_missing: bool = False) -> str:
        if self.type == FhirResource.FileType.XML:
            return self.get_argument_xml(argument, raise_on_missing)
        else:
            return self.get_argument_json(argument, raise_on_missing)

    def get_payload(self, rewrite_version: Optional[str] = None) -> str:
        # if rewrite_version is None:
        #     with open(self.file_path, "r", encoding="utf8") as fs:
        #         return fs.read()
        if self.type == FhirResource.FileType.XML:
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
        with open(self.file_path, encoding="utf8") as unknown_file:
            c = unknown_file.read(1)
            if c != '<':
                return FhirResource.FileType.JSON
            return FhirResource.FileType.XML

    def __repr__(self):
        return f"FHIR Resource ({self.resource_type}) @ {self.file_path} - {self.resource_type}"

    def get_payload_rewrite_xml(self, rewrite_version: Optional[str]) -> str:
        tree = ElementTree.parse(self.file_path)
        root = tree.getroot()
        if rewrite_version is not None:
            version_node = root.find("version")
            if version_node is not None:
                version_node.text = rewrite_version
        if self.id is not None:
            id_node = root.find("id")
            id_node.text = self.id
        return ElementTree.tostring(root, encoding="unicode")

    def get_payload_rewrite_json(self, rewrite_version: Optional[str], indent: int = 2) -> str:
        with open(self.file_path, "r", encoding="utf8") as jf:
            json_dict = json.load(jf)
        if rewrite_version is not None:
            if "version" in json_dict:
                json_dict["version"] = rewrite_version
        if self.id is not None:
            json_dict["id"] = self.id
        return json.dumps(json_dict, indent=indent)

    def get_argument_xml(self, argument: str, raise_on_missing: bool = False):
        tree = ElementTree.parse(self.file_path)
        root = tree.getroot()
        if argument == "resourceType":
            # resource type is provided as the name of the tag, instead of as an attribute
            tag = root.tag
            if "{" in tag:
                return tag.split("}")[1]  # Tag name without namespace
            else:
                return tag  # Tag does not seem to contain a namespace
        res_node = root.find(argument)
        if res_node is None and raise_on_missing:
            raise LookupError(f"the resource {self.file_path} does not have an attribute {argument}!")
        elif res_node is None:
            return None
        else:
            return res_node.text

    def get_argument_json(self, argument: str, raise_on_missing: bool = False) -> Optional[str]:
        with open(self.file_path, encoding="utf8") as jf:
            json_dict = json.load(jf)
            if argument not in json_dict and raise_on_missing:
                raise LookupError(f"the resource {self.file_path} does not have an attribute {argument}!")
            elif argument not in json_dict:
                return None
            else:
                return json_dict[argument]

    def get_id(self, package_version, generate_missing_ids, versioned_ids) -> Optional[str]:
        resource_id = self.get_argument("id", raise_on_missing=False)
        if resource_id is None and not generate_missing_ids:
            return None
        filename_no_ext = os.path.splitext(os.path.basename(self.file_path))[0]
        slug_version = slugify(package_version)
        max_length_versioned = 64 - len(slug_version) - 2
        generated_id = slugify(filename_no_ext, max_length=64 - len(slug_version) - 2)
        if resource_id is None:
            resource_id = generated_id
        if versioned_ids:
            return f"{resource_id[:max_length_versioned]}--{slug_version}"
        else:
            return resource_id[:64]


class PopulatorSettings:
    endpoint: str
    authorization_header: Optional[str]
    log_file: Optional[str]
    get_dependencies: bool
    non_interactive: bool
    packages: List[str]
    include_examples: bool
    rewrite_versions: bool
    exclude_resource_type: List[str]
    log_level: str
    only_put: bool
    versioned_ids: bool
    registry_url: str
    only: List[str]
    log: logging.Logger

    def __init__(self, args: argparse.Namespace, log: logging.Logger):
        self.registry_url = args.registry_url.rstrip("/")
        self.endpoint = args.endpoint.rstrip("/")
        self.authorization_header = args.authorization_header
        self.log_file = args.log_file
        self.get_dependencies = args.get_dependencies
        self.non_interactive = args.non_interactive
        self.packages = args.packages
        self.include_examples = args.include_examples
        self.rewrite_versions = args.rewrite_versions
        self.log_level = args.log_level
        self.exclude_resource_type = [a.lower() for a in args.exclude_resource_type] \
            if args.exclude_resource_type is not None \
            else []
        self.only = [a.lower() for a in args.only] \
            if args.only is not None \
            else []
        self.only_put = args.only_put
        self.versioned_ids = args.versioned_ids
        self.log = log
        self.print_args(args)

    def print_args(self, args: argparse.Namespace):
        for arg in vars(args):
            self.log.info(f" - {arg} : {getattr(args, arg)}")


class Populator:
    log: logging.Logger = None
    temp_dir: str = None
    settings: PopulatorSettings = None

    ignored_dependencies = [
        "hl7.fhir.r4",
    ]

    def __init__(self):
        args = self.parse_args()
        self.log = self.configure_logger(args)
        self.args = PopulatorSettings(args, self.log)
        self.temp_dir = tempfile.mkdtemp(prefix="fhir-populator")
        self.download_dir = os.path.join(self.temp_dir, "download")
        os.mkdir(self.download_dir)
        self.extract_dir = os.path.join(self.temp_dir, "extract")
        os.mkdir(self.extract_dir)
        self.request_session = requests.Session()
        if self.args.authorization_header is not None:
            self.request_session.headers.update({
                "Authorization": self.args.authorization_header
            })
            self.log.debug(f"Using Authorization header: {self.args.authorization_header[:10]}...")

    def __del__(self):
        if self.log is not None:
            self.log.debug("Cleaning up!")
            self.log.debug(f"Deleting temp directory: {self.temp_dir}")
        if self.temp_dir is not None:
            shutil.rmtree(self.temp_dir)

    @staticmethod
    def parse_args() -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog="fhir_populator",
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
        parser.add_argument(
            "--endpoint", required=True, type=str,
            help="The FHIR server REST endpoint"
        )
        parser.add_argument(
            "--authorization-header", type=str,
            help="an authorization header to use for uploading. If none, nothing will be sent.")
        parser.add_argument(
            "--log-file", type=str, help="A log file path")
        parser.add_argument(
            "--get-dependencies", dest="get_dependencies", action="store_true",
            help="if provided, dependencies will be retrieved from the FHIR registry.")
        parser.add_argument(
            "--non-interactive", action="store_true",
            help="In case of errors returned by this FHIR server, the error will be ignored with only a log message " +
                 "being written out. Might be helpful when integrating this module into a script."
        )
        parser.add_argument(
            "--include-examples", action="store_true",
            help="If provided, the resources in the 'examples' " +
                 "folder of the packages will be uploaded.")
        parser.add_argument(
            "--log-level", choices=["INFO", "WARNING", "DEBUG", "ERROR"], default="INFO",
            help="The level to log at")
        parser.add_argument(
            "--rewrite-versions", action="store_true",
            help="If provided, all versions of FHIR resources will be modified to be consistent with the package " +
                 "version. Otherwise, the version is used as-is!"
        )
        parser.add_argument(
            "--only-put", action="store_true",
            help="if provided, IDs will be generated for all resources that lack one. This can be combined with " +
                 "--versioned-ids."
        )
        parser.add_argument(
            "--versioned-ids", action="store_true",
            help="if provided, all resource IDs will be prefixed with the package version."
        )
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--exclude-resource-type", type=str, nargs="*",
            help="Specify resource types to ignore!"
        )
        group.add_argument(
            "--only", type=str, nargs="*",
            help="Only upload the resource types provided here, " +
                 "e.g. only StructureDefinitions, CodeSystems and ValueSets"
        )
        parser.add_argument(
            "--registry-url", type=str, default="https://packages.simplifier.net",
            help="The FHIR registry url, Simplifier by default"
        )
        parser.add_argument(
            "--package", nargs="+", type=str, dest="packages",
            help="Specification for the package to download and push to the FHIR server. " +
                 "You can specify more than one package. " +
                 "Use the syntax 'package@version', or leave out the version to use the latest package " +
                 "available on the registry."
        )
        return parser.parse_args()

    def download_packages(self, packages: List[str]) -> nx.DiGraph:
        untar_folders = []
        get_deps = self.args.get_dependencies
        dependency_graph = nx.DiGraph()
        packages_to_download = list(packages)
        downloaded_packages = list()
        while len(packages_to_download) > 0:
            package = packages_to_download.pop(0)
            if package in downloaded_packages:
                self.log.debug(f"Package {package} is already downloaded.")
                continue
            self.log.info(f"Downloading package with spec {package}")
            package_path = self.download_untar_package(package_name=package)
            untar_folders.append(package_path)
            dependency_graph.add_node(package, path=package_path)
            if get_deps:
                dependencies = self.gather_dependencies(package_path)
                for dep in dependencies:
                    ignored = [ign for ign in self.ignored_dependencies if dep.startswith(ign)]
                    if not any(ignored):
                        dependency_graph.add_edge(dep, package)
                        packages_to_download.append(dep)
                downloaded_packages.append(package)
        self.log.debug("Packages downloaded with dependencies:")
        for node in dependency_graph.nodes:
            self.log.debug(f" - {node}")
        return dependency_graph

    def download_untar_package(self, package_name: str) -> str:
        """
        download a package from the registry
        :param package_name:
        :return:
        """
        if '@' in package_name:
            package_id, package_version = package_name.split('@')
        else:
            raise ValueError(f"No version specified in argument {package_name}")

        request_url = f"{self.args.registry_url}/{package_id}/{package_version}"
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
        self.log.debug(f"Downloaded to {download_path}")
        try:
            with tarfile.open(download_path) as download_tar_fs:
                for tarinfo in download_tar_fs:
                    try:
                        extract_dir = os.path.dirname(tarinfo.path)
                        t_filename, t_ext = os.path.splitext(os.path.basename(tarinfo.path))
                        slug_filename = slugify(t_filename)
                        extract_filename = f"{slug_filename}{t_ext}"
                        extract_to_folder = os.path.join(extract_path, extract_dir)
                        os.makedirs(extract_to_folder, exist_ok=True)
                        extract_to = os.path.join(extract_to_folder, extract_filename)
                        with open(extract_to, "wb") as out_fp:
                            tar_br: BufferedReader
                            with download_tar_fs.extractfile(tarinfo) as tar_br:
                                out_fp.write(tar_br.read())
                        self.log.debug(f"Extracted {extract_to}")
                    except (tarfile.TarError, IOError, OSError):
                        logging.exception(f"Unhandled error extracting member '{tarinfo}' from {download_path}." +
                                          "Extraction will continue.")
                        continue
        except (tarfile.TarError, IOError, OSError):
            logging.exception(f"Unhandled error extracting archive {download_path}")
            exit(1)
        self.log.debug(f"Extracted to {extract_path}")
        return extract_path

    def get_latest_package_version(self, package_name: str) -> str:
        lookup_url = f"{self.args.registry_url}/{package_name}"
        lookup_request = requests.Request(
            method="GET",
            url=lookup_url
        ).prepare()
        response = self.request_session.send(lookup_request)
        versions = [v["version"] for v in response.json()["versions"].values()]
        self.log.debug(f"Available versions for '{package_name}': {versions}")
        last_version = versions[-1]
        self.log.debug(f"Latest version: {last_version}")
        return last_version

    def populate(self) -> None:
        packages = self.resolve_package_versions()
        dependency_graph = self.download_packages(packages)
        self.upload_resources(dependency_graph)
        self.log.info("UPLOAD COMPLETE")

    # noinspection PyArgumentList
    @staticmethod
    def configure_logger(args: argparse.Namespace) -> logging.Logger:
        """
        configure the application logger. This may log to a file (if provided), and configures the log level
        :param args: the (raw) argparse arguments
        :return: the configured Rich logger
        """
        handlers = [
            RichHandler()
        ]
        if args.log_file is not None:
            handlers.append(logging.FileHandler(args.log_file, mode="w"))
        log_format = "%(message)s"
        log_dateformat = "[%X]"
        log_level = args.log_level
        logging.basicConfig(
            level=log_level, format=log_format, datefmt=log_dateformat, handlers=handlers
        )
        return logging.getLogger("rich")

    def upload_resources(self, dependency_graph: nx.DiGraph):
        """
        upload the resources represented by the downloaded packages within the dependency graph.
        This will walk the dependency graph from top to bottom, and upload the files within each package in semantic
        order to minimize missing references.
        When an ID is present in the resource, this will use PUT, otherwise POST, unless args.only_put == TRUE.
        In the latter case, the filename of the resource will be hashed to generate a unique ID that is somewhat stable
        across repetitive runs of the app.
        :param dependency_graph: the dependency graph. If no dependencies are to be fetched, this will only contain the
        nodes of the provided packages, and still work
        :return: None
        """
        ordered_dependencies = nx.topological_sort(dependency_graph)
        # order the resources in topological order: every node before its dependencies
        for package_node in ordered_dependencies:
            node_with_info = dependency_graph.nodes[package_node]
            # topological sort only returns the node name as str
            package_dir = node_with_info["path"]
            self.log.debug("Uploading package '%s' files from package directory: %s", package_node, package_dir)
            self.log.debug("Uploading package '%s' files from package directory: %s", package_node, package_dir)
            fhir_files = []
            package_json = self.read_package_json(package_dir)
            package_version = package_json["version"]
            if package_json is None:
                raise FileNotFoundError(f"package.json was not found within {package_dir}!")
            for (directory_path, _, filenames) in os.walk(package_dir):
                file_name: str
                for file_name in filenames:
                    if os.path.basename(directory_path) == "other":  # other directory SHALL be ignored
                        # https://wiki.hl7.org/FHIR_NPM_Package_Spec#Format
                        continue
                    if file_name == "package.json" or file_name == "index.json":
                        continue
                    elif file_name.endswith(".sch"):  # FHIR Shorthand
                        continue
                    full_path = os.path.join(directory_path, file_name)
                    encoded_path = full_path.encode('utf-8', 'surrogateescape').decode('utf-8', 'replace')
                    if os.path.basename(os.path.dirname(encoded_path)) == "examples" and not self.args.include_examples:
                        self.log.debug(f"file at {encoded_path} is an example and ignored.")
                        continue
                    # noinspection PyBroadException
                    try:
                        fhir_resource = FhirResource(encoded_path, package_version, self.args.only_put,
                                                     self.args.versioned_ids)
                        r_type = fhir_resource.resource_type.lower()
                        if (r_type in self.args.exclude_resource_type) or (
                                len(self.args.only) != 0 and r_type not in self.args.only):
                            self.log.debug(
                                f"Resource {encoded_path} is of resource type {r_type}" +
                                f" and is skipped.")
                            continue
                        else:
                            fhir_files.append(fhir_resource)
                    except (LookupError, json.decoder.JSONDecodeError):
                        self.log.error(f"Error reading FHIR resource as JSON: {file_name}")
                    except Exception:
                        self.log.exception(f"Unhandled error reading FHIR resource from package: {file_name}")
            fhir_files = self.sort_fhir_files(fhir_files)
            rewrite_version = None
            package_version = package_json["version"]
            package_name = package_json["name"]
            package_description = package_json["description"]
            package_dependencies: Dict[str, str] = package_json.get("dependencies")
            self.log.info(f"uploading package: {package_name} (\"{package_description}\"; version {package_version})")
            if not self.args.get_dependencies and package_dependencies is not None and len(
                    package_dependencies.keys()) > 0 and not self.args.get_dependencies:
                for dep, dep_version in package_dependencies.items():
                    self.log.warning(f"The package {package_node} has a dependency on: {dep} version {dep_version}")
            if self.args.rewrite_versions:
                rewrite_version = package_version
                self.log.warning(f"rewriting resources in package {package_node}")
            num_files = len(fhir_files)
            while len(fhir_files) > 0:
                fhir_file = fhir_files.pop(0)
                encoded_path = fhir_file.file_path.encode('utf-8', 'surrogateescape').decode('utf-8', 'replace')
                self.log.info(
                    f"Uploading {encoded_path} ({fhir_file.resource_type}) ({num_files - len(fhir_files)}/{num_files})")
                upload_url = f"{self.args.endpoint}/{fhir_file.resource_type}"
                request_method = "POST"
                if fhir_file.id is not None:
                    request_method = "PUT"
                    upload_url += f"/{fhir_file.id}"
                if fhir_file.resource_type == "Bundle":
                    bundle_type = fhir_file.get_argument("type", raise_on_missing=False)
                    if bundle_type == "transaction":
                        upload_url = self.args.endpoint
                        request_method = "POST"
                content_type = "application/xml" if fhir_file.type == FhirResource.FileType.XML else "application/json"
                payload = fhir_file.get_payload(rewrite_version=rewrite_version).encode("utf-8")
                upload_request = requests.Request(
                    method=request_method,
                    url=upload_url,
                    headers={
                        "Content-Type": content_type,
                        "Accept": "application/json"
                    },
                    data=payload
                ).prepare()
                self.log.info(f"uploading to {upload_url} (content type: {content_type})")
                upload_result = self.request_session.send(upload_request)
                if 200 <= upload_result.status_code < 300:
                    self.log.debug(f"uploaded {fhir_file.resource_type} with status {upload_result.status_code}")
                else:
                    self.log.error(f"Error status code {upload_result.status_code} for {fhir_file.file_path} " +
                                   f"({fhir_file.resource_type})")
                    operation_outcome = upload_result.json()
                    self.log.error(operation_outcome["issue"])
                    if self.args.non_interactive:
                        action = "Ignore"
                    else:
                        choices = [
                            inquirer.List('action',
                                          f"What should we do? (current package: {package_node}):",
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
        """
        sort the FHIR files provided in a logical order for uploading to the server.
        FhirResource has a dict that assigns priorities to each resource type, e.g. CodeSystems before ValueSets
        In this way,  dependency errors on other resources is (hopefully) minimized
        :param fhir_files: the fhir file list
        :return: the sorted fhir file list
        """

        def sort_key(x: FhirResource):
            return x.resource_order, x.resource_type

        fhir_files.sort(key=sort_key)
        return fhir_files

    def read_package_json(self, package_path: str) -> Optional[dict]:
        all_files = []
        for (directory_path, _, filenames) in os.walk(package_path):
            for file_name in filenames:
                all_files.append(os.path.join(directory_path, file_name))
        package_json_file = [f for f in all_files if "package.json" in f]
        if len(package_json_file) != 1:
            self.log.error(f"Within the package {package_path}, one and only one package.json must be present")
            return None
        with open(package_json_file[0], encoding="utf8") as jf:
            package_json = json.load(jf)
        return package_json

    def gather_dependencies(self, package_path: str) -> Optional[List[str]]:
        """
        read the package.json of the provided package path, and return the list of dependencies
        :param package_path: the path of the extracted package
        :return: the list of dependencies, in the syntax required for downloading
        """
        dependencies = []
        package_json = self.read_package_json(package_path)
        if "dependencies" in package_json:
            package_dependencies = package_json["dependencies"]
        else:
            package_dependencies = {}
        for package_name, package_version in package_dependencies.items():
            dependencies.append(f"{package_name}@{package_version}")
        return dependencies

    def resolve_package_versions(self) -> List[str]:
        """
        resolve the latest version of the package notations provided in args.packages
        If no "@" character is present, this will query the registry API to find the latest version specifier
        :return: the list of resolved packages
        """
        packages = self.args.packages
        resolved_packages = []
        for package in packages:
            if "@" in package:
                resolved_packages.append(package)
            else:
                latest_version = self.get_latest_package_version(package)
                resolved_packages.append(f"{package}@{latest_version}")
        return resolved_packages
