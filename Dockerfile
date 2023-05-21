FROM python:3.11

COPY . /opt/fhir-populator

WORKDIR /opt/fhir-populator

RUN pip install -e .

ENTRYPOINT ["fhir-populator"]
