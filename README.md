# FHIR Populator

[![PyPI version](https://badge.fury.io/py/fhir-populator.svg)](https://badge.fury.io/py/fhir-populator)

A tool to load a lot of FHIR resources into a "naked" FHIR server.

It is intended to quickly load a package of FHIR Profiles (`StructureDefinitions`) and associated artefacts (such as `CodeSystem`, `ValueSet`, `ConceptMap`) into a FHIR server that has just been spun up.

This tool was developed in the context of the [Core Dataset (KDS) of the Medical Informatics Initiative (MII) in Germany](https://simplifier.net/organization/koordinationsstellemii/~projects) as well as the [German Corona Consensus Dataset (GECCO) developed by the Network University Medicine](https://simplifier.net/ForschungsnetzCovid-19).

The script is written in Python 3 and [available on PyPI](https://pypi.org/project/fhir-populator/).

## Installation

As the package is available on the Python package index, you can install it quickly into a Virtual Environment. First, you may need to create a folder for FHIR populator and a Virtual Environment (all commands are for Unix-based OS and may need tweaking on Windows):

```bash
mkdir fhir-populator
cd fhir-populator
python -m venv .venv
source .venv/bin/activate
```

On Windows without Windows Subsystem for Linux, you will need to change the last command to `.venv\bin\activate.bat`.

These commands will create a new directory, visit it, create the virtual environment, and activate it.

Next, load the package from PyPI:

```bash
python -m pip install fhir-populator
```

You can now start it as a Python module:

```bash
python -m fhir_populator --help
```

and the help will be printed:

```
usage: fhir_populator [-h] --endpoint ENDPOINT [--authorization-header AUTHORIZATION_HEADER] [--log-file LOG_FILE]
                      [--get-dependencies] [--non-interactive] [--include-examples]
                      [--log-level {INFO,WARNING,DEBUG,ERROR}] [--rewrite-versions] [--only-put] [--versioned-ids]
                      [--exclude-resource-type [EXCLUDE_RESOURCE_TYPE ...] | --only [ONLY ...]]
                      [--registry-url REGISTRY_URL] [--package PACKAGES [PACKAGES ...]]
                      [--persist] [--from-persistence] [--persistence-dir PERSISTANCE_DIR]

optional arguments:
  -h, --help            show this help message and exit
  --endpoint ENDPOINT   The FHIR server REST endpoint (default: None)
  --authorization-header AUTHORIZATION_HEADER
                        an authorization header to use for uploading. If none, nothing will be sent. (default: None)
  --log-file LOG_FILE   A log file path (default: None)
  --get-dependencies    if provided, dependencies will be retrieved from the FHIR registry. (default: False)
  --non-interactive     In case of errors returned by this FHIR server, the error will be ignored with only a log
                        message being written out. Might be helpful when integrating this module into a script.
                        (default: False)
  --include-examples    If provided, the resources in the 'examples' folder of the packages will be uploaded.
                        (default: False)
  --log-level {INFO,WARNING,DEBUG,ERROR}
                        The level to log at (default: INFO)
  --rewrite-versions    If provided, all versions of FHIR resources will be modified to be consistent with the
                        package version. Otherwise, the version is used as-is! (default: False)
  --only-put            if provided, IDs will be generated for all resources that lack one. This can be combined with
                        --versioned-ids. (default: False)
  --versioned-ids       if provided, all resource IDs will be prefixed with the package version. (default: False)
  --exclude-resource-type [EXCLUDE_RESOURCE_TYPE ...]
                        Specify resource types to ignore! (default: None)
  --only [ONLY ...]     Only upload the resource types provided here, e.g. only StructureDefinitions, CodeSystems and
                        ValueSets (default: None)
  --registry-url REGISTRY_URL
                        The FHIR registry url, Simplifier by default (default: https://packages.simplifier.net)
  --package PACKAGES [PACKAGES ...]
                        Specification for the package to download and push to the FHIR server. You can specify more
                        than one package. Use the syntax 'package@version', or leave out the version to use the
                        latest package available on the registry. (default: None)
  --persist          if provided the package will be persisted in the persist-dir
  --persistence-dir      directory where the persisted packages will be stored or loaded from
  --from-persistence     if provided the package will be loaded from the persistence-dir                      
```

There are a lot of command line options that can be used to customize the behaviour of the program.

## Example Invocation

To try out the program, you can spin up a FHIR server, such as [HAPI FHIR JPA Server Starter](https://github.com/hapifhir/hapi-fhir-jpaserver-starter) on your local machine, e.g. using Docker. Assuming the endpoint of the server is http://localhost:8080/fhir, you can upload the latest version of the GECCO package, including dependencies (e.g. the MII KDS modules used by that package), thus:

```bash
python -m fhir_populator --endpoint http://localhost:8080/fhir --get-dependencies --package de.gecco
```

As this example does not specify a version of the `de.gecco` package, the latest version of the package will first be determined from the Simplifier API. You can also specify a version using the syntax `package@version`:

```bash
python -m fhir_populator --endpoint http://localhost:8080/fhir --get-dependencies --package de.gecco@1.0.3
```

Also, you can specify as many packages as you like, and mix-and-match versioned references with unversioned ones:

```bash
python -m fhir_populator --endpoint http://localhost:8080/fhir --get-dependencies --package de.gecco@1.0.3 de.medizininformatikinitiative.kerndatensatz.person
```

## Implementation Details

The script is broken into multiple steps:

1. All unversioned package references are converted to versioned references, by retrieving the package metadata from the NPM registry.
2. The packages are downloaded as Tarballs into a temporary directory (under `/tmp` for Unix systems), and extracted there
3. After each package is downloaded, the `package.json` is examined, and dependencies are added to the download queue, if desired. During this download, a dependency graph is built from the downloaded packages, to make sure that every package is uploaded after its dependencies
4. The packages are uploaded, file-by-file, to the FHIR server. This uses the topological sort of the directed dependency graph, to maintain consistency. Also, the files are uploaded in logical versions (e.g. `CodeSystem` before `ValueSet` before `StructureDefinition` before `Patient` etc.)
5. If the FHIR server returns an error, the user is prompted interactively for input.
6. When all resources are uploaded (or if the user aborts execution with *CTRL-C*), the temporary directory is recursively deleted.

## Configuration

There are a number of configuration options, which are (hopefully) mostly self-explanatory. Some of the more obscure ones are explained below:

* `--authorization-header`: USe if your server is configured for Authentication. You can enter something like `--authorization-header "Bearer asdf" here, which will be presented to the server for each request.
* `--exclude-resource-type`: You can skip resource types, e.g. `--exclude-resource-type CodeSystem ValueSet ConceptMap`. This is not case-sensitive, the lower-case version of the resource type will be matched against the lower-case parameter list.
* `--include-examples`: Examples in FHIR packages are great, but often not consistent across packages. For example, an `Observation` example might reference `Patient/example`, and this patient is nowhere to be found in the package, or its dependencies. Some FHIR servers (such as HAPI JPA Server) validate references on CREATE and return errors for missing references. Hence, examples (files in the `examples` folder of the package, as per the spec) are ignored by default.
* `--non-interactive`: If provided, errors returned by the FHIR server will be ignored, and only a warning will be printed out.
* `--only-put`: FHIR requires that IDs are present for all resources that are uploaded via HTTP PUT. Hence, if IDs are missing, an HTTP POST request is used by the script. This does not generate stable, or nice, IDs by default. You can provide this parameter to make the script generate IDs from the file name of the resource, which should be stable across reruns. This uses a "slugified" version of the filename without unsafe characters, and restricted to 64 characters, as per the specification.
* `--registry-url`: While the script was only tested using the Simplifier registry, it should be compatible to other implementations of the [FHIR NPM Package Spec](https://wiki.hl7.org/FHIR_NPM_Package_Spec), which is implemented by the Simplifier software. You can provide the endpoint of an alternative registry hence.
* `--rewrite-versions`: If provided, all `version` attributes of the resources will be rewritten to match the version in the `package.json`, to separate these definitions from previous versions. You will need to think about the versions numbers you use when communicating with others, who might not use the same versions - ⚠️  use with caution! ⚠️
* `--versioned-ids`: To separate versions of the resources on the same FHIR server, you can override the IDs provided in the resources, by including the slugified version of the package in the ID. If combined with the `--only-put` switch, this will work the same, versioning existing IDs, and slugifying + versioning the filename of resources without IDs.
* `--persist`: If provided, the downloaded packages will be persisted in the `--persistence-dir` directory.
* `--persistence-dir`: The directory where the persisted packages will be stored or loaded from.
* `--from-persistence`: If provided, the package will be loaded from the `--persistence-dir` directory.

## Proxy:

* `--http-proxy`: URL of your HTTP proxy, may optionally include credentials (c.f. [the Requests documentation](https://requests.readthedocs.io/en/latest/user/advanced/#proxies))
* `--https-proxy`: URL for HTTPS requests, if not provided, the HTTP proxy is used instead
* `--proxy-for-fhir`: If provided, the proxy is also used for requests to your FHIR server
* `--proxy-verify`: If provided, this public key (-chain) on your disk is used for validating the re-encrypted traffic to your proxy
* `--proxy-for-fhir`: If provided, the proxy is also used for FHIR requests, not only for NPM requests`

## Updating

```bash
cd fhir-populator
source venv/bin/activate
python -m pip install --upgrade fhir-populator
```

## Hacking

If you want to customize the program, you should:

1. create a fork in GitHub, and clone it.
2. create a new virtual environment in your fork: `python -m venv .venv`; `source .venv/bin/activate`
3. Install the package locally, using `pip install .`
4. Customize the script. Re-run step 3 if you change the script.
5. `python -m fhir_populator`, as before.
6. Create an issue and pull request in the GitHub Repo! We welcome contributions!

## Changelog

| Version | Date | Changes |
|-|-|-|
| v1.0.10 | 2021-06-03 | Initial release |
| v1.1.0  | 2021-06-08 | - handle Unicode filenames, especially on BSD/macOS (#1)<br>- do not serialize null ID for POST (#2)<br>- include option for only certain resource types(#6)<br>- fix XML handling (#6)<br>- add LICENSE |
| v1.1.1  | 2021-06-09 | - explicitly open files with UTF-8 encoding (#12)<br>- ignore pycharm and vscode (#11)|
| v1.2.0  | 2022-12-13 | - support HTTP/HTTPS proxies |
| v1.3.0 | 2023-04-03 | - support using a persistence directory |
