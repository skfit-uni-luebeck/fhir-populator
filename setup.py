from setuptools import setup

setup(
    name='fhir-populator',
    version='1.0.0',
    packages=['fhir_populator'],
    url='https://github.com/itcr-uni-luebeck/fhir-populator',
    license='BSD',
    author='Joshua Wiedekopf',
    author_email='joshua.wiedekopf@uni-luebeck.de',
    description='Load a Simplifier package into a FHIR server, quickly.',
    install_requires=[
        "requests",
        "rich",
        "inquirer",
        "networkx"
    ]
)
