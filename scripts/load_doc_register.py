# from __future__ import print_function
import json
import requests
import sys
import urllib.request
from typing import List, Any
import argparse
import httpx
import rdflib
import os.path
from rdflib import Graph
from rdflib.namespace import DC, DCTERMS, SKOS, OWL, RDF, RDFS, XSD, DCAT
from pyld import jsonld
import jq
from jsonpath_ng.ext import parse as jsonpathparse
# load the document register as JSON via a JSON-LD conversion

DOCSURL = "https://docs.google.com/spreadsheets/d/"
# SAMPLE_RANGE_NAME = 'Class Data!A1:D'


def init_graph() -> Graph:
    g = rdflib.Graph()
    g.bind("xsd", XSD)
    g.bind("dct", DCTERMS)
    g.bind("skos", SKOS)
    g.bind("owl", OWL)
    g.bind("rdf", RDF)
    g.bind("rdfs", RDFS)
    g.bind("dcat", DCAT)
    g.bind("iso", 'http://iso.org/tc211/')
    g.bind("spec", "http://www.opengis.net/def/ont/modspec/")
    g.bind("specrel", "http://www.opengis.net/def/ont/specrel/")
    g.bind("na", "http://www.opengis.net/def/metamodel/ogc-na/")
    g.bind("prov", "http://www.w3.org/ns/prov#")
    return g


def process_input(data, contextfn):
    # Load YAML context file
    from yaml import load
    try:
        from yaml import CLoader as Loader
    except ImportError:
        from yaml import Loader
    with open(contextfn, 'r') as f:
        context = load(f, Loader=Loader)

    # Check if pre-transform necessary
    transform = context.get('transform')
    if transform:
        data = json.loads(jq.compile(transform).input(data).text())

    # Add contexts
    context_list = context.get('context', {})
    global_context = None
    for loc, val in context_list.items():
        if not loc or loc in ['.', '$']:
            global_context = val
        else:
            items = jsonpathparse(loc).find(data)
            for item in items:
                item.value['@context'] = val

    if global_context:
        data = {
            '@context': global_context,
            '@graph': data,
        }

    return data


def main(inputfn, outputfn, contextfn, base=None):

    g = init_graph()

    jsonld.set_document_loader(jsonld.requests_document_loader(timeout=5000))
    with open(inputfn, 'r') as j:
        inputdata = json.load(j)

    jdocld = process_input(inputdata, contextfn)
    with open(f'{outputfn}.jsonld', 'w') as f:
        json.dump(jdocld, f, indent=2)

    options = {}
    if base:
        options['base'] = base
    output = json.dumps(jsonld.expand(jdocld, options))
    g.parse(data=output, format='json-ld')

    formatted_ttl: str = str(g.serialize(format="turtle"))
    with open(outputfn, 'w') as fout_ttl:
        fout_ttl.write(formatted_ttl)
        fout_ttl.write('\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-i",
        "--input",
        help="Source file (instead of service)",
        default="../incubation/bibliography/test.jsonld"
    )

    parser.add_argument(
        "-o",
        "--output",
        help="Output JSON-LD filename",
        default='docs.ttl'
    )

    parser.add_argument(
        '-c',
        '--context',
        help='YAML context file',
        default='../incubation/bibliography/context.yml'
    )

    parser.add_argument(
        '-b',
        '--base',
        help='Base URI for JSON-LD',
        default='http://example.org/vocab#'
    )

    args = parser.parse_args()

    main(args.input, args.output, args.context, args.base)
